from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.domain.models import AuditEvent

# Two-key advisory lock namespace ("AFTR") so audit appends never collide with
# checkout's single-key lock or other advisory locks using one bigint key.
_AUDIT_LOCK_NAMESPACE = 0x41465452


def _serialize_chain(session: Session, merchant_id: int) -> None:
    # A row lock on merchants deadlocks under concurrency: both transactions
    # hold FK KEY SHARE locks from inserting child rows, then both request
    # FOR UPDATE on the same merchant row. An advisory lock serializes the
    # chain without participating in that lock order.
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "CAST(:namespace AS integer), CAST(:merchant_id AS integer))"
        ),
        {"namespace": _AUDIT_LOCK_NAMESPACE, "merchant_id": merchant_id},
    )


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _event_hash(
    prev_hash: str,
    merchant_id: int,
    conversation_id: int | None,
    proposal_id: int | None,
    event_type: str,
    payload: dict[str, Any],
    created_at: datetime,
) -> str:
    created = created_at.replace(tzinfo=None).isoformat(timespec="microseconds")
    raw = (
        f"{prev_hash}|{merchant_id}|{conversation_id or ''}|{proposal_id or ''}|"
        f"{event_type}|{_canonical(payload)}|{created}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_event(
    session: Session,
    merchant_id: int,
    event_type: str,
    payload: dict[str, Any],
    conversation_id: int | None = None,
    proposal_id: int | None = None,
) -> AuditEvent:
    _serialize_chain(session, merchant_id)
    last = session.execute(
        select(AuditEvent)
        .where(AuditEvent.merchant_id == merchant_id)
        .order_by(AuditEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    prev_hash = last.row_hash if last else "GENESIS"
    now = datetime.now(UTC)
    event = AuditEvent(
        merchant_id=merchant_id,
        conversation_id=conversation_id,
        proposal_id=proposal_id,
        event_type=event_type,
        payload_json=payload,
        prev_hash=prev_hash,
        row_hash=_event_hash(
            prev_hash,
            merchant_id,
            conversation_id,
            proposal_id,
            event_type,
            payload,
            now,
        ),
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


def _event_filters(
    merchant_id: int,
    *,
    event_type: str | None = None,
    q: str | None = None,
    proposal_id: int | None = None,
    conversation_id: int | None = None,
) -> list:
    filters = [AuditEvent.merchant_id == merchant_id]
    if event_type:
        filters.append(AuditEvent.event_type == event_type)
    if proposal_id is not None:
        filters.append(AuditEvent.proposal_id == proposal_id)
    if conversation_id is not None:
        filters.append(AuditEvent.conversation_id == conversation_id)
    if q and q.strip():
        escaped = (
            q.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                cast(AuditEvent.payload_json, String).ilike(pattern, escape="\\"),
                AuditEvent.event_type.ilike(pattern, escape="\\"),
            )
        )
    return filters


def list_events(
    session: Session,
    merchant_id: int,
    *,
    event_type: str | None = None,
    q: str | None = None,
    proposal_id: int | None = None,
    conversation_id: int | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> tuple[list[AuditEvent], bool]:
    stmt = select(AuditEvent).where(
        *_event_filters(
            merchant_id,
            event_type=event_type,
            q=q,
            proposal_id=proposal_id,
            conversation_id=conversation_id,
        )
    )
    if before_id is not None:
        stmt = stmt.where(AuditEvent.id < before_id)
    rows = session.execute(
        stmt.order_by(AuditEvent.id.desc()).limit(limit + 1)
    ).scalars().all()
    return rows[:limit], len(rows) > limit


def list_event_types(session: Session, merchant_id: int) -> list[str]:
    return list(
        session.execute(
            select(AuditEvent.event_type)
            .where(AuditEvent.merchant_id == merchant_id)
            .distinct()
            .order_by(AuditEvent.event_type)
        ).scalars().all()
    )


def count_events(
    session: Session,
    merchant_id: int,
    *,
    event_type: str | None = None,
    q: str | None = None,
    proposal_id: int | None = None,
    conversation_id: int | None = None,
) -> int:
    stmt = select(func.count(AuditEvent.id)).where(
        *_event_filters(
            merchant_id,
            event_type=event_type,
            q=q,
            proposal_id=proposal_id,
            conversation_id=conversation_id,
        )
    )
    return int(session.execute(stmt).scalar_one())


def verify_chain_report(
    session: Session, merchant_id: int
) -> tuple[bool, int | str, int]:
    prev_hash = "GENESIS"
    checked = 0
    events = session.execute(
        select(AuditEvent)
        .where(AuditEvent.merchant_id == merchant_id)
        .order_by(AuditEvent.id)
    ).scalars().all()
    for event in events:
        checked += 1
        expected = _event_hash(
            prev_hash,
            event.merchant_id,
            event.conversation_id,
            event.proposal_id,
            event.event_type,
            event.payload_json,
            event.created_at,
        )
        if event.prev_hash != prev_hash or event.row_hash != expected:
            return False, event.id, checked
        prev_hash = event.row_hash
    return True, "ok", checked


def verify_chain(session: Session, merchant_id: int) -> tuple[bool, int | str]:
    valid, broken, _ = verify_chain_report(session, merchant_id)
    return valid, broken
