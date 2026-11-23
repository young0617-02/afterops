from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Inventory, Product


def _upload(client: TestClient, content: str):
    return client.post(
        "/api/admin/products/import",
        files={
            "file": (
                "products.csv",
                content.encode("utf-8-sig"),
                "text/csv",
            )
        },
    )


def test_import_products_creates_products_and_inventory(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    content = (
        "sku,title,category,brand,price,cost,variant_group,variant_label,"
        "initial_qty,warranty_months,status,support_7d_no_reason\n"
        "sku-imp-001,批量耳机,耳机,声谷,199.90,120.50,IMP-EAR,黑色,7,24,active,true\n"
        "SKU-IMP-002,批量键盘,,,59,,,,0,12,draft,false\n"
    )
    response = _upload(api_client, content)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "created": 2,
        "skus": ["SKU-IMP-001", "SKU-IMP-002"],
    }

    first = api_client.get("/api/admin/products/SKU-IMP-001")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["title"] == "批量耳机"
    assert body["price_cents"] == 19990
    assert body["cost_cents"] == 12050
    assert body["variant_group"] == "IMP-EAR"
    assert body["variant_label"] == "黑色"
    assert body["warranty_months"] == 24
    assert body["available_qty"] == 7
    assert body["support_7d_no_reason"] is True

    storefront = api_client.get("/api/store/products/SKU-IMP-001")
    assert storefront.status_code == 200, storefront.text
    assert storefront.json()["available_qty"] == 7

    inventory = db_session.execute(
        select(Inventory)
        .join(Product, Product.id == Inventory.product_id)
        .where(Product.sku == "SKU-IMP-001")
    ).scalar_one()
    assert inventory.qty_available == 7

    audits = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "products_imported")
        .order_by(AuditEvent.id.desc())
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload_json["count"] == 2


def test_import_products_is_all_or_nothing_and_validates_rows(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    invalid = _upload(
        api_client,
        "sku,title,price,initial_qty\n"
        "SKU-BAD-001,合法商品,10,1\n"
        "SKU-BAD-002,坏价格,-1,1\n",
    )
    assert invalid.status_code == 422
    assert "第 3 行" in invalid.json()["detail"]
    assert (
        db_session.execute(
            select(Product).where(Product.sku.in_(["SKU-BAD-001", "SKU-BAD-002"]))
        ).scalars().all()
        == []
    )

    duplicate = _upload(
        api_client,
        "sku,title,price\nSKU-DUP-001,一,10\nSKU-DUP-001,二,10\n",
    )
    assert duplicate.status_code == 422
    assert "文件内 SKU 重复" in duplicate.json()["detail"]

    existing = _upload(
        api_client,
        "sku,title,price\nSKU-EAR-001,重复商品,10\n",
    )
    assert existing.status_code == 409
    assert "SKU 已存在" in existing.json()["detail"]

    missing_header = _upload(api_client, "sku,title\nSKU-NO-PRICE,商品\n")
    assert missing_header.status_code == 422
    assert "缺少列" in missing_header.json()["detail"]

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = _upload(api_client, "sku,title,price\nSKU-NO-001,商品,10\n")
    assert forbidden.status_code == 403