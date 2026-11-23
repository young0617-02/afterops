from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Conversation,
    Customer,
    HandoffTicket,
    SatisfactionRating,
    User,
)
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.handoffs import ticket_ref
from app.services.notifications import notify_staff


@dataclass(frozen=True)
class SatisfactionView:
    id: int
    ticket_id: int
    ticket_ref: str
    customer_phone: str | None
    agent_email: str | None
    score: int
    comment: str
    follow_up_ticket_ref: str | None
    created_at: datetime


def rate_handoff(
    session: Session,
    customer: Customer,
    ticket_id: int,
    score: int,
    comment: str,
) -> SatisfactionView:
    if score < 1 or score > 5:
        raise ValidationError("评分需在 1–5 星之间")
    comment_text = comment.strip()
    if len(comment_text) > 500:
        raise ValidationError("评价内容不能超过 500 个字符")
    try:
        ticket = session.execute(
            select(HandoffTicket)
            .where(
                HandoffTicket.id == ticket_id,
                HandoffTicket.merchant_id == customer.merchant_id,
                HandoffTicket.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if ticket is None:
            raise NotFoundError("人工工单不存在")
        if ticket.status != "resolved" or ticket.resolved_at is None:
            raise ConflictError("只有已解决的工单可以评价")
        existing = session.execute(
            select(SatisfactionRating.id).where(
                SatisfactionRating.handoff_ticket_id == ticket.id
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("该工单已评价")
        rating = SatisfactionRating(
            merchant_id=customer.merchant_id,
            customer_id=customer.id,
            handoff_ticket_id=ticket.id,
            score=score,
            comment=comment_text,
        )
        session.add(rating)
        session.flush()
        agent = (
            session.get(User, ticket.resolved_by)
            if ticket.resolved_by is not None
            else None
        )
        append_event(
            session,
            customer.merchant_id,
            "handoff_rated",
            {
                "ticket_id": ticket.id,
                "score": score,
                "comment": comment_text,
                "resolved_by_user_id": ticket.resolved_by,
            },
        )
        if score <= 2:
            append_event(
                session,
                customer.merchant_id,
                "low_csat_alert",
                {
                    "ticket_id": ticket.id,
                    "score": score,
                    "comment": comment_text,
                    "resolved_by_user_id": ticket.resolved_by,
                },
            )
            notify_staff(
                session,
                customer.merchant_id,
                notification_type="low_csat_alert",
                title=f"低分评价 {ticket_ref(ticket.id)}",
                body=(
                    f"工单 {ticket_ref(ticket.id)} · {score} 星 · "
                    f"{comment_text or '未填写原因'}"
                ),
                link="/admin",
            )
        follow_up_ref: str | None = None
        if score <= 2:
            active = session.execute(
                select(HandoffTicket.id).where(
                    HandoffTicket.merchant_id == customer.merchant_id,
                    HandoffTicket.conversation_id == ticket.conversation_id,
                    HandoffTicket.status.in_(("open", "claimed")),
                )
            ).scalar_one_or_none()
            if active is None:
                follow_up = HandoffTicket(
                    merchant_id=customer.merchant_id,
                    conversation_id=ticket.conversation_id,
                    customer_id=customer.id,
                    queue="complaint",
                    status="open",
                    priority="high",
                    intent="LOW_CSAT",
                    last_user_message=(
                        f"低分回访：原工单 {ticket_ref(ticket.id)} · "
                        f"{score} 星 · {comment_text or '未填写原因'}"
                    ),
                    follow_up_for_ticket_id=ticket.id,
                )
                session.add(follow_up)
                session.flush()
                follow_up_ref = ticket_ref(follow_up.id)
                conversation = session.get(
                    Conversation, ticket.conversation_id
                )
                if conversation is not None:
                    conversation.status = "handoff"
                    conversation.updated_at = datetime.now(UTC)
                append_event(
                    session,
                    customer.merchant_id,
                    "low_csat_follow_up_created",
                    {
                        "ticket_id": ticket.id,
                        "follow_up_ticket_id": follow_up.id,
                        "follow_up_ticket_ref": follow_up_ref,
                        "rating_id": rating.id,
                        "score": score,
                    },
                    conversation_id=ticket.conversation_id,
                )
        session.commit()
        return SatisfactionView(
            id=rating.id,
            ticket_id=ticket.id,
            ticket_ref=ticket_ref(ticket.id),
            customer_phone=customer.phone,
            agent_email=agent.email if agent else None,
            score=score,
            comment=comment_text,
            follow_up_ticket_ref=follow_up_ref,
            created_at=rating.created_at,
        )
    except Exception:
        session.rollback()
        raise
