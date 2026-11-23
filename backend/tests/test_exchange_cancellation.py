from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    AuditEvent,
    Customer,
    Inventory,
    InventoryReservation,
    Notification,
    Order,
    Product,
    Refund,
    ReturnRequest,
)

ADDRESS = {
    "recipient_name": "换货取消测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "换货取消路 1 号",
}

TARGET_SKU = "SKU-EXC-EXP-001"


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
        client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )
    return body["order_no"]


def _delivery(client: TestClient, order_no: str, tracking_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": tracking_no},
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


def _add_expensive_product(db_session, shop_data) -> Inventory:
    expensive = Product(
        merchant_id=shop_data["merchant"].id,
        sku=TARGET_SKU,
        title="高价换货耳机",
        category="耳机",
        description="高价",
        brand="测试",
        specs="测试",
        price_cents=99900,
        support_7d_no_reason=True,
        status="active",
    )
    db_session.add(expensive)
    db_session.flush()
    inventory = Inventory(
        merchant_id=shop_data["merchant"].id,
        product_id=expensive.id,
        warehouse_id="华东",
        warehouse_name="上海仓",
        qty_available=5,
    )
    db_session.add(inventory)
    db_session.commit()
    return inventory


def _pending_exchange(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    *,
    key: str,
) -> tuple[int, str, str, Inventory]:
    inventory = _add_expensive_product(db_session, shop_data)
    login()
    order_no = _paid_order(api_client, key)
    admin_login()
    _delivery(api_client, order_no, f"SF-{key}")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "换货取消测试",
            "resolution": "exchange",
            "exchange_sku": TARGET_SKU,
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["exchange_supplement_cents"] == 10000
    assert body["exchange_payment_status"] == "pending"
    return return_id, order_no, body["replacement_order_no"], inventory


def test_customer_cancel_exchange_releases_stock_and_refunds(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    return_id, order_no, replacement_no, inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="exchange-cancel-0001",
    )
    db_session.expire_all()
    assert db_session.get(Inventory, inventory.id).qty_available == 4

    cancelled = api_client.post(
        f"/api/store/returns/{return_id}/exchange/cancel",
        json={"reason": "补差太高，放弃换货"},
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "exchange_cancelled"
    assert body["exchange_payment_status"] == "cancelled"
    assert body["exchange_cancelled_at"] is not None
    assert body["exchange_cancel_reason"] == "补差太高，放弃换货"
    assert body["exchange_cancelled_by"] == "customer"
    assert body["refund_amount_cents"] == 89900
    assert body["refund_proposal_id"] is not None
    assert body["inspection_deduction_cents"] == 0

    db_session.expire_all()
    assert db_session.get(Inventory, inventory.id).qty_available == 5
    replacement = db_session.execute(
        select(Order).where(Order.order_no == replacement_no)
    ).scalar_one()
    assert replacement.status == "cancelled"
    assert replacement.payment_status == "refunded"
    reservations = db_session.execute(
        select(InventoryReservation).where(
            InventoryReservation.order_id == replacement.id
        )
    ).scalars().all()
    assert reservations
    assert all(item.status == "released" for item in reservations)

    db_session.expire_all()
    notification = db_session.execute(
        select(Notification).where(
            Notification.type == "return_exchange_cancelled"
        )
    ).scalar_one()
    assert notification.customer_id is not None
    assert "退款" in notification.title or "退款" in notification.body

    timeline = api_client.get("/api/store/after-sales/timeline")
    assert timeline.status_code == 200, timeline.text
    assert any(
        row["title"] == "换货已取消" for row in timeline.json()
    )

    again = api_client.post(
        f"/api/store/returns/{return_id}/exchange/cancel", json={}
    )
    assert again.status_code == 409

    pay_after_cancel = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert pay_after_cancel.status_code == 409

    approved = api_client.post(
        f"/api/admin/proposals/{body['refund_proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    refund = db_session.execute(select(Refund)).scalar_one()
    assert refund.amount_cents == 89900
    order = api_client.get(f"/api/store/orders/{order_no}")
    assert order.status_code == 200
    assert order.json()["status"] == "refunded"

    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "exchange_cancelled"
        )
    ).scalar_one()
    assert event.payload_json["refund_amount_cents"] == 89900
    assert event.payload_json["cancelled_by"] == "customer"


def test_cannot_cancel_exchange_after_supplement_paid(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    return_id, _order_no, _replacement_no, inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="exchange-cancel-0002",
    )
    paid = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert paid.status_code == 200, paid.text
    blocked = api_client.post(
        f"/api/store/returns/{return_id}/exchange/cancel", json={}
    )
    assert blocked.status_code == 409
    assert "待支付" in blocked.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Inventory, inventory.id).qty_available == 4


def test_other_customer_cannot_cancel_and_staff_can(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    return_id, _order_no, _replacement_no, inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="exchange-cancel-0003",
    )
    login("13900000009")
    foreign = api_client.post(
        f"/api/store/returns/{return_id}/exchange/cancel", json={}
    )
    assert foreign.status_code == 404

    admin_login()
    cancelled = api_client.post(
        f"/api/admin/returns/{return_id}/exchange/cancel",
        json={"reason": "顾客电话要求取消"},
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "exchange_cancelled"
    assert body["exchange_cancelled_by"] == "staff"
    assert body["refund_proposal_id"] is not None
    assert body["refund_amount_cents"] == 89900
    db_session.expire_all()
    assert db_session.get(Inventory, inventory.id).qty_available == 5
    customer = db_session.execute(select(Customer)).scalars().first()
    assert customer is not None
    notification = db_session.execute(
        select(Notification).where(
            Notification.type == "return_exchange_cancelled",
            Notification.customer_id == customer.id,
        )
    ).scalar_one()
    assert "换货已取消" in notification.title
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert request.exchange_cancel_reason == "顾客电话要求取消"
