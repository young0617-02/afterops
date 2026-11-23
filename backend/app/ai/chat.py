from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.guardrails import SAFE_FALLBACK, has_commitment
from app.ai.intents import Intent, classify
from app.ai.providers import LLMRequest, ProviderError, get_provider
from app.ai.tools import (
    ToolFact,
    get_after_sales,
    get_invoice_status,
    get_order,
    get_product,
    get_refunds,
    get_repairs,
    get_returns,
    get_shipment,
    search_policy,
)
from app.domain.models import Conversation, Customer, HandoffTicket, Message
from app.domain.money import format_cents
from app.services.attachments import validate_attachment_ids
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.handoffs import (
    open_or_touch_ticket,
    ticket_ref,
    touch_active_ticket,
)
from app.services.proposals import (
    ProposalResult,
    create_address_change_proposal,
    create_compensation_proposal,
    create_refund_proposal,
)
from app.services.return_reasons import classify_return_reason
from app.services.returns import create_return
from app.settings import settings

ORDER_RE = re.compile(r"\bORD-[A-Z0-9-]{6,}\b", re.IGNORECASE)
SKU_RE = re.compile(r"\bSKU-[A-Z]{2,6}-[A-Z0-9]{1,4}\b", re.IGNORECASE)
AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)")
ADDRESS_TEXT_RE = re.compile(r"(?:改成|改为|改到|地址改成|地址改为)\s*(.+)")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
PRODUCT_KEYWORDS = (
    "耳机", "手表", "键盘", "音箱", "电源", "摄像头", "固态", "数据线", "风扇", "充电器",
)
HANDOFF_TEXT = "如需人工，回复「转人工」。"
IMAGE_NOTE = "已收到 {count} 张图片，我无法识别图片内容；如需人工查看原图，回复「转人工」。"
IMAGE_ONLY_REPLY = (
    "已收到 {count} 张图片。我无法识别图片内容，请用文字描述具体问题，"
    "或回复「转人工」由客服查看原图。"
)
IMAGE_SUMMARY = "[图片×{count}]"
HANDOFF_ACTION_RE = re.compile(
    r"(回复|发送|输入|联系|转接|接入|转至)[^。！？\n]{0,20}"
    r"(转人工|人工客服|人工坐席|人工)"
)

ORDER_STATUS_LABELS = {
    "pending_payment": "待支付",
    "paid": "已支付",
    "fulfilling": "履约中",
    "shipped": "已发货",
    "delivered": "已签收",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunding": "退款处理中",
    "refunded": "已退款",
    "partially_refunded": "部分退款",
}
PAYMENT_STATUS_LABELS = {
    "unpaid": "未支付",
    "pending": "支付处理中",
    "paid": "已支付",
    "failed": "支付失败",
    "refunded": "已退款",
    "partially_refunded": "部分退款",
}
SHIPMENT_STATUS_LABELS = {
    "pending": "待揽收",
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "delivered": "已签收",
    "exception": "异常件",
}
AFTER_SALES_ACTION_LABELS = {
    "refund": "退款",
    "compensation": "补偿",
    "address_change": "改地址",
}
AFTER_SALES_STATUS_LABELS = {
    "pending": "等待人工审核",
    "approved": "已批准，执行中",
    "executing": "执行中",
    "executed": "已执行",
    "rejected": "未通过",
    "failed": "处理失败",
    "expired": "已过期",
}
INVOICE_STATUS_LABELS = {
    "requested": "待开票",
    "issued": "已开票",
    "rejected": "已驳回",
    "voided": "已作废",
}
REPAIR_STATUS_LABELS = {
    "requested": "待审核",
    "approved": "待顾客回寄",
    "in_transit": "顾客回寄中",
    "received": "维修中",
    "return_shipped": "维修寄回中",
    "completed": "已完成",
    "rejected": "已驳回",
    "withdrawn": "已撤回",
}
RETURN_STATUS_LABELS = {
    "requested": "待审核",
    "approved": "待回寄",
    "in_transit": "回寄中",
    "received": "已收货",
    "rejected": "已驳回",
    "withdrawn": "已撤回",
}

RETURN_APPEAL_LABELS = {
    "pending": "待处理",
    "accepted": "已支持",
    "rejected": "未通过",
    "withdrawn": "已撤回",
}


@dataclass(frozen=True)
class Handoff:
    available: bool
    queue: str
    ticket_ref: str | None


