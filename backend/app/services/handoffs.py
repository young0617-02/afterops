from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Attachment,
    Conversation,
    Customer,
    HandoffNote,
    HandoffTicket,
    Merchant,
    Message,
    User,
)
from app.services.attachments import (
    save_staff_attachment,
    validate_staff_attachment_ids,
)
from app.services.audit import append_event
from app.services.customer_directory import (
    CustomerDirectoryDetail,
    get_customer_directory,
)
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.notifications import (
    notify_customer,
    notify_staff,
    notify_user,
)
from app.settings import settings

ACTIVE_STATUSES = ("open", "claimed")
STATUS_LABELS = {
    "open": "待接管",
    "claimed": "处理中",
    "resolved": "已解决",
}
PRIORITY_RANK = {"normal": 0, "high": 1, "urgent": 2}
ORDER_NO_RE = re.compile(r"ORD-[A-Z0-9-]{6,}", re.IGNORECASE)
INTENT_SUMMARY_LABELS = {
    "HUMAN_REQUEST": "人工转接",
    "REFUND_STATUS": "退款进度",
    "REFUND_REQUEST": "退款申请",
    "COMPENSATION_REQUEST": "补偿申请",
    "ADDRESS_CHANGE": "改地址申请",
    "COMPLAINT": "投诉升级",
    "HIGH_RISK": "重大事项/承诺索取",
    "RETURN_REQUEST": "退货/换货申请",
    "INVOICE_STATUS": "发票进度",
    "REPAIR_STATUS": "维修进度",
    "RETURN_STATUS": "退货进度",
    "LOGISTICS_TRACK": "物流查询",
    "ORDER_STATUS": "订单查询",
    "RETURN_POLICY": "售后政策咨询",
    "PRODUCT_INFO": "商品咨询",
    "GREETING": "问候",
    "UNKNOWN": "未识别",
}
SUMMARY_SUGGESTIONS = {
    "REFUND_STATUS": (
        "核对退款账本、退款提案状态与金额，说明到账时间以支付渠道为准，"
        "不承诺具体到账时间。"
    ),
    "REFUND_REQUEST": (
        "核对订单支付与可退金额，按退款提案流程处理，不直接承诺退款结果。"
    ),
    "COMPENSATION_REQUEST": (
        "核对订单与售后事实，按补偿提案或人工审批处理，不承诺赔付金额。"
    ),
    "RETURN_REQUEST": "核对退货资格与订单状态，按退货/换货申请流程处理。",
    "RETURN_STATUS": (
        "核对退货单、回寄物流、收货与质检状态，再给出下一步说明。"
    ),
    "REPAIR_STATUS": "核对维修单状态与寄回运单，说明下一步处理安排。",
    "INVOICE_STATUS": (
        "核对发票申请、开具、作废状态与订单净支付，必要时说明重开流程。"
    ),
    "LOGISTICS_TRACK": "核对订单与运单节点，按实际物流状态回复。",
    "ORDER_STATUS": "核对订单支付与履约状态，按实际状态回复。",
    "ADDRESS_CHANGE": "确认订单是否可改地址，按改地址提案流程人工审批。",
    "COMPLAINT": "优先安抚并按投诉流程处理，避免任何赔付或结果承诺。",
    "HIGH_RISK": "按高风险流程处理，避免承诺，必要时升级店主。",
    "HUMAN_REQUEST": "先确认顾客具体诉求，再核对相关订单与售后事实。",
    "RETURN_POLICY": "引用现行售后政策条款，说明条件与限制，不代替审批。",
    "PRODUCT_INFO": "核对商品在售状态、价格与库存，再回复顾客。",
}
QUEUE_LABELS = {"after_sales": "售后", "complaint": "投诉"}
PRIORITY_LABELS = {"normal": "普通", "high": "高", "urgent": "紧急"}


