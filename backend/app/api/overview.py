from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.admin import AdminDep
from app.api.deps import SessionDep
from app.domain.models import (
    ActionProposal,
    AuditEvent,
    HandoffTicket,
    Notification,
    Order,
    Payment,
    Refund,
    ReturnRequest,
)
from app.domain.money import format_cents
from app.services.audit import verify_chain_report

router = APIRouter(prefix="/api/admin", tags=["admin"])


class TrendDayOut(BaseModel):
    date: str
    paid_orders: int
    paid_amount_cents: int
    paid_amount_display: str
    refunds: int
    refund_amount_cents: int
    refund_amount_display: str
    proposals: int
    handoffs: int
    ai_chats: int


class OverviewOut(BaseModel):
    generated_at: datetime
    window_hours: int
    pending_proposals: int
    pending_handoffs: int
    claimed_handoffs: int
    urgent_handoffs: int
    unfulfilled_orders: int
    pending_returns: int
    unread_notifications: int
    paid_orders: int
    paid_amount_cents: int
    paid_amount_display: str
    refunds: int
    refund_amount_cents: int
    refund_amount_display: str
    proposals_created: int
    proposals_executed: int
    proposals_rejected: int
    handoffs_created: int
    handoffs_resolved: int
    ai_chats: int
    approval_median_seconds: int | None
    audit_valid: bool
    audit_checked_events: int
    audit_broken_at: int | None


def _count(session, stmt) -> int:
    return int(session.execute(stmt).scalar_one())


@router.get("/overview/trend", response_model=list[TrendDayOut])
def admin_overview_trend(
    admin: AdminDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
) -> list[TrendDayOut]:
    now = datetime.now(UTC)
    start_date = (now - timedelta(days=days - 1)).date()
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
    merchant_id = admin.merchant_id
    buckets = {
        (start_date + timedelta(days=offset)).isoformat(): {
            "paid_orders": 0,
            "paid_amount_cents": 0,
            "refunds": 0,
            "refund_amount_cents": 0,
            "proposals": 0,
            "handoffs": 0,
            "ai_chats": 0,
        }
        for offset in range(days)
    }

    for paid_at, amount in session.execute(
        select(Payment.paid_at, Payment.amount_cents).where(
            Payment.merchant_id == merchant_id,
            Payment.paid_at.is_not(None),
            Payment.paid_at >= start,
        )
    ).all():
        key = paid_at.date().isoformat()
        if key in buckets:
            buckets[key]["paid_orders"] += 1
            buckets[key]["paid_amount_cents"] += int(amount)
    for executed_at, amount in session.execute(
        select(Refund.executed_at, Refund.amount_cents).where(
            Refund.merchant_id == merchant_id,
            Refund.status == "executed",
            Refund.executed_at.is_not(None),
            Refund.executed_at >= start,
        )
    ).all():
        key = executed_at.date().isoformat()
        if key in buckets:
            buckets[key]["refunds"] += 1
            buckets[key]["refund_amount_cents"] += int(amount)
    for created_at, in session.execute(
        select(ActionProposal.created_at).where(
            ActionProposal.merchant_id == merchant_id,
            ActionProposal.created_at >= start,
        )
    ).all():
        key = created_at.date().isoformat()
        if key in buckets:
            buckets[key]["proposals"] += 1
    for created_at, in session.execute(
        select(HandoffTicket.created_at).where(
            HandoffTicket.merchant_id == merchant_id,
            HandoffTicket.created_at >= start,
        )
    ).all():
        key = created_at.date().isoformat()
        if key in buckets:
            buckets[key]["handoffs"] += 1
    for created_at, in session.execute(
        select(AuditEvent.created_at).where(
            AuditEvent.merchant_id == merchant_id,
            AuditEvent.event_type == "chat_answered",
            AuditEvent.created_at >= start,
        )
    ).all():
        key = created_at.date().isoformat()
        if key in buckets:
            buckets[key]["ai_chats"] += 1

    return [
        TrendDayOut(
            date=day,
            paid_orders=item["paid_orders"],
            paid_amount_cents=item["paid_amount_cents"],
            paid_amount_display=format_cents(item["paid_amount_cents"]),
            refunds=item["refunds"],
            refund_amount_cents=item["refund_amount_cents"],
            refund_amount_display=format_cents(item["refund_amount_cents"]),
            proposals=item["proposals"],
            handoffs=item["handoffs"],
            ai_chats=item["ai_chats"],
        )
        for day, item in buckets.items()
    ]