@dataclass(frozen=True)
class ChatResult:
    conversation_id: int
    message_id: int
    reply: str
    intent: str
    intent_label: str
    confidence: float
    citations: list[str]
    handoff: Handoff
    proposals: list[dict]
    returns: list[dict]
    audit_id: int
    provider: str = "template"
    degraded: bool = False
    fallback_reason: str | None = None


def _order_no(text: str) -> str | None:
    match = ORDER_RE.search(text)
    return match.group(0).upper() if match else None


def _sku(text: str) -> str | None:
    match = SKU_RE.search(text)
    return match.group(0).upper() if match else None


def _keyword(text: str) -> str | None:
    return next((word for word in PRODUCT_KEYWORDS if word in text), None)


def _amount_cents(text: str) -> int | None:
    match = AMOUNT_RE.search(text)
    if match is None:
        return None
    try:
        return int((Decimal(match.group(1)) * 100).to_integral_value())
    except InvalidOperation:
        return None


def _new_address(text: str) -> str | None:
    match = ADDRESS_TEXT_RE.search(text)
    if match is None:
        return None
    return match.group(1).strip(" ，,。") or None


def _return_reason(text: str) -> str:
    cleaned = ORDER_RE.sub("", text).strip(" ，,。；;")
    return (cleaned or "顾客通过聊天申请退货")[:1000]


def _proposal_reply(proposal: ProposalResult) -> str:
    amount = (
        format_cents(proposal.amount_cents)
        if proposal.amount_cents is not None
        else ""
    )
    if proposal.action_type == "refund":
        return (
            f"已提交退款申请（提案 #{proposal.proposal_id}，金额 {amount}），"
            "等待人工审核；退款结果以人工核定为准，我不代为确认。"
        )
    if proposal.action_type == "compensation":
        return (
            f"已提交补偿申请（提案 #{proposal.proposal_id}，金额 {amount}），"
            "等待人工审核；是否赔付由人工核定。"
        )
    return (
        f"已提交改地址申请（提案 #{proposal.proposal_id}），"
        "等待人工审核；审核通过后才会修改订单地址。"
    )


def _number_keys(text: str) -> set[str]:
    out: set[str] = set()
    for match in NUMBER_RE.finditer(text):
        raw = match.group(0).replace(",", "")
        try:
            out.add(str(Decimal(raw).normalize()))
        except InvalidOperation:
            out.add(raw)
    return out


def _rewrite_reply(
    reply: str,
    user_text: str,
    provider=None,
) -> tuple[str, str, bool, str | None]:
    if provider is None:
        try:
            provider = get_provider(settings)
        except ProviderError as exc:
            return reply, "template", True, f"provider_config:{exc}"
    name = getattr(provider, "name", "unknown")
    if name == "template":
        return reply, name, False, None
    try:
        result = provider.generate(
            LLMRequest(approved_text=reply, user_text=user_text)
        )
    except ProviderError as exc:
        return reply, name, True, f"provider_error:{exc}"

    candidate = (result.text or "").strip()
    if not candidate:
        return reply, name, True, "empty_output"
    if has_commitment(candidate):
        return reply, name, True, "commitment_leak"
    if not HANDOFF_ACTION_RE.search(candidate):
        return reply, name, True, "missing_handoff"
    approved_numbers = _number_keys(reply)
    extra_numbers = _number_keys(candidate) - approved_numbers
    if extra_numbers:
        return (
            reply,
            name,
            True,
            f"unapproved_numbers:{','.join(sorted(extra_numbers))}",
        )
    missing_numbers = approved_numbers - _number_keys(candidate)
    if missing_numbers:
        return (
            reply,
            name,
            True,
            f"missing_numbers:{','.join(sorted(missing_numbers))}",
        )
    return candidate, name, False, None


def _order_reply(data: dict) -> str:
    status = ORDER_STATUS_LABELS.get(data["status"], data["status"])
    payment = PAYMENT_STATUS_LABELS.get(data["payment_status"], data["payment_status"])
    return (
        f"订单 {data['order_no']} 当前状态：{status}；支付状态：{payment}；"
        f"实付 {data['payable_display']}；商品件数 {data['item_count']}。"
    )


