from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Inventory, InventoryReservation, Notification, Order
from app.services.orders import expire_overdue_reservations


def _checkout(client: TestClient, key: str) -> dict:
    added = client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    assert added.status_code == 200, added.text
    response = client.post(
        "/api/store/checkout",
        headers={"Idempotency-Key": key},
        json={
            "address": {
                "recipient_name": "超时顾客",
                "recipient_phone": "13800000001",
                "region": "华东",
                "city": "上海",
                "detail": "超时测试路 1 号",
            }
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_expire_overdue_reservations_scans_and_notifies(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    earphone_id = shop_data["earphone"].id
    login()
    overdue = _checkout(api_client, "expiry-scan-overdue")
    active = _checkout(api_client, "expiry-scan-active")

    db_session.expire_all()
    orders = {
        order.order_no: order
        for order in db_session.execute(
            select(Order).where(
                Order.order_no.in_([overdue["order_no"], active["order_no"]])
            )
        ).scalars()
    }
    overdue_order = orders[overdue["order_no"]]
    active_order = orders[active["order_no"]]
    reservations = db_session.execute(
        select(InventoryReservation).where(
            InventoryReservation.order_id == overdue_order.id
        )
    ).scalars().all()
    for row in reservations:
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    assert expire_overdue_reservations(db_session) == 1

    db_session.expire_all()
    overdue_order = db_session.get(Order, overdue_order.id)
    active_order = db_session.get(Order, active_order.id)
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    reservations = db_session.execute(
        select(InventoryReservation).where(
            InventoryReservation.order_id == overdue_order.id
        )
    ).scalars().all()
    notifications = db_session.execute(
        select(Notification).where(
            Notification.customer_id == overdue_order.customer_id,
            Notification.type == "order_expired",
        )
    ).scalars().all()

    assert overdue_order.status == "cancelled"
    assert active_order.status == "pending_payment"
    assert sum(row.qty_available for row in inventory) == 14
    assert sum(row.qty_reserved for row in inventory) == 1
    assert [row.status for row in reservations] == ["expired"]
    assert len(notifications) == 1
    assert notifications[0].title == "订单已超时关闭"
    assert overdue_order.order_no in notifications[0].body
