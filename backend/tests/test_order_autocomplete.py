from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Order, Shipment
from app.services.orders import auto_complete_delivered_orders

ADDRESS = {
    "recipient_name": "自动完成顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "自动完成路 1 号",
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


def _deliver(client: TestClient, order_no: str, tracking: str) -> None:
    assert (
        client.post(
            f"/api/admin/orders/{order_no}/shipment",
            json={"carrier": "顺丰速运", "tracking_no": tracking},
        ).status_code
        == 200
    )
    for status in ("in_transit", "out_for_delivery", "delivered"):
        assert (
            client.post(
                f"/api/admin/orders/{order_no}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )


def _backdate(session, order_no: str, days: int = 8) -> None:
    order = session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    shipment = session.execute(
        select(Shipment).where(Shipment.order_id == order.id)
    ).scalar_one()
    shipment.delivered_at = datetime.now(UTC) - timedelta(days=days)
    session.commit()


def test_auto_completes_only_old_delivered_orders(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "autocomplete-0001")
    admin_login()
    _deliver(api_client, order_no, "SF-AUTO-0001")
    _backdate(db_session, order_no)

    assert auto_complete_delivered_orders(db_session, days=7) == 1
    db_session.expire_all()
    order = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    assert order.status == "completed"
    event = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "order_auto_completed")
    ).scalar_one()
    assert event.payload_json["order_no"] == order_no

    assert auto_complete_delivered_orders(db_session, days=7) == 0


def test_auto_complete_skips_active_return_and_pending_proposal(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    return_order = _paid_order(api_client, "autocomplete-return-0001")
    proposal_order = _paid_order(api_client, "autocomplete-proposal-0001")
    admin_login()
    _deliver(api_client, return_order, "SF-AUTO-RET-0001")
    _deliver(api_client, proposal_order, "SF-AUTO-PROP-0001")

    return_request = api_client.post(
        "/api/store/returns",
        json={"order_no": return_order, "reason": "自动完成跳过退货"},
    )
    assert return_request.status_code == 200, return_request.text
    refund = api_client.post(
        "/api/chat",
        json={"text": f"订单 {proposal_order} 我要退款"},
    )
    assert refund.status_code == 200
    assert refund.json()["proposals"]

    _backdate(db_session, return_order)
    _backdate(db_session, proposal_order)
    assert auto_complete_delivered_orders(db_session, days=7) == 0
    db_session.expire_all()
    for order_no in (return_order, proposal_order):
        status = db_session.execute(
            select(Order.status).where(Order.order_no == order_no)
        ).scalar_one()
        assert status == "delivered"
