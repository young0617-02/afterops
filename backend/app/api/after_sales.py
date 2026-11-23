from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    HandoffTicket,
    Order,
    SatisfactionRating,
)
from app.domain.money import format_cents
from app.services.after_sales_timeline import customer_after_sales_timeline
from app.services.proposals import add_proposal_attachments, withdraw_proposal
from app.services.satisfaction import rate_handoff

router = APIRouter(prefix="/api/store", tags=["after-sales"])


class AfterSalesItemOut(BaseModel):
    id: int
    action_type: str
    status: str
    order_no: str
    amount_cents: int | None
    amount_display: str | None
    reason: str
    message: str | None
    commerce_ref: str | None
    created_at: datetime
    approved_at: datetime | None
    executed_at: datetime | None
    attachment_ids: list[int]


class ProposalAttachmentsIn(BaseModel):
    attachment_ids: list[int] = Field(min_length=1, max_length=3)


class ProposalAttachmentsOut(BaseModel):
    id: int
    attachment_ids: list[int]


class WithdrawOut(BaseModel):
    id: int
    status: str


class CustomerHandoffOut(BaseModel):
    id: int
    ticket_ref: str
    queue: str
    status: str
    intent: str
    last_user_message: str
    last_reply: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    rating_score: int | None
    rating_comment: str | None


class HandoffRatingIn(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class HandoffRatingOut(BaseModel):
    ticket_id: int
    ticket_ref: str
    score: int
    comment: str
    follow_up_ticket_ref: str | None
    created_at: datetime


class AfterSalesTimelineEventOut(BaseModel):
    kind: str
    occurred_at: datetime
    title: str
    detail: str
    status: str
    order_no: str | None
    entity_id: int | None


@router.get(
    "/after-sales/timeline",
    response_model=list[AfterSalesTimelineEventOut],
)
def customer_after_sales_timeline_endpoint(
    customer: CurrentCustomer,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AfterSalesTimelineEventOut]:
    return [
        AfterSalesTimelineEventOut(
            kind=event.kind,
            occurred_at=event.occurred_at,
            title=event.title,
            detail=event.detail,
            status=event.status,
            order_no=event.order_no,
            entity_id=event.entity_id,
        )
        for event in customer_after_sales_timeline(
            session, customer, limit=limit
        )
    ]


@router.get("/after-sales", response_model=list[AfterSalesItemOut])
def list_after_sales(
    customer: CurrentCustomer, session: SessionDep
) -> list[AfterSalesItemOut]:
    rows = session.execute(
        select(ActionProposal, AfterSalesCase, Order)
        .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
        .join(Order, Order.id == AfterSalesCase.order_id)
        .where(
            ActionProposal.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
        .order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc())
        .limit(100)
    ).all()
    items: list[AfterSalesItemOut] = []
    for proposal, case, order in rows:
        payload = proposal.payload_json or {}
        amount_cents = payload.get("amount_cents")
        if amount_cents is None:
            amount_cents = case.amount_cents
        message = None
        if proposal.status == "rejected":
            message = proposal.error or "申请未通过"
        elif proposal.status == "failed":
            message = "处理失败，请联系人工"
        items.append(
            AfterSalesItemOut(
                id=proposal.id,
                action_type=proposal.action_type,
                status=proposal.status,
                order_no=order.order_no,
                amount_cents=int(amount_cents) if amount_cents is not None else None,
                amount_display=(
                    format_cents(int(amount_cents))
                    if amount_cents is not None
                    else None
                ),
                reason=str(payload.get("reason") or case.reason or ""),
                message=message,
                commerce_ref=proposal.commerce_ref,
                created_at=proposal.created_at,
                approved_at=proposal.approved_at,
                executed_at=proposal.executed_at,
                attachment_ids=payload.get("attachment_ids") or [],
            )
        )
    return items


@router.post(
    "/after-sales/{proposal_id}/attachments",
    response_model=ProposalAttachmentsOut,
)
def attach_after_sales_evidence(
    proposal_id: int,
    payload: ProposalAttachmentsIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ProposalAttachmentsOut:
    return ProposalAttachmentsOut(
        **add_proposal_attachments(
            session, customer, proposal_id, payload.attachment_ids
        )
    )


@router.post("/after-sales/{proposal_id}/withdraw", response_model=WithdrawOut)
def withdraw_after_sales(
    proposal_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> WithdrawOut:
    result = withdraw_proposal(session, customer, proposal_id)
    return WithdrawOut(**result)


@router.get("/handoffs", response_model=list[CustomerHandoffOut])
def list_customer_handoffs(
    customer: CurrentCustomer, session: SessionDep
) -> list[CustomerHandoffOut]:
    tickets = session.execute(
        select(HandoffTicket)
        .where(
            HandoffTicket.merchant_id == customer.merchant_id,
            HandoffTicket.customer_id == customer.id,
        )
        .order_by(HandoffTicket.updated_at.desc(), HandoffTicket.id.desc())
        .limit(50)
    ).scalars().all()
    ratings = {
        rating.handoff_ticket_id: rating
        for rating in session.execute(
            select(SatisfactionRating).where(
                SatisfactionRating.handoff_ticket_id.in_(
                    [ticket.id for ticket in tickets]
                )
            )
        ).scalars().all()
    } if tickets else {}
    return [
        CustomerHandoffOut(
            id=ticket.id,
            ticket_ref=f"TK-{ticket.id:06d}",
            queue=ticket.queue,
            status=ticket.status,
            intent=ticket.intent,
            last_user_message=ticket.last_user_message,
            last_reply=ticket.last_reply,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            resolved_at=ticket.resolved_at,
            rating_score=(
                ratings[ticket.id].score if ticket.id in ratings else None
            ),
            rating_comment=(
                ratings[ticket.id].comment if ticket.id in ratings else None
            ),
        )
        for ticket in tickets
    ]


@router.post(
    "/handoffs/{ticket_id}/rating", response_model=HandoffRatingOut
)
def rate_customer_handoff(
    ticket_id: int,
    payload: HandoffRatingIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> HandoffRatingOut:
    rating = rate_handoff(
        session, customer, ticket_id, payload.score, payload.comment
    )
    return HandoffRatingOut(
        ticket_id=rating.ticket_id,
        ticket_ref=rating.ticket_ref,
        score=rating.score,
        comment=rating.comment,
        follow_up_ticket_ref=rating.follow_up_ticket_ref,
        created_at=rating.created_at,
    )