def _truncate(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _summary_text(session: Session, ticket: HandoffTicket) -> str:
    messages = list(
        session.execute(
            select(Message)
            .where(Message.conversation_id == ticket.conversation_id)
            .order_by(Message.id.desc())
            .limit(30)
        ).scalars().all()
    )
    messages.reverse()
    total_messages = int(
        session.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == ticket.conversation_id
            )
        ).scalar_one()
    )
    user_messages = [
        message.content
        for message in messages
        if message.role == "user" and (message.content or "").strip()
    ]
    latest_reply = ticket.last_reply or next(
        (
            message.content
            for message in reversed(messages)
            if message.role in ("assistant", "agent")
            and (message.content or "").strip()
        ),
        "",
    )
    recent_user = " / ".join(
        _truncate(message, 90) for message in user_messages[-2:]
    )
    if not recent_user:
        recent_user = _truncate(ticket.last_user_message, 180) or "（无文字描述）"
    blob = " ".join(
        [ticket.last_user_message or "", ticket.last_reply or ""]
        + [message.content or "" for message in messages]
    )
    order_nos = list(
        dict.fromkeys(match.upper() for match in ORDER_NO_RE.findall(blob))
    )[:3]
    intent_label = INTENT_SUMMARY_LABELS.get(
        ticket.intent, ticket.intent or "未识别"
    )
    lines = [f"【诉求】{intent_label}"]
    if order_nos:
        lines.append(f"【订单】{'、'.join(order_nos)}")
    lines.append(f"【客户最近】{recent_user}")
    if latest_reply.strip():
        lines.append(f"【已回复】{_truncate(latest_reply, 180)}")
    lines.append(
        "【建议】"
        + SUMMARY_SUGGESTIONS.get(
            ticket.intent,
            "确认顾客具体诉求，核对订单与售后事实后再处理，不承诺未审批结果。",
        )
    )
    lines.append(
        f"【会话】{total_messages} 条消息 · "
        f"{QUEUE_LABELS.get(ticket.queue, ticket.queue)}队列 · "
        f"{PRIORITY_LABELS.get(ticket.priority, ticket.priority)}优先级"
    )
    return "\n".join(lines)


def _refresh_summary(session: Session, ticket: HandoffTicket) -> None:
    ticket.summary = _summary_text(session, ticket)
    ticket.summary_generated_at = datetime.now(UTC)


def _priority_for(queue: str, intent: str) -> str:
    if intent == "COMPLAINT":
        return "urgent"
    if intent == "HIGH_RISK" or queue == "complaint":
        return "high"
    return "normal"


def touch_active_ticket(
    session: Session,
    ticket: HandoffTicket,
    *,
    queue: str,
    intent: str,
    user_message: str,
) -> None:
    ticket.last_user_message = user_message
    if queue == "complaint":
        ticket.queue = "complaint"
    candidate = _priority_for(queue, intent)
    if PRIORITY_RANK[candidate] > PRIORITY_RANK.get(ticket.priority, 0):
        previous = ticket.priority
        ticket.priority = candidate
        append_event(
            session,
            ticket.merchant_id,
            "handoff_priority_escalated",
            {
                "ticket_id": ticket.id,
                "ticket_ref": ticket_ref(ticket.id),
                "from": previous,
                "to": candidate,
            },
            conversation_id=ticket.conversation_id,
        )


def ticket_ref(ticket_id: int) -> str:
    return f"TK-{ticket_id:06d}"


@dataclass(frozen=True)
class HandoffTicketView:
    id: int
    ticket_ref: str
    queue: str
    status: str
    priority: str
    intent: str
    last_user_message: str
    last_reply: str
    summary: str
    summary_generated_at: datetime | None
    customer_phone: str | None
    customer_name: str | None
    conversation_id: int
    claimed_by_user_id: int | None
    claimed_by_email: str | None
    claimed_at: datetime | None
    resolved_by_user_id: int | None
    resolved_by_email: str | None
    resolved_at: datetime | None
    resolution_note: str
    escalated_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class HandoffMessageView:
    id: int
    role: str
    content: str
    attachment_ids: tuple[int, ...]
    created_at: datetime


