from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import (
    AuditEvent,
    Inventory,
    Notification,
    Product,
    ReturnRequest,
)
from app.services.after_sales_reminders import send_after_sales_reminders
from app.services.returns import auto_cancel_overdue_exchange_supplements

ADDRESS = {
    "recipient_name": "补差超时测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "补差超时路 1 号",
}

TARGET_SKU = "SKU-EXP-SUP-001"


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


def _pending_exchange(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    *,
    key: str,
) -> tuple[int, str, str, Inventory]:
    target = Product(
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
    db_session.add(target)
    db_session.flush()
    inventory = Inventory(
        merchant_id=shop_data["merchant"].id,
        product_id=target.id,
        warehouse_id="华东",
        warehouse_name="上海仓",
        qty_available=5,
    )
    db_session.add(inventory)
    db_session.commit()

    login()
    order_no = _paid_order(api_client, key)
    admin_login()
    _delivery(api_client, order_no, f"SF-{key}")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "补差超时测试",
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
    assert body["exchange_payment_deadline"] is not None
    return return_id, order_no, body["replacement_order_no"], inventory


def test_supplement_reminder_then_auto_cancel(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    now = datetime.now(UTC)
    return_id, _order_no, _replacement_no, inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="supplement-expiry-0001",
    )
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    request.received_at = now - timedelta(hours=25)
    db_session.commit()

    stats = send_after_sales_reminders(db_session, now=now)
    assert stats["supplement_pending"] == 1
    reminder = db_session.execute(
        select(Notification).where(
            Notification.type == "return_exchange_supplement_reminder"
        )
    ).scalar_one()
    assert "自动取消" in reminder.body

    again = send_after_sales_reminders(db_session, now=now)
    assert again["supplement_pending"] == 0

    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    request.received_at = now - timedelta(hours=73)
    request.exchange_supplement_deadline = now - timedelta(hours=1)
    db_session.commit()

    cancelled = auto_cancel_overdue_exchange_supplements(
        db_session, now=now, hours=72
    )
    assert cancelled == 1

    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert request.status == "exchange_cancelled"
    assert request.exchange_payment_status == "cancelled"
    assert request.exchange_cancelled_by == "system"
    assert "自动取消" in request.exchange_cancel_reason
    assert request.refund_proposal_id is not None
    assert request.refund_amount_cents == 89900
    assert db_session.get(Inventory, inventory.id).qty_available == 5

    staff_notifications = int(
        db_session.execute(
            select(func.count(Notification.id)).where(
                Notification.type == "exchange_cancelled"
            )
        ).scalar_one()
    )
    assert staff_notifications == 2
    customer_notification = db_session.execute(
        select(Notification).where(
            Notification.type == "return_exchange_cancelled"
        )
    ).scalar_one()
    assert "取消" in customer_notification.title

    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "exchange_cancelled"
        )
    ).scalar_one()
    assert event.payload_json["cancelled_by"] == "system"

    timeline = api_client.get("/api/store/after-sales/timeline")
    assert timeline.status_code == 200, timeline.text
    cancelled_event = next(
        row for row in timeline.json() if row["title"] == "换货已取消"
    )
    assert "系统自动" in cancelled_event["detail"]
    assert "退款提案" in cancelled_event["detail"]

    repeated = auto_cancel_overdue_exchange_supplements(
        db_session, now=now, hours=72
    )
    assert repeated == 0


def test_not_due_or_paid_supplement_is_not_cancelled(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    now = datetime.now(UTC)
    return_id, _order_no, _replacement_no, inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="supplement-expiry-0002",
    )
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    request.received_at = now - timedelta(hours=71)
    db_session.commit()
    assert (
        auto_cancel_overdue_exchange_supplements(
            db_session, now=now, hours=72
        )
        == 0
    )
    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert request.status == "received"

    paid = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert paid.status_code == 200, paid.text
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    request.received_at = now - timedelta(hours=73)
    db_session.commit()
    assert (
        auto_cancel_overdue_exchange_supplements(
            db_session, now=now, hours=72
        )
        == 0
    )
    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert request.status == "received"
    assert request.exchange_payment_status == "paid"
    assert db_session.get(Inventory, inventory.id).qty_available == 4


def test_disabled_timeout_does_not_cancel(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    now = datetime.now(UTC)
    return_id, _order_no, _replacement_no, _inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="supplement-expiry-0003",
    )
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    request.received_at = now - timedelta(hours=100)
    db_session.commit()
    assert (
        auto_cancel_overdue_exchange_supplements(
            db_session, now=now, hours=0
        )
        == 0
    )
    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert request.status == "received"


def _deadline_from_api(row: dict) -> datetime:
    value = row["exchange_payment_deadline"]
    assert value
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def test_extend_exchange_supplement_deadline(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    now = datetime.now(UTC)
    return_id, _order_no, _replacement_no, _inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="supplement-extend-0001",
    )
    before = next(
        row
        for row in api_client.get("/api/store/returns").json()
        if row["id"] == return_id
    )
    original_deadline = _deadline_from_api(before)
    extended = api_client.post(
        f"/api/admin/returns/{return_id}/exchange-supplement/extend",
        json={"hours": 24},
    )
    assert extended.status_code == 200, extended.text
    new_deadline = _deadline_from_api(extended.json())
    assert _as_utc(new_deadline) == _as_utc(original_deadline) + timedelta(
        hours=24
    )

    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert request.exchange_supplement_deadline is not None
    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "exchange_supplement_extended"
        )
    ).scalar_one()
    assert event.payload_json["hours"] == 24
    notification = db_session.execute(
        select(Notification).where(
            Notification.type == "return_exchange_supplement_extended"
        )
    ).scalar_one()
    assert "支付期限已延长" in notification.title

    assert (
        auto_cancel_overdue_exchange_supplements(
            db_session, now=now, hours=72
        )
        == 0
    )

    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    request.exchange_supplement_deadline = now - timedelta(hours=1)
    db_session.commit()
    expired = api_client.post(
        f"/api/admin/returns/{return_id}/exchange-supplement/extend",
        json={"hours": 24},
    )
    assert expired.status_code == 409
    assert "超期" in expired.json()["detail"]

    assert (
        auto_cancel_overdue_exchange_supplements(
            db_session, now=now, hours=72
        )
        == 1
    )
    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert request.status == "exchange_cancelled"
    assert "期限已过" in request.exchange_cancel_reason
    assert request.exchange_cancelled_by == "system"


def test_paid_supplement_cannot_be_extended(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    return_id, _order_no, _replacement_no, _inventory = _pending_exchange(
        api_client,
        shop_data,
        login,
        admin_login,
        db_session,
        key="supplement-extend-0002",
    )
    paid = api_client.post(
        f"/api/store/returns/{return_id}/exchange-supplement/pay"
    )
    assert paid.status_code == 200, paid.text
    blocked = api_client.post(
        f"/api/admin/returns/{return_id}/exchange-supplement/extend",
        json={"hours": 24},
    )
    assert blocked.status_code == 409
    assert "待支付" in blocked.json()["detail"]

    invalid = api_client.post(
        f"/api/admin/returns/{return_id}/exchange-supplement/extend",
        json={"hours": 0},
    )
    assert invalid.status_code == 422
