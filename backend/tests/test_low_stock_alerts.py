from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Notification, Outbox


def _low_stock_notifications(db_session) -> list[Notification]:
    return db_session.execute(
        select(Notification).where(Notification.type == "low_stock")
    ).scalars().all()


def test_inventory_adjustment_alerts_only_when_crossing(
    api_client: TestClient, shop_data, admin_login, db_session
) -> None:
    admin_login()
    url = "/api/admin/products/SKU-EAR-001/inventory/华东"
    assert api_client.put(url, json={"qty_available": 4}).status_code == 200
    db_session.expire_all()
    assert len(_low_stock_notifications(db_session)) == 2
    assert len(
        db_session.execute(
            select(Outbox).where(
                Outbox.payload_json["subject"].as_string().like("%库存不足%")
            )
        ).scalars().all()
    ) == 2

    assert api_client.put(url, json={"qty_available": 3}).status_code == 200
    db_session.expire_all()
    assert len(_low_stock_notifications(db_session)) == 2

    assert api_client.put(url, json={"qty_available": 20}).status_code == 200
    assert api_client.put(url, json={"qty_available": 5}).status_code == 200
    db_session.expire_all()
    assert len(_low_stock_notifications(db_session)) == 4


def test_checkout_depletion_triggers_low_stock_alert(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 6})
    checkout = api_client.post(
        "/api/store/checkout",
        json={
            "address": {
                "recipient_name": "低库存顾客",
                "recipient_phone": "13800000001",
                "region": "华东",
                "city": "上海",
                "detail": "低库存路 1 号",
            }
        },
        headers={"Idempotency-Key": "low-stock-checkout-0001"},
    )
    assert checkout.status_code == 201, checkout.text
    db_session.expire_all()
    assert len(_low_stock_notifications(db_session)) == 2
