from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent


def _create_variant(
    client: TestClient,
    sku: str,
    label: str,
    price_cents: int,
    qty: int,
) -> dict:
    response = client.post(
        "/api/admin/products",
        json={
            "sku": sku,
            "title": f"变体耳机 {label}",
            "category": "耳机",
            "price_cents": price_cents,
            "variant_group": "VARIANT-EAR",
            "variant_label": label,
            "initial_qty": qty,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_variant_group_navigation_and_search(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    black = _create_variant(api_client, "SKU-VAR-BLK", "曜石黑", 19900, 5)
    white = _create_variant(api_client, "SKU-VAR-WHT", "云雾白", 20900, 3)
    assert black["variant_group"] == "VARIANT-EAR"
    assert black["variant_label"] == "曜石黑"
    assert white["variant_label"] == "云雾白"

    detail = api_client.get("/api/store/products/SKU-VAR-BLK")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["variant_label"] == "曜石黑"
    assert [
        (row["sku"], row["variant_label"], row["price_cents"])
        for row in body["variants"]
    ] == [
        ("SKU-VAR-WHT", "云雾白", 20900),
        ("SKU-VAR-BLK", "曜石黑", 19900),
    ]
    assert {
        row["sku"]: row["available_qty"] for row in body["variants"]
    } == {"SKU-VAR-WHT": 3, "SKU-VAR-BLK": 5}

    search = api_client.get("/api/store/products?q=云雾白").json()
    assert [row["sku"] for row in search] == ["SKU-VAR-WHT"]
    assert search[0]["variant_label"] == "云雾白"

    admin_list = api_client.get("/api/admin/products").json()
    admin_row = next(
        row for row in admin_list if row["sku"] == "SKU-VAR-BLK"
    )
    assert admin_row["variant_group"] == "VARIANT-EAR"
    assert admin_row["variant_label"] == "曜石黑"

    updated = api_client.patch(
        "/api/admin/products/SKU-VAR-WHT",
        json={"variant_label": "珍珠白"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["variant_label"] == "珍珠白"
    db_session.expire_all()
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "product_updated")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert "variant_label" in audit.payload_json["fields"]

    refreshed = api_client.get("/api/store/products/SKU-VAR-BLK").json()
    assert {
        row["sku"]: row["variant_label"] for row in refreshed["variants"]
    } == {"SKU-VAR-WHT": "珍珠白", "SKU-VAR-BLK": "曜石黑"}


def test_variant_group_validation(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    too_long = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-VAR-LONG",
            "title": "超长规格",
            "price_cents": 100,
            "variant_group": "G" * 65,
        },
    )
    assert too_long.status_code == 422
    empty = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-VAR-EMPTY",
            "title": "无规格",
            "price_cents": 100,
        },
    )
    assert empty.status_code == 200, empty.text
    detail = api_client.get("/api/store/products/SKU-VAR-EMPTY").json()
    assert detail["variants"] == []
