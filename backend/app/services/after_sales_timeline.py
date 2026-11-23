from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    Customer,
    HandoffTicket,
    InvoiceRequest,
    Order,
    OrderItem,
    Refund,
    RepairRequest,
    ReturnDeductionAppeal,
    ReturnPickup,
    ReturnRequest,
)
from app.domain.money import format_cents
from app.services.proposals import ACTION_LABELS
from app.services.returns import RETURN_TRACKING_LABELS

RETURN_STATUS_LABELS = {
    "requested": "待审核",
    "approved": "待回寄",
    "in_transit": "回寄中",
    "received": "已收货",
    "rejected": "已驳回",
    "withdrawn": "已撤回",
    "exchange_cancelled": "换货已取消",
}
REPAIR_STATUS_LABELS = {
    "requested": "待审核",
    "approved": "待回寄",
    "in_transit": "回寄中",
    "received": "维修中",
    "return_shipped": "维修寄回中",
    "completed": "已完成",
    "rejected": "已驳回",
    "withdrawn": "已撤回",
}
APPEAL_STATUS_LABELS = {
    "pending": "待处理",
    "accepted": "已支持",
    "rejected": "未通过",
    "withdrawn": "已撤回",
}
PICKUP_STATUS_LABELS = {
    "scheduled": "待上门",
    "picked_up": "已揽收",
    "cancelled": "已取消",
}
PROPOSAL_STATUS_LABELS = {
    "pending": "待审批",
    "approved": "已批准",
    "rejected": "已驳回",
    "executing": "执行中",
    "executed": "已执行",
    "failed": "执行失败",
    "expired": "已过期",
    "withdrawn": "已撤回",
}
INVOICE_STATUS_LABELS = {
    "requested": "待开具",
    "issued": "已开具",
    "voided": "已作废",
    "rejected": "已驳回",
}
HANDOFF_STATUS_LABELS = {
    "open": "待接管",
    "claimed": "处理中",
    "resolved": "已解决",
}


@dataclass(frozen=True)
class TimelineEventView:
    kind: str
    occurred_at: datetime
    title: str
    detail: str
    status: str
    order_no: str | None
    entity_id: int | None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _event(
    kind: str,
    occurred_at: datetime | None,
    title: str,
    detail: str,
    status: str,
    order_no: str | None,
    entity_id: int | None,
) -> TimelineEventView | None:
    occurred = _as_utc(occurred_at)
    if occurred is None:
        return None
    return TimelineEventView(
        kind=kind,
        occurred_at=occurred,
        title=title,
        detail=detail,
        status=status,
        order_no=order_no,
        entity_id=entity_id,
    )


