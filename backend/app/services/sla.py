from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import ActionProposal, AuditEvent, HandoffTicket, User
from app.services.audit import append_event
from app.services.notifications import notify_staff, notify_user


def _reminded_ids(
    session: Session, event_type: str, field: str
) -> set[tuple[int, int]]:
    events = session.execute(
        select(AuditEvent).where(AuditEvent.event_type == event_type)
    ).scalars().all()
    return {
        (event.merchant_id, int(event.payload_json.get(field)))
        for event in events
        if event.payload_json.get(field) is not None
    }


def send_sla_reminders(
    session: Session,
    *,
    proposal_minutes: int = 30,
    handoff_minutes: int = 15,
    handoff_high_minutes: int = 10,
    handoff_urgent_minutes: int = 5,
    handoff_resolution_hours: int = 24,
    handoff_first_response_minutes: int = 30,
    handoff_escalation_minutes: int = 60,
) -> dict[str, int]:
    now = datetime.now(UTC)
    proposal_reminded = _reminded_ids(
        session, "proposal_sla_reminder", "proposal_id"
    )
    handoff_reminded = _reminded_ids(
        session, "handoff_sla_reminder", "ticket_id"
    )
    stats = {
        "proposals": 0,
        "handoffs": 0,
        "handoff_resolutions": 0,
        "handoff_first_responses": 0,
        "handoff_escalations": 0,
    }
    proposals = session.execute(
        select(ActionProposal).where(
            ActionProposal.status == "pending",
            ActionProposal.created_at
            <= now - timedelta(minutes=proposal_minutes),
        )
    ).scalars().all()
    for proposal in proposals:
        key = (proposal.merchant_id, proposal.id)
        if key in proposal_reminded:
            continue
        payload = proposal.payload_json or {}
        waited = int((now - proposal.created_at).total_seconds() // 60)
        notify_staff(
            session,
            proposal.merchant_id,
            notification_type="proposal_sla",
            title=f"待审批提案已超时 #{proposal.id}",
            body=(
                f"订单 {payload.get('order_no', '-')} 已等待 {waited} 分钟，"
                "请尽快处理"
            ),
            link="/admin",
        )
        append_event(
            session,
            proposal.merchant_id,
            "proposal_sla_reminder",
            {
                "proposal_id": proposal.id,
                "order_no": payload.get("order_no"),
                "waited_minutes": waited,
            },
            proposal_id=proposal.id,
        )
        stats["proposals"] += 1

    thresholds = {
        "urgent": handoff_urgent_minutes,
        "high": handoff_high_minutes,
        "normal": handoff_minutes,
    }
    minimum = min(thresholds.values())
    handoffs = session.execute(
        select(HandoffTicket).where(
            HandoffTicket.status == "open",
            HandoffTicket.created_at <= now - timedelta(minutes=minimum),
        )
    ).scalars().all()
    for ticket in handoffs:
        key = (ticket.merchant_id, ticket.id)
        if key in handoff_reminded:
            continue
        threshold = thresholds.get(ticket.priority, handoff_minutes)
        created_at = ticket.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at > now - timedelta(minutes=threshold):
            continue
        waited = int((now - created_at).total_seconds() // 60)
        notify_staff(
            session,
            ticket.merchant_id,
            notification_type="handoff_sla",
            title=f"人工工单待接管 {ticket.id:06d}",
            body=f"工单已等待 {waited} 分钟，请尽快接管",
            link="/admin",
        )
        append_event(
            session,
            ticket.merchant_id,
            "handoff_sla_reminder",
            {
                "ticket_id": ticket.id,
                "waited_minutes": waited,
                "priority": ticket.priority,
            },
        )
        stats["handoffs"] += 1

    resolution_reminded = _reminded_ids(
        session, "handoff_resolution_sla_reminder", "ticket_id"
    )
    resolution_cutoff = now - timedelta(hours=handoff_resolution_hours)
    claimed_tickets = session.execute(
        select(HandoffTicket).where(
            HandoffTicket.status == "claimed",
            HandoffTicket.claimed_at.is_not(None),
            HandoffTicket.claimed_at <= resolution_cutoff,
        )
    ).scalars().all()
    for ticket in claimed_tickets:
        key = (ticket.merchant_id, ticket.id)
        if key in resolution_reminded:
            continue
        claimed_at = ticket.claimed_at
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=UTC)
        waited_hours = int((now - claimed_at).total_seconds() // 3600)
        notify_staff(
            session,
            ticket.merchant_id,
            notification_type="handoff_resolution_sla",
            title=f"工单处理超时 {ticket.id:06d}",
            body=(
                f"工单已接管 {waited_hours} 小时仍未解决，"
                "请尽快处理或转交"
            ),
            link="/admin",
        )
        append_event(
            session,
            ticket.merchant_id,
            "handoff_resolution_sla_reminder",
            {
                "ticket_id": ticket.id,
                "waited_hours": waited_hours,
                "priority": ticket.priority,
            },
        )
        stats["handoff_resolutions"] += 1

    first_replied = _reminded_ids(
        session, "handoff_agent_replied", "ticket_id"
    )
    first_reminded = _reminded_ids(
        session, "handoff_first_response_sla_reminder", "ticket_id"
    )
    first_response_cutoff = now - timedelta(
        minutes=handoff_first_response_minutes
    )
    first_response_tickets = session.execute(
        select(HandoffTicket).where(
            HandoffTicket.status == "claimed",
            HandoffTicket.created_at <= first_response_cutoff,
        )
    ).scalars().all()
    assignee_ids = {
        ticket.claimed_by
        for ticket in first_response_tickets
        if ticket.claimed_by is not None
    }
    assignees = (
        {
            user.id: user
            for user in session.execute(
                select(User).where(User.id.in_(assignee_ids))
            ).scalars().all()
        }
        if assignee_ids
        else {}
    )
    for ticket in first_response_tickets:
        key = (ticket.merchant_id, ticket.id)
        if key in first_replied or key in first_reminded:
            continue
        assignee = (
            assignees.get(ticket.claimed_by)
            if ticket.claimed_by is not None
            else None
        )
        if assignee is None:
            continue
        created_at = ticket.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        waited = int((now - created_at).total_seconds() // 60)
        notify_user(
            session,
            ticket.merchant_id,
            assignee,
            notification_type="handoff_first_response_sla",
            title=f"工单首次响应超时 {ticket.id:06d}",
            body=(
                f"工单已创建 {waited} 分钟，尚未回复顾客，"
                "请尽快处理"
            ),
            link="/admin",
        )
        append_event(
            session,
            ticket.merchant_id,
            "handoff_first_response_sla_reminder",
            {
                "ticket_id": ticket.id,
                "waited_minutes": waited,
                "priority": ticket.priority,
                "user_id": assignee.id,
            },
        )
        stats["handoff_first_responses"] += 1

    escalated = _reminded_ids(session, "handoff_escalated", "ticket_id")
    escalation_cutoff = now - timedelta(minutes=handoff_escalation_minutes)
    escalation_tickets = session.execute(
        select(HandoffTicket).where(
            HandoffTicket.status == "claimed",
            HandoffTicket.created_at <= escalation_cutoff,
            HandoffTicket.escalated_at.is_(None),
        )
    ).scalars().all()
    owners_by_merchant: dict[int, list[User]] = {}
    for owner in session.execute(
        select(User).where(User.role == "owner", User.status == "active")
    ).scalars().all():
        owners_by_merchant.setdefault(owner.merchant_id, []).append(owner)
    for ticket in escalation_tickets:
        key = (ticket.merchant_id, ticket.id)
        if key in escalated or key in first_replied:
            continue
        owners = owners_by_merchant.get(ticket.merchant_id, [])
        if not owners:
            continue
        created_at = ticket.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        waited = int((now - created_at).total_seconds() // 60)
        previous_priority = ticket.priority
        if previous_priority == "normal":
            ticket.priority = "high"
        ticket.escalated_at = now
        for owner in owners:
            notify_user(
                session,
                ticket.merchant_id,
                owner,
                notification_type="handoff_first_response_escalation",
                title=f"工单首次响应超时升级 {ticket.id:06d}",
                body=(
                    f"工单已创建 {waited} 分钟仍无客服回复，"
                    "优先级已升级，请安排跟进"
                ),
                link="/admin",
            )
        append_event(
            session,
            ticket.merchant_id,
            "handoff_escalated",
            {
                "ticket_id": ticket.id,
                "waited_minutes": waited,
                "priority_from": previous_priority,
                "priority_to": ticket.priority,
                "assignee_user_id": ticket.claimed_by,
                "owner_user_ids": [owner.id for owner in owners],
            },
        )
        stats["handoff_escalations"] += 1

    if any(stats.values()):
        session.commit()
    return stats