def _logistics_reply(order_no: str, fact: ToolFact) -> str:
    if not fact.ok:
        return f"订单 {order_no} 暂无物流信息（可能尚未发货或物流数据未回传）。"
    packages = fact.data.get("packages", [])
    if not packages:
        return f"订单 {order_no} 暂无物流信息（可能尚未发货或物流数据未回传）。"
    parts = []
    for package in packages:
        status = SHIPMENT_STATUS_LABELS.get(
            package["status"], package["status"]
        )
        node = package.get("current_node") or "暂无"
        parts.append(
            f"包裹 #{package['package_no']}：{package['carrier']}，"
            f"运单 {package['tracking_no']}，当前状态 {status}，"
            f"最近节点「{node}」"
        )
    if len(parts) == 1:
        return f"订单 {order_no} 的物流：{parts[0].removeprefix('包裹 #1：')}。"
    return f"订单 {order_no} 共 {len(parts)} 个包裹：" + "；".join(parts) + "。"


def _refund_ledger_reply(order_no: str, fact: ToolFact) -> str:
    if not fact.ok:
        return f"订单 {order_no} 暂无退款记录。"
    refunds = fact.data.get("refunds", [])
    if not refunds:
        return f"订单 {order_no} 暂无退款记录。"
    parts = []
    settlement_labels = {
        "processing": "渠道处理中",
        "succeeded": "渠道已到账",
        "failed": "渠道失败",
    }
    for refund in refunds:
        kind = "补偿" if refund["kind"] == "compensation" else "退款"
        reason = f"（{refund['reason']}）" if refund.get("reason") else ""
        status = (
            "已执行" if refund["status"] == "executed" else refund["status"]
        )
        settlement = settlement_labels.get(
            refund.get("settlement_status", ""), ""
        )
        reference = (
            refund.get("settlement_reference")
            or refund["provider_refund_id"]
        )
        parts.append(
            f"{kind} {refund['amount_display']} 元{reason}，状态{status}"
            + (f"，{settlement}" if settlement else "")
            + f"，处理引用 {reference}"
        )
    executed = all(refund["status"] == "executed" for refund in refunds)
    prefix = "退款申请已执行。" if executed else "退款处理记录："
    return (
        f"订单 {order_no} {prefix}共 {len(parts)} 笔："
        + "；".join(parts)
        + f"。累计退款 {fact.data['refunded_total_display']} 元；"
        "到账时间以支付渠道为准。"
    )


def _invoice_reply(order_no: str, fact: ToolFact) -> str:
    if not fact.ok:
        return (
            f"订单 {order_no} 暂无发票申请；如需发票，可在订单详情提交"
            "个人或企业发票申请。"
        )
    data = fact.data
    status = INVOICE_STATUS_LABELS.get(data["status"], data["status"])
    reply = (
        f"订单 {order_no} 发票状态：{status}；"
        f"发票金额 {data['amount_display']} 元，"
        f"当前净支付 {data['net_paid_display']} 元"
    )
    if data["invoice_no"]:
        reply += f"；发票号 {data['invoice_no']}"
    if data["adjustment_required"]:
        reply += "。订单退款后发票金额与净支付不一致，需作废后按净支付重开，请等待人工处理"
    if data["void_reason"]:
        reply += f"；作废原因：{data['void_reason']}"
    return reply + "。"


def _repair_reply(order_no: str, fact: ToolFact) -> str:
    if not fact.ok:
        return f"订单 {order_no} 暂无维修申请记录。"
    repairs = fact.data.get("repairs", [])
    if not repairs:
        return f"订单 {order_no} 暂无维修申请记录。"
    repair = repairs[0]
    status = REPAIR_STATUS_LABELS.get(repair["status"], repair["status"])
    reply = f"订单 {order_no} 最新维修 #{repair['id']}（{repair['sku']}）状态：{status}"
    if repair["tracking_no"]:
        reply += (
            f"；顾客回寄 {repair['carrier']} 运单 {repair['tracking_no']}"
        )
    if repair["return_tracking_no"]:
        reply += (
            f"；维修寄回 {repair['return_carrier']} 运单 "
            f"{repair['return_tracking_no']}"
        )
    if repair["resolution_note"]:
        reply += f"；处理结果：{repair['resolution_note']}"
    if len(repairs) > 1:
        reply += f"；另有 {len(repairs) - 1} 条维修记录"
    return reply + "。"


