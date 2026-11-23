from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.models import AuditEvent, Outbox
from app.services.notifications import notify_staff
from app.settings import Settings
from app.worker.outbox import process_once


def _outbox_rows(db_session) -> list[Outbox]:
    return db_session.execute(select(Outbox).order_by(Outbox.id)).scalars().all()


def test_staff_notification_enqueues_email_and_worker_marks_processed(
    db_session, shop_data
) -> None:
    notify_staff(
        db_session,
        shop_data["merchant"].id,
        notification_type="proposal_created",
        title="新的退款提案 #1",
        body="订单 ORD-TEST-1 · 899.00 元",
        link="/admin",
    )
    db_session.commit()
    rows = _outbox_rows(db_session)
    assert len(rows) == 2
    assert {row.payload_json["to"] for row in rows} == {
        "owner@test.local",
        "agent@test.local",
    }
    assert all(row.status == "pending" for row in rows)
    assert all(
        row.payload_json["subject"] == "[AfterOps] 新的退款提案 #1"
        for row in rows
    )

    sent: list[dict] = []
    stats = process_once(db_session, sender=sent.append)
    assert stats == {"processed": 2, "failed": 0, "scheduled": 0}
    assert len(sent) == 2
    rows = _outbox_rows(db_session)
    assert all(row.status == "processed" for row in rows)
    assert all(row.attempts == 1 for row in rows)
    assert all(row.processed_at is not None for row in rows)

    again = process_once(db_session, sender=sent.append)
    assert again == {"processed": 0, "failed": 0, "scheduled": 0}
    assert len(sent) == 2


def test_outbox_failure_retries_then_fails(db_session, shop_data) -> None:
    db_session.add(
        Outbox(
            merchant_id=shop_data["merchant"].id,
            event_type="staff_notification_email",
            aggregate_type="notification",
            aggregate_id="test",
            payload_json={
                "to": "owner@test.local",
                "subject": "test",
                "body": "test",
            },
            status="pending",
            available_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    db_session.commit()

    def fail(_payload: dict) -> None:
        raise OSError("smtp unavailable")

    cfg = Settings(outbox_max_attempts=2)
    first = process_once(
        db_session,
        sender=fail,
        cfg=cfg,
        now=datetime.now(UTC),
    )
    assert first["scheduled"] == 1
    row = _outbox_rows(db_session)[0]
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.available_at > datetime.now(UTC) - timedelta(seconds=5)

    second = process_once(
        db_session,
        sender=fail,
        cfg=cfg,
        now=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert second["failed"] == 1
    row = _outbox_rows(db_session)[0]
    assert row.status == "failed"
    assert row.attempts == 2


def test_admin_can_inspect_and_retry_failed_outbox(
    api_client, shop_data, admin_login, db_session
) -> None:
    failed = Outbox(
        merchant_id=shop_data["merchant"].id,
        event_type="staff_notification_email",
        aggregate_type="notification",
        aggregate_id="test",
        payload_json={
            "to": "owner@test.local",
            "subject": "[AfterOps] failed",
            "body": "fail",
        },
        status="failed",
        attempts=5,
        last_error="smtp unavailable",
        available_at=datetime.now(UTC),
    )
    db_session.add(failed)
    db_session.commit()

    admin_login()
    listed = api_client.get("/api/admin/outbox?status=failed")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["counts"]["failed"] == 1
    item = body["items"][0]
    assert item["last_error"] == "smtp unavailable"
    assert item["subject"] == "[AfterOps] failed"

    retried = api_client.post(f"/api/admin/outbox/{item['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "pending"
    assert retried.json()["attempts"] == 0
    db_session.expire_all()
    row = db_session.get(Outbox, item["id"])
    assert row.status == "pending"
    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "outbox_retry_requested"
        )
    ).scalar_one()
    assert event.payload_json["outbox_id"] == item["id"]

    admin_login("agent@test.local", "agent123456")
    assert api_client.get("/api/admin/outbox").status_code == 200
    assert (
        api_client.post(f"/api/admin/outbox/{item['id']}/retry").status_code
        == 403
    )

    admin_login()
    row.status = "processed"
    row.processed_at = datetime.now(UTC)
    db_session.commit()
    assert (
        api_client.post(f"/api/admin/outbox/{item['id']}/retry").status_code
        == 409
    )
