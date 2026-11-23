"""Single-process outbox worker for staff email notifications.

Delivery is at-least-once: the external SMTP call cannot join the database
transaction, so a crash after send and before commit can resend an email.
"""
from __future__ import annotations

import logging
import smtplib
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.models import Outbox
from app.services.after_sales_reminders import send_after_sales_reminders
from app.services.audit_anchors import write_audit_anchors
from app.services.orders import (
    auto_complete_delivered_orders,
    expire_overdue_reservations,
)
from app.services.refund_settlements import (
    auto_retry_failed_refunds,
    auto_settle_processing_refunds,
)
from app.services.returns import auto_cancel_overdue_exchange_supplements
from app.services.sla import send_sla_reminders
from app.settings import Settings, settings

logger = logging.getLogger("afterops.outbox")

Sender = Callable[[dict], None]


def smtp_sender(payload: dict) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = payload["to"]
    message["Subject"] = payload["subject"]
    message.set_content(payload.get("body", ""))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(message)


def _backoff_seconds(attempts: int) -> int:
    return min(60 * (2 ** max(0, attempts - 1)), 900)


def process_once(
    session: Session,
    *,
    sender: Sender | None = None,
    batch_size: int = 20,
    max_attempts: int | None = None,
    cfg: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    cfg = cfg or settings
    sender = sender or smtp_sender
    max_attempts = max_attempts or cfg.outbox_max_attempts
    now = now or datetime.now(UTC)
    rows = session.execute(
        select(Outbox)
        .where(
            Outbox.status == "pending",
            Outbox.available_at <= now,
        )
        .order_by(Outbox.available_at, Outbox.id)
        .limit(batch_size)
        .with_for_update()
    ).scalars().all()
    stats = {"processed": 0, "failed": 0, "scheduled": 0}
    for row in rows:
        row.status = "processing"
        row.attempts += 1
        session.flush()
        payload = row.payload_json or {}
        try:
            if "to" not in payload or "subject" not in payload:
                raise ValueError("outbox payload missing to/subject")
            sender(payload)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
            row.last_error = str(exc)[:1000]
            if row.attempts >= max_attempts:
                row.status = "failed"
                stats["failed"] += 1
                logger.error(
                    "outbox id=%s failed after %s attempts: %s",
                    row.id,
                    row.attempts,
                    exc,
                )
            else:
                row.status = "pending"
                row.available_at = now + timedelta(
                    seconds=_backoff_seconds(row.attempts)
                )
                stats["scheduled"] += 1
                logger.warning(
                    "outbox id=%s attempt %s failed, retry scheduled: %s",
                    row.id,
                    row.attempts,
                    exc,
                )
            continue
        row.status = "processed"
        row.processed_at = now
        row.last_error = ""
        stats["processed"] += 1
        logger.info("outbox id=%s processed", row.id)
    session.commit()
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info(
        "outbox worker started smtp=%s:%s poll=%ss auto_complete_days=%s "
        "expire_interval=%ss anchor_interval=%ss supplement_timeout=%sh "
        "refund_auto_settle=%sm retry=%sm/%sx",
        settings.smtp_host,
        settings.smtp_port,
        settings.outbox_poll_seconds,
        settings.order_auto_complete_days,
        settings.order_expire_interval_seconds,
        settings.audit_anchor_interval_seconds,
        settings.after_sales_supplement_timeout_hours,
        settings.refund_auto_settle_minutes,
        settings.refund_auto_retry_minutes,
        settings.refund_auto_retry_max_attempts,
    )
    last_job_at = 0.0
    last_sla_at = 0.0
    last_after_sales_at = 0.0
    last_expire_at = 0.0
    last_anchor_at = -settings.audit_anchor_interval_seconds
    while True:
        try:
            with SessionLocal() as session:
                stats = process_once(session)
                if any(stats.values()):
                    logger.info("outbox batch %s", stats)
            if (
                settings.order_expire_interval_seconds > 0
                and time.monotonic() - last_expire_at
                >= settings.order_expire_interval_seconds
            ):
                with SessionLocal() as expire_session:
                    expired = expire_overdue_reservations(expire_session)
                last_expire_at = time.monotonic()
                if expired:
                    logger.info("expired %s unpaid orders", expired)
            if (
                settings.order_auto_complete_days > 0
                and time.monotonic() - last_job_at
                >= settings.order_auto_complete_interval_seconds
            ):
                with SessionLocal() as job_session:
                    completed = auto_complete_delivered_orders(
                        job_session, days=settings.order_auto_complete_days
                    )
                last_job_at = time.monotonic()
                if completed:
                    logger.info("auto-completed %s delivered orders", completed)
            if time.monotonic() - last_sla_at >= settings.sla_reminder_interval_seconds:
                with SessionLocal() as sla_session:
                    stats = send_sla_reminders(
                        sla_session,
                        proposal_minutes=settings.proposal_sla_minutes,
                        handoff_minutes=settings.handoff_sla_minutes,
                        handoff_high_minutes=settings.handoff_sla_high_minutes,
                        handoff_urgent_minutes=settings.handoff_sla_urgent_minutes,
                        handoff_resolution_hours=(
                            settings.handoff_resolution_sla_hours
                        ),
                        handoff_first_response_minutes=(
                            settings.handoff_first_response_minutes
                        ),
                        handoff_escalation_minutes=(
                            settings.handoff_first_response_escalation_minutes
                        ),
                    )
                last_sla_at = time.monotonic()
                if any(stats.values()):
                    logger.info("sla reminders %s", stats)
            if (
                settings.after_sales_reminder_interval_seconds > 0
                and time.monotonic() - last_after_sales_at
                >= settings.after_sales_reminder_interval_seconds
            ):
                with SessionLocal() as reminder_session:
                    auto_settled = auto_settle_processing_refunds(
                        reminder_session,
                        minutes=settings.refund_auto_settle_minutes,
                    )
                    auto_retried = auto_retry_failed_refunds(
                        reminder_session,
                        minutes=settings.refund_auto_retry_minutes,
                        max_attempts=settings.refund_auto_retry_max_attempts,
                    )
                    auto_cancelled = auto_cancel_overdue_exchange_supplements(
                        reminder_session,
                        hours=settings.after_sales_supplement_timeout_hours,
                    )
                    stats = send_after_sales_reminders(
                        reminder_session,
                        quote_hours=settings.after_sales_quote_reminder_hours,
                        settlement_hours=(
                            settings.after_sales_settlement_reminder_hours
                        ),
                        appeal_hours=settings.after_sales_appeal_reminder_hours,
                        supplement_hours=(
                            settings.after_sales_supplement_reminder_hours
                        ),
                        supplement_timeout_hours=(
                            settings.after_sales_supplement_timeout_hours
                        ),
                    )
                last_after_sales_at = time.monotonic()
                if auto_settled:
                    logger.info(
                        "auto-settled %s refunds", auto_settled
                    )
                if auto_retried:
                    logger.info(
                        "auto-retried %s failed refunds", auto_retried
                    )
                if auto_cancelled:
                    logger.info(
                        "auto-cancelled %s overdue exchange supplements",
                        auto_cancelled,
                    )
                if any(stats.values()):
                    logger.info("after-sales reminders %s", stats)
            if (
                settings.audit_anchor_secret
                and settings.audit_anchor_interval_seconds > 0
                and time.monotonic() - last_anchor_at
                >= settings.audit_anchor_interval_seconds
            ):
                with SessionLocal() as anchor_session:
                    anchored = write_audit_anchors(anchor_session)
                last_anchor_at = time.monotonic()
                if anchored:
                    logger.info("audit anchors written: %s", anchored)
        except Exception:
            logger.exception("outbox loop failed")
        time.sleep(settings.outbox_poll_seconds)


if __name__ == "__main__":
    main()
