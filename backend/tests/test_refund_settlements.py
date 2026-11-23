from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Notification, Refund

ADDRESS = {
    "recipient_name": "退款结算顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "退款结算路 1 号",
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
        client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )
    return body["order_no"]


def _processing_refund(client: TestClient, key: str) -> tuple[str, int]:
    order_no = _paid_order(client, key)
    proposal = client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "refund",
            "order_no": order_no,
            "amount_cents": 1000,
            "reason": "退款结算测试",
        },
    )
    assert proposal.status_code == 200, proposal.text
    assert (
        client.post(
            f"/api/admin/proposals/{proposal.json()['proposal_id']}/approve"
        ).status_code
        == 200
    )
    listed = client.get(
        "/api/admin/finance/refunds", params={"status": "processing"}
    )
    assert listed.status_code == 200, listed.text
    refund = next(
        item
        for item in listed.json()
        if item["order_no"] == order_no
    )
    return order_no, refund["id"]


def test_refund_settlement_failure_retry_and_success(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    order_no, refund_id = _processing_refund(
        api_client, "settlement-flow-0001"
    )
    pending = api_client.get(
        "/api/admin/finance/refunds", params={"status": "processing"}
    )
    row = next(
        item for item in pending.json() if item["id"] == refund_id
    )
    assert row["settlement_status"] == "processing"
    assert row["settlement_attempts"] == 1
    assert row["settlement_reference"] == ""
    assert row["status"] == "executed"

    detail = api_client.get(f"/api/store/orders/{order_no}")
    assert detail.status_code == 200, detail.text
    refund_row = detail.json()["refunds"][0]
    assert refund_row["settlement_status"] == "processing"

    missing_reason = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": False},
    )
    assert missing_reason.status_code == 422
    assert "原因" in missing_reason.json()["detail"]

    failed = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": False, "failure_reason": "渠道返回余额不足"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["settlement_status"] == "failed"
    assert failed.json()["settlement_failure_reason"] == "渠道返回余额不足"
    assert failed.json()["settled_at"] is None

    retried = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/retry"
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["settlement_status"] == "processing"
    assert retried.json()["settlement_attempts"] == 2
    assert retried.json()["settlement_failure_reason"] == ""

    succeeded = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": True, "reference": "PSP-TEST-0001"},
    )
    assert succeeded.status_code == 200, succeeded.text
    assert succeeded.json()["settlement_status"] == "succeeded"
    assert succeeded.json()["settlement_reference"] == "PSP-TEST-0001"
    assert succeeded.json()["settled_at"] is not None
    timeline = api_client.get("/api/store/after-sales/timeline")
    assert timeline.status_code == 200, timeline.text
    assert any(
        row["title"] == "退款已到账" and row["order_no"] == order_no
        for row in timeline.json()
    )

    again = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": True},
    )
    assert again.status_code == 409
    retry_succeeded = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/retry"
    )
    assert retry_succeeded.status_code == 409

    customer_detail = api_client.get(f"/api/store/orders/{order_no}")
    refund_row = customer_detail.json()["refunds"][0]
    assert refund_row["settlement_status"] == "succeeded"
    assert refund_row["settlement_reference"] == "PSP-TEST-0001"

    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 退款进度"}
    )
    assert chat.status_code == 200, chat.text
    assert "渠道已到账" in chat.json()["reply"]
    assert "PSP-TEST-0001" in chat.json()["reply"]

    db_session.expire_all()
    stored = db_session.execute(
        select(Refund).where(Refund.id == refund_id)
    ).scalar_one()
    assert stored.settlement_status == "succeeded"
    assert stored.settlement_attempts == 2
    events = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type.like("refund_settlement_%")
        )
    ).scalars().all()
    assert set(events) == {
        "refund_settlement_failed",
        "refund_settlement_retried",
        "refund_settlement_succeeded",
    }
    notifications = db_session.execute(
        select(Notification.type).where(
            Notification.type.in_(
                ("refund_settlement_failed", "refund_settled")
            )
        )
    ).scalars().all()
    assert set(notifications) == {
        "refund_settlement_failed",
        "refund_settled",
    }
