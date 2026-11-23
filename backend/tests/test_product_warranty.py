from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Order, Shipment

ADDRESS = {
    "recipient_name": "保修测试顾客",
    "recipient_phone": "13800000009",
    "region": "华东",
    "city": "上海",
    "detail": "保修测试路 9 号",
}


def _paid_order(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code
        == 200
    )
    return body["order_no"]


def _delivery(client: TestClient, order_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-WAR-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 200, created.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        assert (
            client.post(
                f"/api/admin/orders/{order_no}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )


def _repair(client: TestClient, order_no: str):
    return client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "保修期测试",
        },
    )


def _eligibility(client: TestClient, order_no: str):
    return client.get(
        f"/api/store/orders/{order_no}/repair-eligibility"
    )


def _set_delivered_at(db_session, order_no: str, value: datetime) -> None:
    order_id = db_session.execute(
        select(Order.id).where(Order.order_no == order_no)
    ).scalar_one()
    shipment = db_session.execute(
        select(Shipment).where(Shipment.order_id == order_id)
    ).scalar_one()
    shipment.delivered_at = value
    db_session.commit()


def test_warranty_is_snapshotted_at_checkout(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "warranty-snapshot")
    admin_login()
    _delivery(api_client, order_no)

    before = _eligibility(api_client, order_no)
    assert before.status_code == 200, before.text
    item = before.json()["items"][0]
    assert item["warranty_months"] == 12
    assert item["eligible"] is True
    assert item["deadline"]

    changed = api_client.patch(
        "/api/admin/products/SKU-EAR-001",
        json={"warranty_months": 0},
    )
    assert changed.status_code == 200, changed.text

    after = _eligibility(api_client, order_no)
    assert after.json()["items"][0]["warranty_months"] == 12
    assert after.json()["items"][0]["eligible"] is True
    accepted = _repair(api_client, order_no)
    assert accepted.status_code == 200, accepted.text


def test_new_orders_use_current_product_warranty(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    assert (
        api_client.patch(
            "/api/admin/products/SKU-EAR-001",
            json={"warranty_months": 0},
        ).status_code
        == 200
    )
    no_warranty_order = _paid_order(api_client, "warranty-none")
    _delivery(api_client, no_warranty_order)
    no_warranty = _eligibility(api_client, no_warranty_order)
    item = no_warranty.json()["items"][0]
    assert item["warranty_months"] == 0
    assert item["eligible"] is False
    assert item["reason"] == "该商品不提供保修"
    rejected = _repair(api_client, no_warranty_order)
    assert rejected.status_code == 409

    assert (
        api_client.patch(
            "/api/admin/products/SKU-EAR-001",
            json={"warranty_months": 1},
        ).status_code
        == 200
    )
    short_order = _paid_order(api_client, "warranty-one-month")
    _delivery(api_client, short_order)

    _set_delivered_at(db_session, short_order, datetime.now(UTC) - timedelta(days=62))
    expired = _eligibility(api_client, short_order)
    item = expired.json()["items"][0]
    assert item["warranty_months"] == 1
    assert item["eligible"] is False
    assert "1 个月" in item["reason"]
    assert _repair(api_client, short_order).status_code == 409

    _set_delivered_at(db_session, short_order, datetime.now(UTC) - timedelta(days=20))
    accepted = _repair(api_client, short_order)
    assert accepted.status_code == 200, accepted.text

    still_zero = _eligibility(api_client, no_warranty_order)
    assert still_zero.json()["items"][0]["warranty_months"] == 0

    assert (
        api_client.patch(
            "/api/admin/products/SKU-EAR-001",
            json={"warranty_months": 121},
        ).status_code
        == 422
    )
    assert (
        api_client.patch(
            "/api/admin/products/SKU-EAR-001",
            json={"warranty_months": -1},
        ).status_code
        == 422
    )


def test_create_product_with_custom_warranty(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    created = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-WAR-006",
            "title": "半年保修商品",
            "price_cents": 9900,
            "warranty_months": 6,
            "initial_qty": 1,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["warranty_months"] == 6

    storefront = api_client.get("/api/store/products/SKU-WAR-006")
    assert storefront.status_code == 200, storefront.text
    assert storefront.json()["warranty_months"] == 6

    invalid = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-WAR-121",
            "title": "保修超限",
            "price_cents": 9900,
            "warranty_months": 121,
        },
    )
    assert invalid.status_code == 422