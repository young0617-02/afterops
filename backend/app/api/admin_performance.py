from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.admin import OwnerDep
from app.api.deps import SessionDep
from app.services.agent_performance import agent_performance

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AgentPerformanceRowOut(BaseModel):
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


class RecentSatisfactionOut(BaseModel):
    ticket_id: int
    ticket_ref: str
    customer_phone: str | None
    agent_email: str | None
    score: int
    comment: str
    created_at: datetime


class AgentPerformanceOut(BaseModel):
    hours: int
    since: datetime
    unassigned_open: int
    csat_count: int
    csat_average: float | None
    agents: list[AgentPerformanceRowOut]
    recent_ratings: list[RecentSatisfactionOut]


@router.get("/agent-performance", response_model=AgentPerformanceOut)
def admin_agent_performance(
    admin: OwnerDep,
    session: SessionDep,
    hours: Annotated[int, Query(ge=1, le=720)] = 168,
) -> AgentPerformanceOut:
    result = agent_performance(session, admin.merchant_id, hours)
    return AgentPerformanceOut(
        hours=result.hours,
        since=result.since,
        unassigned_open=result.unassigned_open,
        csat_count=result.csat_count,
        csat_average=result.csat_average,
        recent_ratings=[
            RecentSatisfactionOut(
                ticket_id=rating.ticket_id,
                ticket_ref=rating.ticket_ref,
                customer_phone=rating.customer_phone,
                agent_email=rating.agent_email,
                score=rating.score,
                comment=rating.comment,
                created_at=rating.created_at,
            )
            for rating in result.recent_ratings
        ],
        agents=[
            AgentPerformanceRowOut(
                user_id=row.user_id,
                email=row.email,
                role=row.role,
                status=row.status,
                open_tickets=row.open_tickets,
                claimed=row.claimed,
                resolved=row.resolved,
                avg_claim_minutes=row.avg_claim_minutes,
                avg_resolution_minutes=row.avg_resolution_minutes,
                first_responses=row.first_responses,
                avg_first_response_minutes=row.avg_first_response_minutes,
                sla_breaches=row.sla_breaches,
                resolution_sla_breaches=row.resolution_sla_breaches,
                first_response_sla_breaches=(
                    row.first_response_sla_breaches
                ),
                csat_count=row.csat_count,
                csat_average=row.csat_average,
            )
            for row in result.agents
        ],
    )
