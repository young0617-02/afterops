from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.domain.models import Outbox
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/api/admin", tags=["admin"])


class OutboxEventOut(BaseModel):
    id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    to: str
    subject: str
    status: str
    attempts: int
    last_error: str
    available_at: datetime
    created_at: datetime
    processed_at: datetime | None


class OutboxListOut(BaseModel):
    items: list[OutboxEventOut]
    counts: dict[str, int]


class OutboxRetryOut(BaseModel):
    id: int
    status: str
    attempts: int


def _out(row: Outbox) -> OutboxEventOut:
    payload = row.payload_json or {}
    return OutboxEventOut(
        id=row.id,
        event_type=row.event_type,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        to=str(payload.get("to", "")),
        subject=str(payload.get("subject", "")),
        status=row.status,
        attempts=row.attempts,
        last_error=row.last_error,
        available_at=row.available_at,
        created_at=row.created_at,
        processed_at=row.processed_at,
    )


@router.get("/outbox", response_model=OutboxListOut)
def admin_outbox(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[
        Literal["failed", "pending", "processing", "processed", "all"],
        Query(),
    ] = "failed",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> OutboxListOut:
    stmt = select(Outbox).where(Outbox.merchant_id == admin.merchant_id)
    if status != "all":
        stmt = stmt.where(Outbox.status == status)
    rows = session.execute(
        stmt.order_by(Outbox.id.desc()).limit(limit)
    ).scalars().all()
    counts = {
        "pending": 0,
        "processing": 0,
        "processed": 0,
        "failed": 0,
    }
    for row_status, count in session.execute(
        select(Outbox.status, func.count(Outbox.id))
        .where(Outbox.merchant_id == admin.merchant_id)
        .group_by(Outbox.status)
    ).all():
        if row_status in counts:
            counts[row_status] = int(count)
    return OutboxListOut(
        items=[_out(row) for row in rows],
        counts=counts,
    )


@router.post("/outbox/{outbox_id}/retry", response_model=OutboxRetryOut)
def admin_outbox_retry(
    outbox_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> OutboxRetryOut:
    try:
        row = session.execute(
            select(Outbox)
            .where(
                Outbox.id == outbox_id,
                Outbox.merchant_id == admin.merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("投递事件不存在")
        if row.status == "processed":
            raise ConflictError("已投递事件不能重试")
        if row.status == "processing":
            raise ConflictError("事件正在投递，不能重试")
        row.status = "pending"
        row.attempts = 0
        row.available_at = datetime.now(UTC)
        row.processed_at = None
        append_event(
            session,
            admin.merchant_id,
            "outbox_retry_requested",
            {
                "outbox_id": row.id,
                "event_type": row.event_type,
                "operator_user_id": admin.user_id,
            },
        )
        session.commit()
        return OutboxRetryOut(
            id=row.id, status=row.status, attempts=row.attempts
        )
    except Exception:
        session.rollback()
        raise
