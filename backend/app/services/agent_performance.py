from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    AuditEvent,
    Customer,
    HandoffTicket,
    SatisfactionRating,
    User,
)
from app.services.handoffs import ticket_ref
from app.settings import settings


@dataclass(frozen=True)
class AgentPerformanceRow:
    user_id: int
    email: str
    role: str
    status: str
    open_tickets: int
    claimed: int
    resolved: int
    avg_claim_minutes: float | None
    avg_resolution_minutes: float | None
    first_responses: int
    avg_first_response_minutes: float | None
    sla_breaches: int
    resolution_sla_breaches: int
    first_response_sla_breaches: int
    csat_count: int
    csat_average: float | None


@dataclass(frozen=True)
class RecentSatisfaction:
    ticket_id: int
    ticket_ref: str
    customer_phone: str | None
    agent_email: str | None
    score: int
    comment: str
    created_at: datetime


@dataclass(frozen=True)
class AgentPerformance:
    hours: int
    since: datetime
    unassigned_open: int
    csat_count: int
    csat_average: float | None
    agents: tuple[AgentPerformanceRow, ...]
    recent_ratings: tuple[RecentSatisfaction, ...]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _minutes(start: datetime, end: datetime) -> float:
    return max(0.0, (_as_utc(end) - _as_utc(start)).total_seconds() / 60)


def _sla_threshold(priority: str) -> int:
    if priority == "urgent":
        return settings.handoff_sla_urgent_minutes
    if priority == "high":
        return settings.handoff_sla_high_minutes
    return settings.handoff_sla_minutes


def _first_responses(
    session: Session, merchant_id: int
) -> dict[int, tuple[datetime, int]]:
    events = session.execute(
        select(AuditEvent).where(
            AuditEvent.merchant_id == merchant_id,
            AuditEvent.event_type == "handoff_agent_replied",
        )
    ).scalars().all()
    first: dict[int, tuple[datetime, int]] = {}
    for event in events:
        payload = event.payload_json or {}
        ticket_id = payload.get("ticket_id")
        user_id = payload.get("operator_user_id")
        if not isinstance(ticket_id, int) or not isinstance(user_id, int):
            continue
        at = _as_utc(event.created_at)
        current = first.get(ticket_id)
        if current is None or at < current[0]:
            first[ticket_id] = (at, user_id)
    return first