def _return_reply(order_no: str, fact: ToolFact) -> str:
    if not fact.ok:
        return f"订单 {order_no} 暂无退货申请记录。"
    returns = fact.data.get("returns", [])
    if not returns:
        return f"订单 {order_no} 暂无退货申请记录。"
    request = returns[0]
    status = RETURN_STATUS_LABELS.get(
        request["status"], request["status"]
    )
    label = "换货" if request["resolution"] == "exchange" else "退货退款"
    reply = f"订单 {order_no} 最新退货 #{request['id']}（{label}）状态：{status}"
    if request["tracking_no"]:
        reply += (
            f"；回寄 {request['carrier']} 运单 {request['tracking_no']}"
        )
    if request["refund_amount_display"]:
        reply += f"；退款金额 {request['refund_amount_display']} 元"
    if request["refund_proposal_id"]:
        reply += f"；退款提案 #{request['refund_proposal_id']} 等待处理"
    if request["inspection_deduction_cents"] > 0:
        reply += (
            f"；质检扣减 "
            f"{format_cents(request['inspection_deduction_cents'])} 元"
        )
    appeal = request.get("appeal")
    if appeal:
        reply += (
            "；扣减申诉"
            f"{RETURN_APPEAL_LABELS.get(appeal['status'], appeal['status'])}"
        )
        if appeal.get("approved_display"):
            reply += f"（核准 {appeal['approved_display']} 元）"
        elif appeal["status"] == "pending":
            reply += f"（申请 {appeal['requested_display']} 元）"
    if request["replacement_order_id"]:
        replacement_no = request.get("replacement_order_no") or (
            f"ID {request['replacement_order_id']}"
        )
        reply += f"；已生成换货订单 {replacement_no}"
        shipments = request.get("replacement_shipments") or []
        if shipments:
            reply += "；补发" + "、".join(
                f"包裹 #{shipment['package_no']} {shipment['carrier']} "
                f"运单 {shipment['tracking_no']}"
                f"（{shipment['current_node'] or shipment['status']}）"
                for shipment in shipments
            )
        else:
            reply += "，等待补发发货"
    if request.get("exchange_supplement_cents", 0) > 0:
        supplement_status = (
            "已支付"
            if request.get("exchange_payment_status") == "paid"
            else "待支付"
        )
        reply += (
            f"；换货补差价 {request['exchange_supplement_display']} 元"
            f"{supplement_status}"
        )
    if len(returns) > 1:
        reply += f"；另有 {len(returns) - 1} 条退货记录"
    return reply + "。"


def _after_sales_reply(order_no: str, fact: ToolFact) -> str:
    if not fact.ok:
        return f"订单 {order_no} 暂无售后申请记录。"
    data = fact.data
    action = AFTER_SALES_ACTION_LABELS.get(
        data["action_type"], data["action_type"]
    )
    status = AFTER_SALES_STATUS_LABELS.get(data["status"], data["status"])
    amount = (
        f"，金额 {format_cents(data['amount_cents'])} 元"
        if data.get("amount_cents") is not None
        else ""
    )
    if data["status"] == "executed":
        ref = data.get("commerce_ref")
        return (
            f"订单 {order_no} 的{action}申请已执行{amount}"
            + (f"，处理引用 {ref}" if ref else "")
            + "。到账时间以支付渠道为准，我不承诺具体到账时间。"
        )
    if data["status"] == "rejected":
        reason = data.get("message") or "人工审核未通过"
        return f"订单 {order_no} 的{action}申请未通过{amount}；原因：{reason}。"
    if data["status"] == "failed":
        return (
            f"订单 {order_no} 的{action}申请处理失败{amount}，"
            "需要人工介入处理。"
        )
    return (
        f"订单 {order_no} 的{action}申请{amount}，当前状态：{status}，"
        "请等待人工处理。"
    )


def _policy_reply(fact: ToolFact, high_risk: bool = False) -> str:
    data = fact.data
    parts = [part.strip() for part in re.split(r"[。！？；\n]+", data["body"]) if part.strip()][:3]
    excerpt = "。".join(parts) or data["title"]
    validity = f"{data['valid_from']} 至 {data['valid_until'] or '长期'}"
    prefix = "这条规则我可以解释，但退款、赔付等结论由人工核定，我不代为确认。" if high_risk else ""
    return (
        f"{prefix}规则依据：{excerpt}。"
        f"（{data['source']}，{data['entry_id']}@{data['version']}，有效期 {validity}）"
    )


def _product_reply(data: dict) -> str:
    return (
        f"商品 {data['title']}（{data['sku']}）页面价 {data['price_display']}，"
        f"当前可售 {data['available_qty']} 件。库存为查询时点快照，价格以提交订单时页面展示为准。"
    )


def _human_reply() -> str:
    return "已记录转人工请求，本轮问题与查询到的数据会一并移交坐席。"


def _unknown_reply() -> str:
    return "我可以帮你查订单状态、物流、退款进度、退换货政策、商品价格与库存；请提供订单号或具体问题。"


