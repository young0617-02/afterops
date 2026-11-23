from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Notification, Refund
from app.services.refund_settlements import (
    auto_retry_failed_refunds,
    auto_settle_processing_refunds,
)

ADDRESS = {
    "recipient_name": "自动结算顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "自动结算路 1 号",
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
            "reason": "自动结算测试",
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
        item for item in listed.json() if item["order_no"] == order_no
    )
    return order_no, refund["id"]


def test_auto_settle_due_processing_refund(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    order_no, refund_id = _processing_refund(
        api_client, "auto-settle-0001"
    )
    now = datetime.now(UTC)
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    refund.executed_at = now - timedelta(minutes=6)
    db_session.commit()

    settled = auto_settle_processing_refunds(
        db_session, now=now, minutes=5
    )
    assert settled == 1

    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_status == "succeeded"
    assert refund.settlement_reference.startswith("PSP-AUTO-")
    assert refund.settlement_failure_reason == ""
    assert refund.settled_at is not None
    assert refund.settlement_attempts == 1

    notification = db_session.execute(
        select(Notification).where(
            Notification.type == "refund_settled"
        )
    ).scalar_one()
    assert order_no in notification.body
    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "refund_settlement_auto"
        )
    ).scalar_one()
    assert event.payload_json["auto_minutes"] == 5
    assert event.payload_json["operator_user_id"] is None
    assert event.payload_json["reference"].startswith("PSP-AUTO-")

    again = auto_settle_processing_refunds(
        db_session, now=now, minutes=5
    )
    assert again == 0
    manual = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": True, "reference": "PSP-MANUAL-AFTER"},
    )
    assert manual.status_code == 409


def test_auto_settle_skips_not_due_and_disabled(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, refund_id = _processing_refund(
        api_client, "auto-settle-0002"
    )
    now = datetime.now(UTC)
    assert (
        auto_settle_processing_refunds(db_session, now=now, minutes=5)
        == 0
    )
    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_status == "processing"

    refund.executed_at = now - timedelta(minutes=6)
    db_session.commit()
    assert (
        auto_settle_processing_refunds(db_session, now=now, minutes=0)
        == 0
    )
    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_status == "processing"

    manual = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": True, "reference": "PSP-MANUAL-0002"},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["settlement_status"] == "succeeded"


def test_auto_retry_failed_refund_then_settle(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, refund_id = _processing_refund(
        api_client, "auto-retry-0001"
    )
    failed = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": False, "failure_reason": "渠道超时"},
    )
    assert failed.status_code == 200, failed.text
    now = datetime.now(UTC)
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_failed_at is not None
    refund.settlement_failed_at = now - timedelta(minutes=16)
    db_session.commit()

    retried = auto_retry_failed_refunds(
        db_session, now=now, minutes=15, max_attempts=3
    )
    assert retried == 1
    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_status == "processing"
    assert refund.settlement_attempts == 2
    assert refund.settlement_failure_reason == ""
    assert refund.settlement_reference == ""
    assert refund.settlement_failed_at is None

    notification = db_session.execute(
        select(Notification).where(
            Notification.type == "refund_settlement_retried"
        )
    ).scalar_one()
    assert "系统自动重试" in notification.body
    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "refund_settlement_auto_retried"
        )
    ).scalar_one()
    assert event.payload_json["attempt"] == 2
    assert event.payload_json["max_attempts"] == 3
    assert event.payload_json["operator_user_id"] is None

    again = auto_retry_failed_refunds(
        db_session, now=now, minutes=15, max_attempts=3
    )
    assert again == 0

    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    refund.executed_at = now - timedelta(minutes=6)
    db_session.commit()
    settled = auto_settle_processing_refunds(
        db_session, now=now, minutes=5
    )
    assert settled == 1
    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_status == "succeeded"
    assert refund.settlement_reference.startswith("PSP-AUTO-")


def test_auto_retry_respects_limit_and_disable(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, refund_id = _processing_refund(
        api_client, "auto-retry-0002"
    )
    assert (
        api_client.post(
            f"/api/admin/finance/refunds/{refund_id}/settle",
            json={"succeeded": False, "failure_reason": "渠道拒绝"},
        ).status_code
        == 200
    )
    now = datetime.now(UTC)
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    refund.settlement_failed_at = now - timedelta(minutes=20)
    db_session.commit()

    assert (
        auto_retry_failed_refunds(
            db_session, now=now, minutes=0, max_attempts=3
        )
        == 0
    )
    assert (
        auto_retry_failed_refunds(
            db_session, now=now, minutes=15, max_attempts=1
        )
        == 0
    )
    assert (
        auto_retry_failed_refunds(
            db_session, now=now, minutes=15, max_attempts=2
        )
        == 1
    )

    second_failure = api_client.post(
        f"/api/admin/finance/refunds/{refund_id}/settle",
        json={"succeeded": False, "failure_reason": "再次失败"},
    )
    assert second_failure.status_code == 200, second_failure.text
    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_attempts == 2
    refund.settlement_failed_at = now - timedelta(minutes=20)
    db_session.commit()
    assert (
        auto_retry_failed_refunds(
            db_session, now=now, minutes=15, max_attempts=2
        )
        == 0
    )
    db_session.expire_all()
    refund = db_session.get(Refund, refund_id)
    assert refund is not None
    assert refund.settlement_status == "failed"
    assert refund.settlement_failure_reason == "再次失败"
