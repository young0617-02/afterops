from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    InventoryMovement,
    InventoryReservation,
    Order,
)
from app.services.orders import expire_overdue_reservations

ADDRESS = {
    "recipient_name": "库存流水顾客",
    "recipient_phone": "13800000006",
    "region": "华东",
    "city": "上海",
    "detail": "库存流水路 6 号",
}


def _paid_order(
    client: TestClient, key: str, *, sku: str = "SKU-EAR-001"
) -> str:
    client.post("/api/store/cart/items", json={"sku": sku, "qty": 1})
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
        json={"carrier": "顺丰速运", "tracking_no": f"SF-{uuid4().hex[:8]}"},
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


def _movements(client: TestClient, sku: str) -> list[dict]:
    response = client.get(
        "/api/admin/inventory-movements", params={"sku": sku}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_checkout_and_cancel_movements(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "movement-cancel"},
    )
    assert checkout.status_code == 201, checkout.text
    order_no = checkout.json()["order_no"]

    admin_login()
    rows = _movements(api_client, "SKU-EAR-001")
    assert rows[0]["reason"] == "checkout_reserved"
    assert rows[0]["qty_delta"] == -1
    assert rows[0]["qty_after"] == 9
    assert rows[0]["order_no"] == order_no

    cancelled = api_client.post(f"/api/store/orders/{order_no}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    rows = _movements(api_client, "SKU-EAR-001")
    assert rows[0]["reason"] == "order_cancelled"
    assert rows[0]["qty_delta"] == 1
    assert rows[0]["qty_after"] == 10

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert _movements(api_client, "SKU-EAR-001")


def test_expired_order_release_movement(
    api_client: TestClient,
    shop_data,
    login,
    db_session,
) -> None:
    login()
    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "movement-expired"},
    )
    assert checkout.status_code == 201, checkout.text
    order_no = checkout.json()["order_no"]
    order = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    reservation = db_session.execute(
        select(InventoryReservation).where(
            InventoryReservation.order_id == order.id
        )
    ).scalar_one()
    reservation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    assert expire_overdue_reservations(db_session) == 1
    movement = db_session.execute(
        select(InventoryMovement)
        .where(InventoryMovement.order_id == order.id)
        .order_by(InventoryMovement.id.desc())
    ).scalars().first()
    assert movement is not None
    assert movement.reason == "order_expired"
    assert movement.qty_delta == 1
    assert movement.qty_after == 10


def test_admin_adjustment_and_product_creation_movements(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    adjusted = api_client.put(
        "/api/admin/products/SKU-EAR-001/inventory/华东",
        json={"qty_available": 12, "warehouse_name": "上海青浦仓"},
    )
    assert adjusted.status_code == 200, adjusted.text
    rows = _movements(api_client, "SKU-EAR-001")
    assert rows[0]["reason"] == "admin_adjustment"
    assert rows[0]["qty_delta"] == 2
    assert rows[0]["qty_after"] == 12
    assert rows[0]["operator_email"] == "owner@test.local"

    created = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-MOVE-001",
            "title": "流水测试商品",
            "price_cents": 1000,
            "initial_qty": 3,
        },
    )
    assert created.status_code == 200, created.text
    rows = _movements(api_client, "SKU-MOVE-001")
    assert rows[0]["reason"] == "product_created"
    assert rows[0]["qty_delta"] == 3
    assert rows[0]["qty_after"] == 3


def test_return_restock_movement(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "movement-return")
    admin_login()
    _delivery(api_client, order_no)
    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "流水退货"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert api_client.post(f"/api/admin/returns/{return_id}/approve").status_code == 200
    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-RETURN-MOVE"},
        ).status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text

    rows = _movements(api_client, "SKU-EAR-001")
    restock = next(row for row in rows if row["reason"] == "return_restocked")
    assert restock["qty_delta"] == 1
    assert restock["qty_after"] == 10
    assert restock["order_no"] == order_no
    assert restock["return_id"] == return_id
    assert restock["note"].startswith("退货回补")


def test_exchange_allocation_movement(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "movement-exchange")
    admin_login()
    _delivery(api_client, order_no)
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "流水换货",
            "resolution": "exchange",
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert api_client.post(f"/api/admin/returns/{return_id}/approve").status_code == 200
    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-EXCHANGE-MOVE"},
        ).status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    replacement_no = received.json()["replacement_order_no"]

    rows = _movements(api_client, "SKU-EAR-001")
    allocation = next(
        row for row in rows if row["reason"] == "exchange_allocated"
    )
    assert allocation["qty_delta"] == -1
    assert allocation["order_no"] == replacement_no
    assert allocation["return_id"] == return_id
    assert any(row["reason"] == "return_restocked" for row in rows)