def _conversation(session: Session, customer: Customer, conversation_id: int | None) -> Conversation:
    if conversation_id is not None:
        conversation = session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.merchant_id == customer.merchant_id,
                Conversation.customer_id == customer.id,
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise NotFoundError("会话不存在")
        return conversation
    conversation = Conversation(
        merchant_id=customer.merchant_id,
        customer_id=customer.id,
        channel="web",
        status="open",
    )
    session.add(conversation)
    session.flush()
    return conversation


def handle_chat(
    session: Session,
    customer: Customer,
    text: str,
    conversation_id: int | None = None,
    attachment_ids: list[int] | None = None,
    provider=None,
) -> ChatResult:
    try:
        text = text.strip()
        attachments = validate_attachment_ids(
            session, customer, attachment_ids or []
        )
        if not text and not attachments:
            raise ValidationError("请输入问题或上传图片")
        summary = text or IMAGE_SUMMARY.format(count=len(attachments))
        conversation = _conversation(session, customer, conversation_id)
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=text,
                attachment_ids=attachments or None,
            )
        )
        session.flush()

        intent: Intent = (
            classify(text)
            if text
            else Intent("ATTACHMENT", "图片消息", 1.0)
        )

        active_ticket = session.execute(
            select(HandoffTicket)
            .where(
                HandoffTicket.merchant_id == customer.merchant_id,
                HandoffTicket.conversation_id == conversation.id,
                HandoffTicket.status.in_(("open", "claimed")),
            )
            .order_by(HandoffTicket.id.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if active_ticket is not None:
            now = datetime.now(UTC)
            queue = (
                "complaint"
                if intent.code in ("HIGH_RISK", "COMPLAINT")
                else "after_sales"
            )
            touch_active_ticket(
                session,
                active_ticket,
                queue=queue,
                intent=intent.code,
                user_message=summary,
            )
            conversation.status = "handoff"
            conversation.updated_at = now
            if attachments:
                reply = (
                    f"已收到 {len(attachments)} 张图片，图片已随消息转给人工坐席。"
                    if active_ticket.status == "claimed"
                    else f"已收到 {len(attachments)} 张图片，已转人工，"
                    "图片已进入人工队列，请等待客服接管。"
                )
            else:
                reply = (
                    "客服已接管本次会话，你的消息已转给人工坐席。"
                    if active_ticket.status == "claimed"
                    else "已转人工，消息已进入人工队列，请等待客服接管。"
                )
            assistant = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=reply,
                tool_calls_json={
                    "mode": "handoff_waiting",
                    "handoff_ticket_ref": ticket_ref(active_ticket.id),
                    "attachment_count": len(attachments),
                },
            )
            session.add(assistant)
            session.flush()
            audit = append_event(
                session,
                customer.merchant_id,
                "chat_handoff_message",
                {
                    "intent": "HANDOFF_WAIT",
                    "ticket_id": active_ticket.id,
                    "ticket_ref": ticket_ref(active_ticket.id),
                    "message_id": assistant.id,
                    "attachment_count": len(attachments),
                },
                conversation_id=conversation.id,
            )
            session.commit()
            return ChatResult(
                conversation_id=conversation.id,
                message_id=assistant.id,
                reply=reply,
                intent="HANDOFF_WAIT",
                intent_label="人工处理中",
                confidence=1.0,
                citations=[],
                handoff=Handoff(
                    available=True,
                    queue=active_ticket.queue,
                    ticket_ref=ticket_ref(active_ticket.id),
                ),
                proposals=[],
                returns=[],
                audit_id=audit.id,
                provider="system",
            )

        if not text:
            reply = IMAGE_ONLY_REPLY.format(count=len(attachments))
            guardrail_triggered = has_commitment(reply)
            assistant = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=reply,
            )
            session.add(assistant)
            session.flush()
            conversation.updated_at = datetime.now(UTC)
            audit = append_event(
                session,
                customer.merchant_id,
                "chat_answered",
                {
                    "intent": "ATTACHMENT",
                    "reply": reply,
                    "citations": [],
                    "proposals": [],
                    "returns": [],
                    "tool_statuses": [],
                    "numbers": [],
                    "guardrail_triggered": guardrail_triggered,
                    "llm_provider": "template",
                    "degraded": False,
                    "fallback_reason": None,
                    "attachment_count": len(attachments),
                    "handoff_ticket_id": None,
                    "handoff_ticket_ref": None,
                },
                conversation_id=conversation.id,
            )
            assistant.tool_calls_json = {
                "intent": "ATTACHMENT",
                "mode": "attachment_only",
                "attachment_count": len(attachments),
                "citations": [],
                "proposals": [],
                "returns": [],
                "audit_id": audit.id,
                "llm_provider": "template",
                "degraded": False,
                "fallback_reason": None,
                "handoff_ticket_ref": None,
            }
            session.commit()
            return ChatResult(
                conversation_id=conversation.id,
                message_id=assistant.id,
                reply=reply,
                intent="ATTACHMENT",
                intent_label="图片消息",
                confidence=1.0,
                citations=[],
                handoff=Handoff(
                    available=True,
                    queue="after_sales",
                    ticket_ref=None,
                ),
                proposals=[],
                returns=[],
                audit_id=audit.id,
                provider="template",
            )

        facts: list[ToolFact] = []
        proposals: list[dict] = []
        returns: list[dict] = []
        high_risk = intent.high_risk
        confidence = intent.confidence

        if intent.code == "HUMAN_REQUEST":
            reply = _human_reply()
        elif intent.code in ("REFUND_REQUEST", "COMPENSATION_REQUEST", "ADDRESS_CHANGE"):
            order_no = _order_no(text)
            if order_no is None:
                reply = "请先提供订单号（形如 ORD-2026-000101），我才能核对并提交申请。"
                confidence = min(confidence, 0.35)
            else:
                order_fact = get_order(
                    session, customer.merchant_id, customer.id, order_no
                )
                facts.append(order_fact)
                if not order_fact.ok:
                    reply = "未查到该订单，请核对订单号；如需人工，可回复「转人工」。"
                    confidence = min(confidence, 0.3)
                else:
                    policy_topic = {
                        "REFUND_REQUEST": "procedure",
                        "COMPENSATION_REQUEST": "compensation",
                        "ADDRESS_CHANGE": "address_change",
                    }[intent.code]
                    policy_fact = search_policy(
                        session, customer.merchant_id, policy_topic
                    )
                    if policy_fact.ok:
                        facts.append(policy_fact)
                    proposal: ProposalResult | None = None
                    try:
                        if intent.code == "REFUND_REQUEST":
                            proposal = create_refund_proposal(
                                session,
                                customer,
                                order_no,
                                _amount_cents(text),
                                text,
                                conversation.id,
                            )
                        elif intent.code == "COMPENSATION_REQUEST":
                            amount = _amount_cents(text)
                            if amount is None:
                                reply = "请提供希望补偿的金额（元），例如「赔我 100 元」。"
                                confidence = min(confidence, 0.35)
                            else:
                                proposal = create_compensation_proposal(
                                    session,
                                    customer,
                                    order_no,
                                    amount,
                                    text,
                                    conversation.id,
                                )
                        else:
                            new_address = _new_address(text)
                            if new_address is None:
                                reply = "请提供新地址，例如「改地址，改成上海市浦东新区…」。"
                                confidence = min(confidence, 0.35)
                            else:
                                proposal = create_address_change_proposal(
                                    session,
                                    customer,
                                    order_no,
                                    new_address,
                                    conversation.id,
                                )
                    except (ConflictError, ValidationError) as exc:
                        reply = f"暂时无法提交申请：{exc.message}。"
                        confidence = min(confidence, 0.3)
                    if proposal is not None:
                        proposals.append(proposal.to_json())
                        reply = _proposal_reply(proposal)
                        confidence = max(confidence, 0.85)
                        conversation.status = "handoff"
        elif intent.high_risk:
            topic = intent.topic or "human"
            fact = search_policy(session, customer.merchant_id, topic)
            facts.append(fact)
            if fact.ok:
                reply = _policy_reply(fact, high_risk=True)
            else:
                reply = "这条诉求涉及退款、赔付或投诉，需要人工受理；我不代为确认结论。"
        elif intent.code == "REFUND_STATUS":
            order_no = _order_no(text)
            if order_no is None:
                reply = "请先提供订单号（形如 ORD-2026-000101），我才能核对退款进度。"
                confidence = min(confidence, 0.35)
            else:
                order_fact = get_order(
                    session, customer.merchant_id, customer.id, order_no
                )
                facts.append(order_fact)
                if not order_fact.ok:
                    reply = "未查到该订单，请核对订单号；如需人工，可回复「转人工」。"
                    confidence = min(confidence, 0.3)
                else:
                    refund_fact = get_refunds(
                        session,
                        customer.merchant_id,
                        order_fact.data["order_id"],
                    )
                    facts.append(refund_fact)
                    after_sales_fact = get_after_sales(
                        session,
                        customer.merchant_id,
                        order_fact.data["order_id"],
                    )
                    facts.append(after_sales_fact)
                    if refund_fact.ok:
                        reply = _refund_ledger_reply(order_no, refund_fact)
                        if (
                            after_sales_fact.ok
                            and after_sales_fact.data.get("status")
                            in ("pending", "approved", "executing")
                        ):
                            reply += " 另有一笔退款申请仍在人工处理中。"
                    else:
                        reply = _after_sales_reply(order_no, after_sales_fact)
        elif intent.code in (
            "INVOICE_STATUS",
            "REPAIR_STATUS",
            "RETURN_STATUS",
        ):
            order_no = _order_no(text)
            if order_no is None:
                reply = "请先提供订单号（形如 ORD-2026-000101），我才能核对进度。"
                confidence = min(confidence, 0.35)
            else:
                order_fact = get_order(
                    session, customer.merchant_id, customer.id, order_no
                )
                facts.append(order_fact)
                if not order_fact.ok:
                    reply = "未查到该订单，请核对订单号；如需人工，可回复「转人工」。"
                    confidence = min(confidence, 0.3)
                elif intent.code == "INVOICE_STATUS":
                    fact = get_invoice_status(
                        session,
                        customer.merchant_id,
                        order_fact.data["order_id"],
                    )
                    facts.append(fact)
                    reply = _invoice_reply(order_no, fact)
                elif intent.code == "REPAIR_STATUS":
                    fact = get_repairs(
                        session,
                        customer.merchant_id,
                        order_fact.data["order_id"],
                    )
                    facts.append(fact)
                    reply = _repair_reply(order_no, fact)
                else:
                    fact = get_returns(
                        session,
                        customer.merchant_id,
                        order_fact.data["order_id"],
                    )
                    facts.append(fact)
                    reply = _return_reply(order_no, fact)
        elif intent.code in ("ORDER_STATUS", "LOGISTICS_TRACK"):
            order_no = _order_no(text)
            if order_no is None:
                reply = (
                    "请先提供订单号（形如 ORD-2026-000101），我才能核对这一单；"
                    "在拿到订单号之前我不下结论。"
                )
                confidence = min(confidence, 0.35)
            else:
                order_fact = get_order(
                    session, customer.merchant_id, customer.id, order_no
                )
                facts.append(order_fact)
                if not order_fact.ok:
                    reply = "未查到该订单，请核对订单号；如需人工，可回复「转人工」。"
                    confidence = min(confidence, 0.3)
                elif intent.code == "LOGISTICS_TRACK":
                    shipment_fact = get_shipment(
                        session, customer.merchant_id, order_fact.data["order_id"]
                    )
                    facts.append(shipment_fact)
                    reply = _logistics_reply(order_fact.data["order_no"], shipment_fact)
                else:
                    reply = _order_reply(order_fact.data)
        elif intent.code == "RETURN_REQUEST":
            order_no = _order_no(text)
            if order_no is None:
                reply = (
                    "请先提供已签收订单的订单号（形如 ORD-...），"
                    "我才能提交退货或换货申请。"
                )
                confidence = min(confidence, 0.35)
            else:
                order_fact = get_order(
                    session, customer.merchant_id, customer.id, order_no
                )
                facts.append(order_fact)
                if not order_fact.ok:
                    reply = "未查到该订单，请核对订单号；如需人工，可回复「转人工」。"
                    confidence = min(confidence, 0.3)
                else:
                    policy_fact = search_policy(
                        session, customer.merchant_id, "return"
                    )
                    if policy_fact.ok:
                        facts.append(policy_fact)
                    resolution = "exchange" if "换货" in text else "refund"
                    try:
                        with session.begin_nested():
                            returned = create_return(
                                session,
                                customer,
                                order_no,
                                _return_reason(text),
                                resolution=resolution,
                                reason_category=classify_return_reason(text),
                                commit=False,
                            )
                        label = "换货" if resolution == "exchange" else "退货退款"
                        returns.append(
                            {
                                "id": returned.id,
                                "status": returned.status,
                                "resolution": resolution,
                            }
                        )
                        reply = (
                            f"已提交{label}申请（退货 #{returned.id}），"
                            "等待店主审核；审核通过后会告知寄回地址。"
                            "退货结果以人工核定为准，我不代为确认。"
                        )
                    except (ConflictError, NotFoundError, ValidationError) as exc:
                        reply = (
                            f"暂时不能提交退货申请：{exc.message}。"
                            "如需人工，可回复「转人工」。"
                        )
                        confidence = min(confidence, 0.5)
        elif intent.code == "RETURN_POLICY":
            fact = search_policy(session, customer.merchant_id, intent.topic or "return")
            facts.append(fact)
            if fact.ok:
                reply = _policy_reply(fact)
            else:
                reply = "暂未检索到该主题的现行政策条文，为避免说错，我不编造规则，已转人工。"
                confidence = min(confidence, 0.3)
        elif intent.code == "PRODUCT_INFO":
            fact = get_product(
                session,
                customer.merchant_id,
                sku=_sku(text),
                keyword=_keyword(text),
            )
            facts.append(fact)
            if fact.ok:
                reply = _product_reply(fact.data)
            else:
                reply = "没有查到对应商品，请提供 SKU（形如 SKU-NCM-001）或商品关键词。"
                confidence = min(confidence, 0.3)
        elif intent.code == "GREETING":
            reply = _unknown_reply()
        else:
            reply = _unknown_reply()
            confidence = min(confidence, 0.3)

        if attachments:
            reply = f"{reply.rstrip()} {IMAGE_NOTE.format(count=len(attachments))}"
        if not HANDOFF_ACTION_RE.search(reply):
            reply = f"{reply.rstrip()} {HANDOFF_TEXT}"

        guardrail_triggered = has_commitment(reply)
        if guardrail_triggered:
            reply = f"{SAFE_FALLBACK} {HANDOFF_TEXT}"
            confidence = min(confidence, 0.3)

        reply, provider_name, degraded, fallback_reason = _rewrite_reply(
            reply, text, provider
        )
        if degraded:
            confidence = min(confidence, 0.5)
        if attachments and "无法识别图片" not in reply:
            reply = f"{reply.rstrip()} {IMAGE_NOTE.format(count=len(attachments))}"

        citations = list(dict.fromkeys(fact.citation for fact in facts if fact.citation))
        assistant = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
        )
        session.add(assistant)
        session.flush()

        queue = (
            "complaint"
            if intent.code in ("HIGH_RISK", "COMPLAINT")
            else "after_sales"
        )
        needs_ticket = (
            high_risk or intent.code == "HUMAN_REQUEST"
        ) and not proposals
        ticket = open_or_touch_ticket(
            session,
            conversation,
            customer,
            queue=queue,
            intent=intent.code,
            user_message=summary,
            reply=reply,
            create=needs_ticket,
        )
        handoff = Handoff(
            available=True,
            queue=queue,
            ticket_ref=ticket_ref(ticket.id) if ticket else None,
        )
        if high_risk or proposals or intent.code == "HUMAN_REQUEST":
            conversation.status = "handoff"
        conversation.updated_at = datetime.now(UTC)

        audit = append_event(
            session,
            customer.merchant_id,
            "chat_answered",
            {
                "intent": intent.code,
                "reply": reply,
                "citations": citations,
                "proposals": proposals,
                "returns": returns,
                "tool_statuses": [
                    {"tool": fact.tool, "status": fact.status} for fact in facts
                ],
                "numbers": [number for fact in facts for number in fact.numbers],
                "guardrail_triggered": guardrail_triggered,
                "llm_provider": provider_name,
                "degraded": degraded,
                "fallback_reason": fallback_reason,
                "attachment_count": len(attachments),
                "handoff_ticket_id": ticket.id if ticket else None,
                "handoff_ticket_ref": (
                    ticket_ref(ticket.id) if ticket else None
                ),
            },
            conversation_id=conversation.id,
        )
        assistant.tool_calls_json = {
            "intent": intent.code,
            "citations": citations,
            "proposals": proposals,
            "returns": returns,
            "audit_id": audit.id,
            "llm_provider": provider_name,
            "degraded": degraded,
            "fallback_reason": fallback_reason,
            "attachment_count": len(attachments),
            "handoff_ticket_ref": ticket_ref(ticket.id) if ticket else None,
        }
        session.commit()
        return ChatResult(
            conversation_id=conversation.id,
            message_id=assistant.id,
            reply=reply,
            intent=intent.code,
            intent_label=intent.label,
            confidence=confidence,
            citations=citations,
            handoff=handoff,
            proposals=proposals,
            returns=returns,
            audit_id=audit.id,
            provider=provider_name,
            degraded=degraded,
            fallback_reason=fallback_reason,
        )
    except Exception:
        session.rollback()
        raise