@router.get("/overview", response_model=OverviewOut)
def admin_overview(
    admin: AdminDep,
    session: SessionDep,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> OverviewOut:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=hours)
    merchant_id = admin.merchant_id

    pending_proposals = _count(
        session,
        select(func.count(ActionProposal.id)).where(
            ActionProposal.merchant_id == merchant_id,
            ActionProposal.status == "pending",
        ),
    )
    pending_handoffs = _count(
        session,
        select(func.count(HandoffTicket.id)).where(
            HandoffTicket.merchant_id == merchant_id,
            HandoffTicket.status == "open",
        ),
    )
    claimed_handoffs = _count(
        session,
        select(func.count(HandoffTicket.id)).where(
            HandoffTicket.merchant_id == merchant_id,
            HandoffTicket.status == "claimed",
        ),
    )
    urgent_handoffs = _count(
        session,
        select(func.count(HandoffTicket.id)).where(
            HandoffTicket.merchant_id == merchant_id,
            HandoffTicket.status.in_(("open", "claimed")),
            HandoffTicket.priority == "urgent",
        ),
    )
    unfulfilled_orders = _count(
        session,
        select(func.count(Order.id)).where(
            Order.merchant_id == merchant_id,
            Order.payment_status.in_(("paid", "partially_refunded")),
            Order.fulfillment_status.in_(("unfulfilled", "allocated")),
            Order.status.notin_(("cancelled", "refunded")),
        ),
    )
    pending_returns = _count(
        session,
        select(func.count(ReturnRequest.id)).where(
            ReturnRequest.merchant_id == merchant_id,
            ReturnRequest.status.in_(("requested", "approved", "in_transit")),
        ),
    )
    unread_notifications = _count(
        session,
        select(func.count(Notification.id)).where(
            Notification.merchant_id == merchant_id,
            Notification.recipient_type == "user",
            Notification.user_id == admin.user_id,
            Notification.read_at.is_(None),
        ),
    )

    paid_row = session.execute(
        select(
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount_cents), 0),
        ).where(
            Payment.merchant_id == merchant_id,
            Payment.paid_at.is_not(None),
            Payment.paid_at >= cutoff,
        )
    ).one()
    paid_orders = int(paid_row[0])
    paid_amount_cents = int(paid_row[1])

    refund_row = session.execute(
        select(
            func.count(Refund.id),
            func.coalesce(func.sum(Refund.amount_cents), 0),
        ).where(
            Refund.merchant_id == merchant_id,
            Refund.status == "executed",
            Refund.executed_at.is_not(None),
            Refund.executed_at >= cutoff,
        )
    ).one()
    refunds = int(refund_row[0])
    refund_amount_cents = int(refund_row[1])

    proposals_created = _count(
        session,
        select(func.count(ActionProposal.id)).where(
            ActionProposal.merchant_id == merchant_id,
            ActionProposal.created_at >= cutoff,
        ),
    )
    proposals_executed = _count(
        session,
        select(func.count(ActionProposal.id)).where(
            ActionProposal.merchant_id == merchant_id,
            ActionProposal.status == "executed",
            ActionProposal.executed_at.is_not(None),
            ActionProposal.executed_at >= cutoff,
        ),
    )
    proposals_rejected = _count(
        session,
        select(func.count(ActionProposal.id)).where(
            ActionProposal.merchant_id == merchant_id,
            ActionProposal.status == "rejected",
            ActionProposal.approved_at.is_not(None),
            ActionProposal.approved_at >= cutoff,
        ),
    )
    handoffs_created = _count(
        session,
        select(func.count(HandoffTicket.id)).where(
            HandoffTicket.merchant_id == merchant_id,
            HandoffTicket.created_at >= cutoff,
        ),
    )
    handoffs_resolved = _count(
        session,
        select(func.count(HandoffTicket.id)).where(
            HandoffTicket.merchant_id == merchant_id,
            HandoffTicket.status == "resolved",
            HandoffTicket.resolved_at.is_not(None),
            HandoffTicket.resolved_at >= cutoff,
        ),
    )
    ai_chats = _count(
        session,
        select(func.count(AuditEvent.id)).where(
            AuditEvent.merchant_id == merchant_id,
            AuditEvent.event_type == "chat_answered",
            AuditEvent.created_at >= cutoff,
        ),
    )

    approval_rows = session.execute(
        select(ActionProposal.created_at, ActionProposal.approved_at).where(
            ActionProposal.merchant_id == merchant_id,
            ActionProposal.status.in_(("executed", "rejected")),
            ActionProposal.approved_at.is_not(None),
            ActionProposal.approved_at >= cutoff,
        )
    ).all()
    intervals = [
        (approved - created).total_seconds()
        for created, approved in approval_rows
        if approved >= created
    ]
    approval_median_seconds = (
        round(statistics.median(intervals)) if intervals else None
    )

    audit_valid, broken, checked = verify_chain_report(session, merchant_id)

    return OverviewOut(
        generated_at=now,
        window_hours=hours,
        pending_proposals=pending_proposals,
        pending_handoffs=pending_handoffs,
        claimed_handoffs=claimed_handoffs,
        urgent_handoffs=urgent_handoffs,
        unfulfilled_orders=unfulfilled_orders,
        pending_returns=pending_returns,
        unread_notifications=unread_notifications,
        paid_orders=paid_orders,
        paid_amount_cents=paid_amount_cents,
        paid_amount_display=format_cents(paid_amount_cents),
        refunds=refunds,
        refund_amount_cents=refund_amount_cents,
        refund_amount_display=format_cents(refund_amount_cents),
        proposals_created=proposals_created,
        proposals_executed=proposals_executed,
        proposals_rejected=proposals_rejected,
        handoffs_created=handoffs_created,
        handoffs_resolved=handoffs_resolved,
        ai_chats=ai_chats,
        approval_median_seconds=approval_median_seconds,
        audit_valid=audit_valid,
        audit_checked_events=checked,
        audit_broken_at=broken if isinstance(broken, int) else None,
    )