@dataclass(frozen=True)
class HandoffNoteView:
    id: int
    author_email: str | None
    content: str
    created_at: datetime


@dataclass(frozen=True)
class HandoffDetailView:
    ticket: HandoffTicketView
    messages: tuple[HandoffMessageView, ...]
    notes: tuple[HandoffNoteView, ...]
    customer_context: CustomerDirectoryDetail


@dataclass(frozen=True)
class HandoffActionResult:
    id: int
    ticket_ref: str
    status: str


def _users_by_id(session: Session, user_ids: set[int]) -> dict[int, User]:
    if not user_ids:
        return {}
    users = session.execute(
        select(User).where(User.id.in_(user_ids))
    ).scalars().all()
    return {user.id: user for user in users}


def _view(
    ticket: HandoffTicket,
    customer: Customer | None,
    users: dict[int, User],
) -> HandoffTicketView:
    claimed_by = users.get(ticket.claimed_by) if ticket.claimed_by else None
    resolved_by = users.get(ticket.resolved_by) if ticket.resolved_by else None
    return HandoffTicketView(
        id=ticket.id,
        ticket_ref=ticket_ref(ticket.id),
        queue=ticket.queue,
        status=ticket.status,
        priority=ticket.priority,
        intent=ticket.intent,
        last_user_message=ticket.last_user_message,
        last_reply=ticket.last_reply,
        summary=ticket.summary,
        summary_generated_at=ticket.summary_generated_at,
        customer_phone=customer.phone if customer else None,
        customer_name=customer.name if customer else None,
        conversation_id=ticket.conversation_id,
        claimed_by_user_id=ticket.claimed_by,
        claimed_by_email=claimed_by.email if claimed_by else None,
        claimed_at=ticket.claimed_at,
        resolved_by_user_id=ticket.resolved_by,
        resolved_by_email=resolved_by.email if resolved_by else None,
        resolved_at=ticket.resolved_at,
        resolution_note=ticket.resolution_note,
        escalated_at=ticket.escalated_at,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def open_or_touch_ticket(
    session: Session,
    conversation: Conversation,
    customer: Customer,
    *,
    queue: str,
    intent: str,
    user_message: str,
    reply: str,
    create: bool,
) -> HandoffTicket | None:
    session.execute(
        select(Conversation.id)
        .where(Conversation.id == conversation.id)
        .with_for_update()
    ).scalar_one()
    ticket = session.execute(
        select(HandoffTicket)
        .where(
            HandoffTicket.merchant_id == conversation.merchant_id,
            HandoffTicket.conversation_id == conversation.id,
            HandoffTicket.status.in_(ACTIVE_STATUSES),
        )
        .order_by(HandoffTicket.id.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if ticket is not None:
        if create:
            ticket.intent = intent
            if queue == "complaint":
                ticket.queue = "complaint"
            candidate = _priority_for(queue, intent)
            if PRIORITY_RANK[candidate] > PRIORITY_RANK.get(ticket.priority, 0):
                previous = ticket.priority
                ticket.priority = candidate
                append_event(
                    session,
                    ticket.merchant_id,
                    "handoff_priority_escalated",
                    {
                        "ticket_id": ticket.id,
                        "ticket_ref": ticket_ref(ticket.id),
                        "from": previous,
                        "to": candidate,
                    },
                    conversation_id=ticket.conversation_id,
                )
        ticket.last_user_message = user_message
        ticket.last_reply = reply
        _refresh_summary(session, ticket)
        return ticket
    if not create:
        return None

    ticket = HandoffTicket(
        merchant_id=conversation.merchant_id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        queue=queue,
        status="open",
        priority=_priority_for(queue, intent),
        intent=intent,
        last_user_message=user_message,
        last_reply=reply,
    )
    session.add(ticket)
    session.flush()
    _refresh_summary(session, ticket)
    append_event(
        session,
        conversation.merchant_id,
        "handoff_created",
        {
            "ticket_id": ticket.id,
            "ticket_ref": ticket_ref(ticket.id),
            "queue": queue,
            "priority": _priority_for(queue, intent),
            "intent": intent,
            "conversation_id": conversation.id,
        },
        conversation_id=conversation.id,
    )
    notify_staff(
        session,
        conversation.merchant_id,
        notification_type="handoff_created",
        title=f"新的转人工工单 {ticket_ref(ticket.id)}",
        body=f"{customer.phone or '顾客'} · {queue}队列",
        link="/admin",
    )
    auto_assign = session.execute(
        select(Merchant.auto_assign_handoffs).where(
            Merchant.id == conversation.merchant_id
        )
    ).scalar_one_or_none()
    if auto_assign:
        counts = {
            user_id: int(count)
            for user_id, count in session.execute(
                select(
                    HandoffTicket.claimed_by,
                    func.count(HandoffTicket.id),
                )
                .where(
                    HandoffTicket.merchant_id == conversation.merchant_id,
                    HandoffTicket.status.in_(ACTIVE_STATUSES),
                    HandoffTicket.claimed_by.is_not(None),
                )
                .group_by(HandoffTicket.claimed_by)
            ).all()
        }
        agents = session.execute(
            select(User)
            .where(
                User.merchant_id == conversation.merchant_id,
                User.role == "agent",
                User.status == "active",
                User.handoff_available.is_(True),
                User.last_seen_at.is_not(None),
                User.last_seen_at
                >= datetime.now(UTC)
                - timedelta(seconds=settings.agent_online_window_seconds),
            )
            .order_by(User.id)
        ).scalars().all()
        agents = [
            agent
            for agent in agents
            if not agent.handoff_skills or queue in agent.handoff_skills
        ]
        if agents:
            assignee = min(
                agents,
                key=lambda user: (counts.get(user.id, 0), user.id),
            )
            now = datetime.now(UTC)
            ticket.status = "claimed"
            ticket.claimed_by = assignee.id
            ticket.claimed_at = now
            append_event(
                session,
                conversation.merchant_id,
                "handoff_auto_assigned",
                {
                    "ticket_id": ticket.id,
                    "ticket_ref": ticket_ref(ticket.id),
                    "assignee_user_id": assignee.id,
                    "active_tickets": counts.get(assignee.id, 0),
                    "queue": queue,
                    "priority": ticket.priority,
                },
                conversation_id=conversation.id,
            )
            notify_user(
                session,
                conversation.merchant_id,
                assignee,
                notification_type="handoff_auto_assigned",
                title=f"工单 {ticket_ref(ticket.id)} 已自动分配给你",
                body=f"{queue}队列 · 当前处理中 {counts.get(assignee.id, 0)} 单",
                link="/admin",
            )
    return ticket


def refresh_ticket_summary(
    session: Session, merchant_id: int, ticket_id: int
) -> HandoffTicketView:
    try:
        ticket = session.execute(
            select(HandoffTicket)
            .where(
                HandoffTicket.id == ticket_id,
                HandoffTicket.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if ticket is None:
            raise NotFoundError("工单不存在")
        _refresh_summary(session, ticket)
        append_event(
            session,
            merchant_id,
            "handoff_summary_refreshed",
            {
                "ticket_id": ticket.id,
                "ticket_ref": ticket_ref(ticket.id),
                "message_count": int(
                    session.execute(
                        select(func.count(Message.id)).where(
                            Message.conversation_id == ticket.conversation_id
                        )
                    ).scalar_one()
                ),
            },
            conversation_id=ticket.conversation_id,
        )
        customer = session.get(Customer, ticket.customer_id)
        users = _users_by_id(
            session,
            {
                user_id
                for user_id in (ticket.claimed_by, ticket.resolved_by)
                if user_id is not None
            },
        )
        session.commit()
        return _view(ticket, customer, users)
    except Exception:
        session.rollback()
        raise


def list_tickets(
    session: Session,
    merchant_id: int,
    *,
    status: str = "active",
    priority: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> list[HandoffTicketView]:
    stmt = (
        select(HandoffTicket, Customer)
        .outerjoin(Customer, Customer.id == HandoffTicket.customer_id)
        .where(HandoffTicket.merchant_id == merchant_id)
    )
    if status == "active":
        stmt = stmt.where(HandoffTicket.status.in_(ACTIVE_STATUSES))
    elif status != "all":
        stmt = stmt.where(HandoffTicket.status == status)
    if priority:
        stmt = stmt.where(HandoffTicket.priority == priority)
    if q and q.strip():
        text = q.strip()
        pattern = f"%{text}%"
        conditions = [
            Customer.phone.ilike(pattern),
            Customer.name.ilike(pattern),
            HandoffTicket.last_user_message.ilike(pattern),
            HandoffTicket.last_reply.ilike(pattern),
        ]
        digits = "".join(char for char in text if char.isdigit())
        if digits and len(digits) <= 18:
            conditions.append(HandoffTicket.id == int(digits))
        stmt = stmt.where(or_(*conditions))
    priority_rank = case(
        (HandoffTicket.priority == "urgent", 2),
        (HandoffTicket.priority == "high", 1),
        else_=0,
    )
    rows = session.execute(
        stmt.order_by(
            priority_rank.desc(),
            HandoffTicket.updated_at.desc(),
            HandoffTicket.id.desc(),
        ).limit(limit)
    ).all()
    users = _users_by_id(
        session,
        {
            user_id
            for ticket, _ in rows
            for user_id in (ticket.claimed_by, ticket.resolved_by)
            if user_id is not None
        },
    )
    return [_view(ticket, customer, users) for ticket, customer in rows]


def count_tickets(session: Session, merchant_id: int) -> dict[str, int]:
    counts = {"open": 0, "claimed": 0, "resolved": 0}
    rows = session.execute(
        select(HandoffTicket.status, func.count(HandoffTicket.id))
        .where(HandoffTicket.merchant_id == merchant_id)
        .group_by(HandoffTicket.status)
    ).all()
    for status, count in rows:
        if status in counts:
            counts[status] = int(count)
    return counts


def get_ticket_detail(
    session: Session, merchant_id: int, ticket_id: int
) -> HandoffDetailView:
    row = session.execute(
        select(HandoffTicket, Customer)
        .outerjoin(Customer, Customer.id == HandoffTicket.customer_id)
        .where(
            HandoffTicket.id == ticket_id,
            HandoffTicket.merchant_id == merchant_id,
        )
    ).first()
    if row is None:
        raise NotFoundError("工单不存在")
    ticket, customer = row
    users = _users_by_id(
        session,
        {
            user_id
            for user_id in (ticket.claimed_by, ticket.resolved_by)
            if user_id is not None
        },
    )
    messages = session.execute(
        select(Message)
        .where(Message.conversation_id == ticket.conversation_id)
        .order_by(Message.id)
    ).scalars().all()
    note_rows = session.execute(
        select(HandoffNote, User)
        .outerjoin(User, User.id == HandoffNote.author_user_id)
        .where(HandoffNote.ticket_id == ticket.id)
        .order_by(HandoffNote.created_at, HandoffNote.id)
    ).all()
    return HandoffDetailView(
        ticket=_view(ticket, customer, users),
        messages=tuple(
            HandoffMessageView(
                id=message.id,
                role=message.role,
                content=message.content,
                attachment_ids=tuple(message.attachment_ids or ()),
                created_at=message.created_at,
            )
            for message in messages
        ),
        notes=tuple(
            HandoffNoteView(
                id=note.id,
                author_email=author.email if author else None,
                content=note.content,
                created_at=note.created_at,
            )
            for note, author in note_rows
        ),
        customer_context=get_customer_directory(
            session, merchant_id, ticket.customer_id
        ),
    )


def add_ticket_attachment(
    session: Session,
    merchant_id: int,
    user_id: int,
    ticket_id: int,
    *,
    original_name: str,
    data: bytes,
) -> Attachment:
    ticket = session.execute(
        select(HandoffTicket).where(
            HandoffTicket.id == ticket_id,
            HandoffTicket.merchant_id == merchant_id,
        )
    ).scalar_one_or_none()
    if ticket is None:
        raise NotFoundError("工单不存在")
    if ticket.status == "resolved":
        raise ConflictError("工单已解决")
    attachment = save_staff_attachment(
        session,
        merchant_id=merchant_id,
        customer_id=ticket.customer_id,
        user_id=user_id,
        original_name=original_name,
        data=data,
    )
    append_event(
        session,
        merchant_id,
        "handoff_attachment_uploaded",
        {
            "ticket_id": ticket.id,
            "ticket_ref": ticket_ref(ticket.id),
            "attachment_id": attachment.id,
            "content_type": attachment.content_type,
            "size_bytes": attachment.size_bytes,
            "operator_user_id": user_id,
        },
        conversation_id=ticket.conversation_id,
    )
    session.commit()
    return attachment


def _ticket_for_update(
    session: Session, merchant_id: int, ticket_id: int
) -> HandoffTicket:
    ticket = session.execute(
        select(HandoffTicket)
        .where(
            HandoffTicket.id == ticket_id,
            HandoffTicket.merchant_id == merchant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if ticket is None:
        raise NotFoundError("工单不存在")
    return ticket


def claim_ticket(
    session: Session, merchant_id: int, user_id: int, ticket_id: int
) -> HandoffActionResult:
    try:
        ticket = _ticket_for_update(session, merchant_id, ticket_id)
        if ticket.status == "resolved":
            raise ConflictError("工单已解决")
        if ticket.claimed_by == user_id:
            result = HandoffActionResult(
                id=ticket.id,
                ticket_ref=ticket_ref(ticket.id),
                status=ticket.status,
            )
            session.commit()
            return result
        if ticket.claimed_by is not None:
            raise ConflictError("工单已由其他客服接管")
        now = datetime.now(UTC)
        ticket.status = "claimed"
        ticket.claimed_by = user_id
        ticket.claimed_at = now
        append_event(
            session,
            merchant_id,
            "handoff_claimed",
            {
                "ticket_id": ticket.id,
                "ticket_ref": ticket_ref(ticket.id),
                "operator_user_id": user_id,
            },
            conversation_id=ticket.conversation_id,
        )
        session.commit()
        return HandoffActionResult(
            id=ticket.id,
            ticket_ref=ticket_ref(ticket.id),
            status=ticket.status,
        )
    except Exception:
        session.rollback()
        raise


def assign_ticket(
    session: Session,
    merchant_id: int,
    operator_id: int,
    ticket_id: int,
    assignee_id: int,
) -> HandoffActionResult:
    try:
        ticket = _ticket_for_update(session, merchant_id, ticket_id)
        if ticket.status == "resolved":
            raise ConflictError("工单已解决")
        assignee = session.execute(
            select(User).where(
                User.id == assignee_id,
                User.merchant_id == merchant_id,
                User.status == "active",
            )
        ).scalar_one_or_none()
        if assignee is None:
            raise ValidationError("客服账号不存在或已停用")
        if ticket.status == "claimed" and ticket.claimed_by == assignee_id:
            session.commit()
            return HandoffActionResult(
                id=ticket.id,
                ticket_ref=ticket_ref(ticket.id),
                status=ticket.status,
            )
        now = datetime.now(UTC)
        ticket.status = "claimed"
        ticket.claimed_by = assignee_id
        ticket.claimed_at = now
        append_event(
            session,
            merchant_id,
            "handoff_assigned",
            {
                "ticket_id": ticket.id,
                "ticket_ref": ticket_ref(ticket.id),
                "assignee_user_id": assignee_id,
                "operator_user_id": operator_id,
            },
            conversation_id=ticket.conversation_id,
        )
        notify_user(
            session,
            merchant_id,
            assignee,
            notification_type="handoff_assigned",
            title=f"工单 {ticket_ref(ticket.id)} 已指派给你",
            body=f"{ticket.queue}队列 · {ticket.last_user_message[:120]}",
            link="/admin",
        )
        session.commit()
        return HandoffActionResult(
            id=ticket.id,
            ticket_ref=ticket_ref(ticket.id),
            status=ticket.status,
        )
    except Exception:
        session.rollback()
        raise


@dataclass(frozen=True)
class AgentMessageResult:
    message_id: int
    ticket_id: int
    ticket_ref: str
    status: str
    content: str
    created_at: datetime
    auto_claimed: bool


def send_agent_message(
    session: Session,
    merchant_id: int,
    user_id: int,
    ticket_id: int,
    content: str,
    attachment_ids: list[int] | None = None,
) -> AgentMessageResult:
    content_value = content.strip()
    if len(content_value) > 1000:
        raise ValidationError("回复内容不能超过 1000 个字符")
    if not content_value and not attachment_ids:
        raise ValidationError("回复内容或图片至少提供一项")
    try:
        conversation_id = session.execute(
            select(HandoffTicket.conversation_id).where(
                HandoffTicket.id == ticket_id,
                HandoffTicket.merchant_id == merchant_id,
            )
        ).scalar_one_or_none()
        if conversation_id is None:
            raise NotFoundError("工单不存在")
        session.execute(
            select(Conversation.id)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        ).scalar_one_or_none()
        ticket = _ticket_for_update(session, merchant_id, ticket_id)
        if ticket.status == "resolved":
            raise ConflictError("工单已解决")
        if ticket.claimed_by is not None and ticket.claimed_by != user_id:
            raise ConflictError("工单已由其他客服接管")
        attachments = validate_staff_attachment_ids(
            session,
            merchant_id,
            ticket.customer_id,
            attachment_ids or [],
        )
        now = datetime.now(UTC)
        auto_claimed = ticket.claimed_by is None
        if auto_claimed:
            ticket.status = "claimed"
            ticket.claimed_by = user_id
            ticket.claimed_at = now
            append_event(
                session,
                merchant_id,
                "handoff_claimed",
                {
                    "ticket_id": ticket.id,
                    "ticket_ref": ticket_ref(ticket.id),
                    "operator_user_id": user_id,
                    "auto": True,
                },
                conversation_id=conversation_id,
            )
        message = Message(
            conversation_id=conversation_id,
            role="agent",
            content=content_value,
            attachment_ids=attachments or None,
            created_at=now,
        )
        session.add(message)
        session.flush()
        reply_summary = content_value or f"[图片×{len(attachments)}]"
        ticket.last_reply = reply_summary
        _refresh_summary(session, ticket)
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.status = "handoff"
            conversation.updated_at = now
        ref = ticket_ref(ticket.id)
        notify_customer(
            session,
            merchant_id,
            ticket.customer_id,
            notification_type="agent_reply",
            title=f"客服回复了工单 {ref}",
            body=reply_summary[:120],
            link="/",
        )
        append_event(
            session,
            merchant_id,
            "handoff_agent_replied",
            {
                "ticket_id": ticket.id,
                "ticket_ref": ref,
                "message_id": message.id,
                "operator_user_id": user_id,
                "auto_claimed": auto_claimed,
                "length": len(content_value),
                "attachment_count": len(attachments),
            },
            conversation_id=conversation_id,
        )
        session.commit()
        return AgentMessageResult(
            message_id=message.id,
            ticket_id=ticket.id,
            ticket_ref=ref,
            status=ticket.status,
            content=message.content,
            created_at=message.created_at,
            auto_claimed=auto_claimed,
        )
    except Exception:
        session.rollback()
        raise


def add_handoff_note(
    session: Session,
    merchant_id: int,
    user_id: int,
    ticket_id: int,
    content: str,
) -> HandoffNoteView:
    content_value = content.strip()
    if not content_value:
        raise ValidationError("内部备注不能为空")
    if len(content_value) > 1000:
        raise ValidationError("内部备注不能超过 1000 个字符")
    try:
        ticket = session.execute(
            select(HandoffTicket).where(
                HandoffTicket.id == ticket_id,
                HandoffTicket.merchant_id == merchant_id,
            )
        ).scalar_one_or_none()
        if ticket is None:
            raise NotFoundError("工单不存在")
        note = HandoffNote(
            merchant_id=merchant_id,
            ticket_id=ticket.id,
            author_user_id=user_id,
            content=content_value,
        )
        session.add(note)
        session.flush()
        append_event(
            session,
            merchant_id,
            "handoff_note_added",
            {
                "ticket_id": ticket.id,
                "ticket_ref": ticket_ref(ticket.id),
                "note_id": note.id,
                "operator_user_id": user_id,
                "length": len(content_value),
            },
            conversation_id=ticket.conversation_id,
        )
        session.commit()
        author = session.get(User, user_id)
        return HandoffNoteView(
            id=note.id,
            author_email=author.email if author else None,
            content=note.content,
            created_at=note.created_at,
        )
    except Exception:
        session.rollback()
        raise


def resolve_ticket(
    session: Session,
    merchant_id: int,
    user_id: int,
    ticket_id: int,
    note: str = "",
) -> HandoffActionResult:
    note_value = note.strip()
    if len(note_value) > 1000:
        raise ValidationError("处理说明不能超过 1000 个字符")
    try:
        conversation_id = session.execute(
            select(HandoffTicket.conversation_id).where(
                HandoffTicket.id == ticket_id,
                HandoffTicket.merchant_id == merchant_id,
            )
        ).scalar_one_or_none()
        if conversation_id is None:
            raise NotFoundError("工单不存在")
        session.execute(
            select(Conversation.id)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        ).scalar_one_or_none()
        ticket = _ticket_for_update(session, merchant_id, ticket_id)
        if ticket.status == "resolved":
            raise ConflictError("工单已解决")
        if ticket.claimed_by is not None and ticket.claimed_by != user_id:
            raise ConflictError("工单已由其他客服接管")
        now = datetime.now(UTC)
        if ticket.claimed_by is None:
            ticket.claimed_by = user_id
            ticket.claimed_at = now
        ticket.status = "resolved"
        ticket.resolved_by = user_id
        ticket.resolved_at = now
        ticket.resolution_note = note_value
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.status = "closed"
        append_event(
            session,
            merchant_id,
            "handoff_resolved",
            {
                "ticket_id": ticket.id,
                "ticket_ref": ticket_ref(ticket.id),
                "operator_user_id": user_id,
                "note": note_value,
            },
            conversation_id=ticket.conversation_id,
        )
        notify_customer(
            session,
            merchant_id,
            ticket.customer_id,
            notification_type="handoff_resolved",
            title="人工工单已解决",
            body=f"工单 {ticket_ref(ticket.id)} 已由客服处理",
            link="/after-sales",
        )
        session.commit()
        return HandoffActionResult(
            id=ticket.id,
            ticket_ref=ticket_ref(ticket.id),
            status=ticket.status,
        )
    except Exception:
        session.rollback()
        raise