def agent_performance(
    session: Session, merchant_id: int, hours: int = 168
) -> AgentPerformance:
    since = datetime.now(UTC) - timedelta(hours=hours)
    tickets = session.execute(
        select(HandoffTicket).where(HandoffTicket.merchant_id == merchant_id)
    ).scalars().all()
    users = session.execute(
        select(User).where(User.merchant_id == merchant_id)
    ).scalars().all()
    users = sorted(
        users, key=lambda user: (0 if user.role == "owner" else 1, user.email)
    )
    ticket_by_id = {ticket.id: ticket for ticket in tickets}
    user_by_id = {user.id: user for user in users}
    first_responses = _first_responses(session, merchant_id)
    ratings = [
        rating
        for rating in session.execute(
            select(SatisfactionRating).where(
                SatisfactionRating.merchant_id == merchant_id
            )
        ).scalars().all()
        if _as_utc(rating.created_at) >= since
    ]
    ratings.sort(key=lambda rating: rating.created_at, reverse=True)
    customer_ids = {rating.customer_id for rating in ratings}
    customers = (
        {
            customer.id: customer
            for customer in session.execute(
                select(Customer).where(Customer.id.in_(customer_ids))
            ).scalars().all()
        }
        if customer_ids
        else {}
    )

    rows: list[AgentPerformanceRow] = []
    for user in users:
        claimed = [
            ticket
            for ticket in tickets
            if ticket.claimed_by == user.id
            and ticket.claimed_at is not None
            and _as_utc(ticket.claimed_at) >= since
        ]
        resolved = [
            ticket
            for ticket in tickets
            if ticket.resolved_by == user.id
            and ticket.resolved_at is not None
            and _as_utc(ticket.resolved_at) >= since
        ]
        open_tickets = sum(
            1
            for ticket in tickets
            if ticket.claimed_by == user.id
            and ticket.status in ("open", "claimed")
        )
        claim_minutes = [
            _minutes(ticket.created_at, ticket.claimed_at)
            for ticket in claimed
        ]
        resolution_minutes = [
            _minutes(ticket.created_at, ticket.resolved_at)
            for ticket in resolved
        ]
        user_first = [
            (ticket, first_responses[ticket.id][0])
            for ticket in tickets
            if ticket.id in first_responses
            and first_responses[ticket.id][1] == user.id
            and first_responses[ticket.id][0] >= since
        ]
        first_minutes = [
            _minutes(ticket.created_at, first_at)
            for ticket, first_at in user_first
        ]
        user_ratings = [
            rating
            for rating in ratings
            if ticket_by_id.get(rating.handoff_ticket_id) is not None
            and ticket_by_id[rating.handoff_ticket_id].resolved_by == user.id
        ]
        rows.append(
            AgentPerformanceRow(
                user_id=user.id,
                email=user.email,
                role=user.role,
                status=user.status,
                open_tickets=open_tickets,
                claimed=len(claimed),
                resolved=len(resolved),
                avg_claim_minutes=(
                    round(statistics.fmean(claim_minutes), 1)
                    if claim_minutes
                    else None
                ),
                avg_resolution_minutes=(
                    round(statistics.fmean(resolution_minutes), 1)
                    if resolution_minutes
                    else None
                ),
                first_responses=len(user_first),
                avg_first_response_minutes=(
                    round(statistics.fmean(first_minutes), 1)
                    if first_minutes
                    else None
                ),
                sla_breaches=sum(
                    1
                    for ticket in claimed
                    if _minutes(ticket.created_at, ticket.claimed_at)
                    > _sla_threshold(ticket.priority)
                ),
                resolution_sla_breaches=sum(
                    1
                    for ticket in resolved
                    if _minutes(ticket.created_at, ticket.resolved_at)
                    > settings.handoff_resolution_sla_hours * 60
                ),
                first_response_sla_breaches=sum(
                    1
                    for ticket in claimed
                    if ticket.id not in first_responses
                    or _minutes(
                        ticket.created_at, first_responses[ticket.id][0]
                    )
                    > settings.handoff_first_response_minutes
                ),
                csat_count=len(user_ratings),
                csat_average=(
                    round(
                        statistics.fmean(
                            rating.score for rating in user_ratings
                        ),
                        1,
                    )
                    if user_ratings
                    else None
                ),
            )
        )
    recent_ratings: list[RecentSatisfaction] = []
    for rating in ratings[:10]:
        ticket = ticket_by_id.get(rating.handoff_ticket_id)
        if ticket is None:
            continue
        agent = (
            user_by_id.get(ticket.resolved_by)
            if ticket.resolved_by is not None
            else None
        )
        customer = customers.get(rating.customer_id)
        recent_ratings.append(
            RecentSatisfaction(
                ticket_id=ticket.id,
                ticket_ref=ticket_ref(ticket.id),
                customer_phone=customer.phone if customer else None,
                agent_email=agent.email if agent else None,
                score=rating.score,
                comment=rating.comment,
                created_at=rating.created_at,
            )
        )
    return AgentPerformance(
        hours=hours,
        since=since,
        unassigned_open=sum(
            1 for ticket in tickets if ticket.status == "open"
        ),
        csat_count=len(ratings),
        csat_average=(
            round(statistics.fmean(rating.score for rating in ratings), 1)
            if ratings
            else None
        ),
        agents=tuple(rows),
        recent_ratings=tuple(recent_ratings),
    )