def customer_after_sales_timeline(
    session: Session, customer: Customer, *, limit: int = 50
) -> list[TimelineEventView]:
    events: list[TimelineEventView] = []

    return_rows = session.execute(
        select(ReturnRequest, Order)
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(
            ReturnRequest.merchant_id == customer.merchant_id,
            ReturnRequest.customer_id == customer.id,
        )
        .order_by(ReturnRequest.id.desc())
        .limit(40)
    ).all()
    return_ids = [request.id for request, _order in return_rows]
    pickups = {
        pickup.return_request_id: pickup
        for pickup in (
            session.execute(
                select(ReturnPickup)
                .where(ReturnPickup.return_request_id.in_(return_ids))
                .order_by(ReturnPickup.id.desc())
            ).scalars().all()
            if return_ids
            else []
        )
    }
    appeals = {
        appeal.return_request_id: appeal
        for appeal in (
            session.execute(
                select(ReturnDeductionAppeal).where(
                    ReturnDeductionAppeal.return_request_id.in_(return_ids)
                )
            ).scalars().all()
            if return_ids
            else []
        )
    }
    for request, order in return_rows:
        status_label = RETURN_STATUS_LABELS.get(request.status, request.status)
        resolution_label = "换货" if request.resolution == "exchange" else "退款"
        events.append(
            _event(
                "return",
                request.created_at,
                "退货申请已提交",
                f"{resolution_label} · {request.reason or '未填写原因'}",
                "requested",
                order.order_no,
                request.id,
            )
        )
        events.append(
            _event(
                "return",
                request.approved_at,
                "退货申请已通过",
                request.return_address or "等待商家提供回寄地址",
                "approved",
                order.order_no,
                request.id,
            )
        )
        for tracking in request.tracking_events:
            events.append(
                _event(
                    "return_tracking",
                    tracking.happened_at,
                    f"退货物流 {RETURN_TRACKING_LABELS.get(tracking.status, tracking.status)}",
                    tracking.description,
                    tracking.status,
                    order.order_no,
                    request.id,
                )
            )
        if request.received_at is not None:
            detail_parts = [f"当前状态 {status_label}"]
            if request.inspection_deduction_cents > 0:
                detail_parts.append(
                    f"质检扣减 {format_cents(request.inspection_deduction_cents)} 元"
                )
            if request.refund_amount_cents is not None:
                detail_parts.append(
                    f"退款 {format_cents(request.refund_amount_cents)} 元"
                )
            if request.replacement_order_id is not None:
                detail_parts.append("换货订单已创建")
            events.append(
                _event(
                    "return",
                    request.received_at,
                    "退货已收货",
                    " · ".join(detail_parts),
                    "received",
                    order.order_no,
                    request.id,
                )
            )
        if request.exchange_paid_at is not None:
            events.append(
                _event(
                    "return",
                    request.exchange_paid_at,
                    "换货补差价已支付",
                    f"{format_cents(request.exchange_supplement_cents)} 元 · "
                    f"{request.exchange_payment_ref}",
                    "paid",
                    order.order_no,
                    request.id,
                )
            )
        if request.status == "exchange_cancelled":
            cancel_parts = [
                request.exchange_cancel_reason or "换货已取消",
                {
                    "customer": "顾客操作",
                    "staff": "商家操作",
                    "system": "系统自动",
                }.get(request.exchange_cancelled_by, "换货取消"),
            ]
            if request.refund_amount_cents is not None:
                if request.refund_proposal_id is not None:
                    cancel_parts.append(
                        f"退款提案 #{request.refund_proposal_id} "
                        f"{format_cents(request.refund_amount_cents)} 元待审批"
                    )
                else:
                    cancel_parts.append(
                        f"退款 {format_cents(request.refund_amount_cents)} 元"
                    )
            events.append(
                _event(
                    "return",
                    request.exchange_cancelled_at or request.updated_at,
                    "换货已取消",
                    " · ".join(cancel_parts),
                    "exchange_cancelled",
                    order.order_no,
                    request.id,
                )
            )
        if request.status == "rejected":
            events.append(
                _event(
                    "return",
                    request.updated_at,
                    "退货申请被驳回",
                    request.rejected_reason or "商家未填写原因",
                    "rejected",
                    order.order_no,
                    request.id,
                )
            )
        if request.status == "withdrawn":
            events.append(
                _event(
                    "return",
                    request.updated_at,
                    "退货申请已撤回",
                    "顾客主动撤回",
                    "withdrawn",
                    order.order_no,
                    request.id,
                )
            )
        pickup = pickups.get(request.id)
        if pickup is not None:
            events.append(
                _event(
                    "return_pickup",
                    pickup.created_at,
                    "已预约上门取件",
                    f"{pickup.scheduled_date} {pickup.time_window} · "
                    f"{pickup.pickup_no}",
                    pickup.status,
                    order.order_no,
                    request.id,
                )
            )
            events.append(
                _event(
                    "return_pickup",
                    pickup.picked_up_at,
                    "上门取件已揽收",
                    f"{pickup.courier_name} {pickup.pickup_no}",
                    "picked_up",
                    order.order_no,
                    request.id,
                )
            )
            events.append(
                _event(
                    "return_pickup",
                    pickup.cancelled_at,
                    "上门取件已取消",
                    f"{PICKUP_STATUS_LABELS.get(pickup.status, pickup.status)}"
                    + (f" · {pickup.cancel_reason}" if pickup.cancel_reason else ""),
                    "cancelled",
                    order.order_no,
                    request.id,
                )
            )
        appeal = appeals.get(request.id)
        if appeal is not None:
            events.append(
                _event(
                    "return_appeal",
                    appeal.created_at,
                    "质检扣减申诉已提交",
                    f"申请 {format_cents(appeal.requested_cents)} 元 · {appeal.reason}",
                    "pending",
                    order.order_no,
                    request.id,
                )
            )
            if appeal.resolved_at is not None:
                detail = APPEAL_STATUS_LABELS.get(appeal.status, appeal.status)
                if appeal.approved_cents is not None:
                    detail += f" · 核准 {format_cents(appeal.approved_cents)} 元"
                if appeal.resolution_note:
                    detail += f" · {appeal.resolution_note}"
                events.append(
                    _event(
                        "return_appeal",
                        appeal.resolved_at,
                        f"扣减申诉{APPEAL_STATUS_LABELS.get(appeal.status, appeal.status)}",
                        detail,
                        appeal.status,
                        order.order_no,
                        request.id,
                    )
                )

    repair_rows = session.execute(
        select(RepairRequest, Order, OrderItem)
        .join(Order, Order.id == RepairRequest.order_id)
        .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
        .where(
            RepairRequest.merchant_id == customer.merchant_id,
            RepairRequest.customer_id == customer.id,
        )
        .order_by(RepairRequest.id.desc())
        .limit(30)
    ).all()
    for repair, order, item in repair_rows:
        summary = f"{item.title_snapshot} · {item.sku}"
        events.append(
            _event(
                "repair",
                repair.created_at,
                "维修申请已提交",
                f"{summary} · {repair.issue}",
                "requested",
                order.order_no,
                repair.id,
            )
        )
        events.append(
            _event(
                "repair",
                repair.approved_at,
                "维修申请已通过",
                "等待顾客寄回",
                "approved",
                order.order_no,
                repair.id,
            )
        )
        events.append(
            _event(
                "repair",
                repair.received_at,
                "维修件已收货",
                summary,
                "received",
                order.order_no,
                repair.id,
            )
        )
        events.append(
            _event(
                "repair",
                repair.repaired_at,
                "维修完成已寄回",
                f"{repair.return_carrier} {repair.return_tracking_no} · "
                f"{repair.resolution_note}",
                "return_shipped",
                order.order_no,
                repair.id,
            )
        )
        events.append(
            _event(
                "repair",
                repair.completed_at,
                "维修已完成",
                repair.resolution_note,
                "completed",
                order.order_no,
                repair.id,
            )
        )
        if repair.quote_status != "none" and repair.quote_cents is not None:
            revised = repair.quote_revised_at is not None
            events.append(
                _event(
                    "repair_quote",
                    (
                        repair.quote_revised_at
                        if revised
                        else repair.quote_created_at
                    ),
                    "维修报价已更新" if revised else "维修报价已发送",
                    f"{format_cents(repair.quote_cents)} 元 · "
                    f"{repair.quote_note}",
                    "pending",
                    order.order_no,
                    repair.id,
                )
            )
            if repair.quote_responded_at is not None:
                accepted = repair.quote_status in ("accepted", "paid")
                events.append(
                    _event(
                        "repair_quote",
                        repair.quote_responded_at,
                        (
                            "维修报价已接受"
                            if accepted
                            else "维修报价已拒绝"
                        ),
                        f"{format_cents(repair.quote_cents)} 元",
                        "accepted" if accepted else "declined",
                        order.order_no,
                        repair.id,
                    )
                )
            if repair.quote_paid_at is not None:
                events.append(
                    _event(
                        "repair_quote",
                        repair.quote_paid_at,
                        "维修费已支付",
                        f"{format_cents(repair.quote_cents)} 元 · "
                        f"{repair.quote_payment_ref}",
                        "paid",
                        order.order_no,
                        repair.id,
                    )
                )
        if repair.status == "rejected":
            events.append(
                _event(
                    "repair",
                    repair.updated_at,
                    "维修申请被驳回",
                    repair.rejected_reason or "商家未填写原因",
                    "rejected",
                    order.order_no,
                    repair.id,
                )
            )
        if repair.status == "withdrawn":
            events.append(
                _event(
                    "repair",
                    repair.updated_at,
                    "维修申请已撤回",
                    "顾客主动撤回",
                    "withdrawn",
                    order.order_no,
                    repair.id,
                )
            )

    proposal_rows = session.execute(
        select(ActionProposal, AfterSalesCase, Order)
        .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
        .join(Order, Order.id == AfterSalesCase.order_id)
        .where(
            ActionProposal.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
        .order_by(ActionProposal.id.desc())
        .limit(30)
    ).all()
    for proposal, _case, order in proposal_rows:
        action_label = ACTION_LABELS.get(
            proposal.action_type, proposal.action_type
        )
        amount = proposal.payload_json.get("amount_cents")
        detail = (
            f"{format_cents(int(amount))} 元"
            if isinstance(amount, int)
            else PROPOSAL_STATUS_LABELS.get(
                proposal.status, proposal.status
            )
        )
        events.append(
            _event(
                "proposal",
                proposal.created_at,
                f"{action_label}提案已提交",
                detail,
                proposal.status,
                order.order_no,
                proposal.id,
            )
        )
        events.append(
            _event(
                "proposal",
                proposal.executed_at,
                f"{action_label}提案已执行",
                detail,
                "executed",
                order.order_no,
                proposal.id,
            )
        )
        if proposal.status in ("rejected", "withdrawn", "failed", "expired"):
            events.append(
                _event(
                    "proposal",
                    proposal.updated_at,
                    f"{action_label}提案"
                    f"{PROPOSAL_STATUS_LABELS.get(proposal.status, proposal.status)}",
                    proposal.error or "",
                    proposal.status,
                    order.order_no,
                    proposal.id,
                )
            )

    refund_rows = session.execute(
        select(Refund, Order)
        .join(Order, Order.id == Refund.order_id)
        .where(
            Refund.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
        .order_by(Refund.id.desc())
        .limit(30)
    ).all()
    for refund, order in refund_rows:
        refund_label = "补偿" if refund.kind == "compensation" else "退款"
        event_kind = (
            "compensation" if refund.kind == "compensation" else "refund"
        )
        settlement_label = {
            "processing": "渠道处理中",
            "succeeded": "渠道已到账",
            "failed": "渠道失败",
        }.get(refund.settlement_status, refund.settlement_status)
        events.append(
            _event(
                event_kind,
                refund.executed_at or refund.created_at,
                f"{refund_label}已执行",
                f"{format_cents(refund.amount_cents)} 元 · "
                f"{refund.settlement_reference or refund.provider_refund_id or settlement_label}",
                refund.settlement_status,
                order.order_no,
                refund.id,
            )
        )
        if refund.settled_at is not None:
            events.append(
                _event(
                    event_kind,
                    refund.settled_at,
                    f"{refund_label}已到账",
                    f"{format_cents(refund.amount_cents)} 元 · "
                    f"{refund.settlement_reference}",
                    "succeeded",
                    order.order_no,
                    refund.id,
                )
            )

    invoice_rows = session.execute(
        select(InvoiceRequest, Order)
        .join(Order, Order.id == InvoiceRequest.order_id)
        .where(
            InvoiceRequest.merchant_id == customer.merchant_id,
            InvoiceRequest.customer_id == customer.id,
        )
        .order_by(InvoiceRequest.id.desc())
        .limit(20)
    ).all()
    for invoice, order in invoice_rows:
        events.append(
            _event(
                "invoice",
                invoice.created_at,
                "发票申请已提交",
                f"{invoice.title} · {format_cents(invoice.amount_cents)} 元",
                "requested",
                order.order_no,
                invoice.id,
            )
        )
        events.append(
            _event(
                "invoice",
                invoice.issued_at,
                "发票已开具",
                invoice.invoice_no,
                "issued",
                order.order_no,
                invoice.id,
            )
        )
        events.append(
            _event(
                "invoice",
                invoice.voided_at,
                "发票已作废",
                invoice.void_reason,
                "voided",
                order.order_no,
                invoice.id,
            )
        )

    handoff_rows = session.execute(
        select(HandoffTicket)
        .where(
            HandoffTicket.merchant_id == customer.merchant_id,
            HandoffTicket.customer_id == customer.id,
        )
        .order_by(HandoffTicket.id.desc())
        .limit(20)
    ).scalars().all()
    for ticket in handoff_rows:
        events.append(
            _event(
                "handoff",
                ticket.created_at,
                "已转人工客服",
                f"工单 TK-{ticket.id:06d} · "
                f"{HANDOFF_STATUS_LABELS.get(ticket.status, ticket.status)}",
                ticket.status,
                None,
                ticket.id,
            )
        )
        events.append(
            _event(
                "handoff",
                ticket.resolved_at,
                "人工工单已解决",
                ticket.resolution_note,
                "resolved",
                None,
                ticket.id,
            )
        )

    ordered = sorted(
        (event for event in events if event is not None),
        key=lambda event: (event.occurred_at, event.entity_id or 0),
        reverse=True,
    )
    return ordered[: max(1, min(limit, 200))]
