from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Customer,
    Order,
    OrderItem,
    RepairRequest,
    Shipment,
    ShipmentEvent,
)
from app.domain.money import format_cents
from app.services.after_sales_charges import record_after_sales_charge
from app.services.attachments import validate_attachment_ids
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.notifications import notify_customer, notify_staff
from app.settings import settings

ACTIVE_STATUSES = (
    "requested",
    "approved",
    "in_transit",
    "received",
    "return_shipped",
)


@dataclass(frozen=True)
class RepairView:
    id: int
    order_no: str
    sku: str
    title: str
    status: str
    issue: str
    attachment_ids: tuple[int, ...]
    carrier: str
    tracking_no: str
    return_carrier: str
    return_tracking_no: str
    return_address: str
    rejected_reason: str
    resolution_note: str
    quote_cents: int | None
    quote_status: str
    quote_note: str
    quote_created_at: datetime | None
    quote_revised_at: datetime | None
    quote_responded_at: datetime | None
    quote_paid_at: datetime | None
    quote_payment_ref: str
    parts_cost_cents: int
    parts_note: str
    created_at: datetime
    approved_at: datetime | None
    received_at: datetime | None
    repaired_at: datetime | None
    completed_at: datetime | None


def _view(repair: RepairRequest, order: Order, item: OrderItem) -> RepairView:
    return RepairView(
        id=repair.id,
        order_no=order.order_no,
        sku=item.sku,
        title=item.title_snapshot,
        status=repair.status,
        issue=repair.issue,
        attachment_ids=tuple(repair.attachment_ids or []),
        carrier=repair.carrier,
        tracking_no=repair.tracking_no,
        return_carrier=repair.return_carrier,
        return_tracking_no=repair.return_tracking_no,
        return_address=(
            settings.return_address
            if repair.status in ("approved", "in_transit")
            else ""
        ),
        rejected_reason=repair.rejected_reason,
        resolution_note=repair.resolution_note,
        quote_cents=repair.quote_cents,
        quote_status=repair.quote_status,
        quote_note=repair.quote_note,
        quote_created_at=repair.quote_created_at,
        quote_revised_at=repair.quote_revised_at,
        quote_responded_at=repair.quote_responded_at,
        quote_paid_at=repair.quote_paid_at,
        quote_payment_ref=repair.quote_payment_ref,
        parts_cost_cents=repair.parts_cost_cents,
        parts_note=repair.parts_note,
        created_at=repair.created_at,
        approved_at=repair.approved_at,
        received_at=repair.received_at,
        repaired_at=repair.repaired_at,
        completed_at=repair.completed_at,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _add_months(value: datetime, months: int) -> datetime:
    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _delivered_at(session: Session, order: Order) -> datetime | None:
    shipments = session.execute(
        select(Shipment).where(Shipment.order_id == order.id)
    ).scalars().all()
    delivered: list[datetime] = []
    for shipment in shipments:
        if shipment.delivered_at is not None:
            delivered_at = _as_utc(shipment.delivered_at)
            if delivered_at is not None:
                delivered.append(delivered_at)
        elif shipment.status == "delivered":
            last_event = session.execute(
                select(func.max(ShipmentEvent.happened_at)).where(
                    ShipmentEvent.shipment_id == shipment.id
                )
            ).scalar_one()
            event_at = _as_utc(last_event)
            if event_at is not None:
                delivered.append(event_at)
    return max(delivered) if delivered else None


def _repair_for_update(
    session: Session, merchant_id: int, repair_id: int
) -> tuple[RepairRequest, Order, OrderItem]:
    row = session.execute(
        select(RepairRequest, Order, OrderItem)
        .join(Order, Order.id == RepairRequest.order_id)
        .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
        .where(
            RepairRequest.id == repair_id,
            RepairRequest.merchant_id == merchant_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("维修申请不存在")
    return row


@dataclass(frozen=True)
class RepairEligibilityItem:
    sku: str
    title: str
    warranty_months: int
    deadline: datetime | None
    eligible: bool
    reason: str


@dataclass(frozen=True)
class RepairEligibility:
    order_no: str
    delivered_at: datetime | None
    items: tuple[RepairEligibilityItem, ...]


def _item_eligibility(
    order: Order,
    item: OrderItem,
    delivered_at: datetime | None,
    active_item_ids: set[int],
    now: datetime,
) -> tuple[datetime | None, str]:
    months = item.warranty_months
    deadline = (
        _add_months(delivered_at, months)
        if delivered_at is not None and months > 0
        else None
    )
    if order.status not in ("delivered", "completed"):
        return deadline, "只有已签收订单可以申请维修"
    if order.payment_status not in ("paid", "partially_refunded"):
        return deadline, "当前支付状态不能申请维修"
    if delivered_at is None:
        return deadline, "订单缺少签收时间，请联系人工处理"
    if months <= 0:
        return deadline, "该商品不提供保修"
    if now > deadline:
        return deadline, f"已超出保修期（签收后 {months} 个月）"
    if item.id in active_item_ids:
        return deadline, "该商品已有进行中的维修申请"
    return deadline, ""


def repair_eligibility(
    session: Session,
    customer: Customer,
    order_no: str,
) -> RepairEligibility:
    order = session.execute(
        select(Order).where(
            Order.order_no == order_no,
            Order.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")
    delivered_at = _delivered_at(session, order)
    items = session.execute(
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
    ).scalars().all()
    item_ids = [item.id for item in items]
    active_ids = set(
        session.execute(
            select(RepairRequest.order_item_id).where(
                RepairRequest.order_item_id.in_(item_ids),
                RepairRequest.status.in_(ACTIVE_STATUSES),
            )
        ).scalars()
    ) if item_ids else set()
    now = datetime.now(UTC)
    views = []
    for item in items:
        if item.cancelled_qty >= item.qty:
            continue
        deadline, reason = _item_eligibility(
            order, item, delivered_at, active_ids, now
        )
        views.append(
            RepairEligibilityItem(
                sku=item.sku,
                title=item.title_snapshot,
                warranty_months=item.warranty_months,
                deadline=deadline,
                eligible=not reason,
                reason=reason,
            )
        )
    return RepairEligibility(
        order_no=order.order_no,
        delivered_at=delivered_at,
        items=tuple(views),
    )


def create_repair(
    session: Session,
    customer: Customer,
    order_no: str,
    sku: str,
    issue: str,
    attachment_ids: list[int] | None = None,
) -> RepairView:
    issue_text = issue.strip()
    if not issue_text:
        raise ValidationError("请描述故障或问题")
    if len(issue_text) > 2000:
        raise ValidationError("问题描述不能超过 2000 个字符")
    try:
        order = session.execute(
            select(Order)
            .where(
                Order.order_no == order_no,
                Order.merchant_id == customer.merchant_id,
                Order.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("订单不存在")
        if order.status not in ("delivered", "completed"):
            raise ConflictError("只有已签收订单可以申请维修")
        if order.payment_status not in ("paid", "partially_refunded"):
            raise ConflictError("当前支付状态不能申请维修")
        item = session.execute(
            select(OrderItem).where(
                OrderItem.order_id == order.id,
                OrderItem.sku == sku.strip().upper(),
            )
        ).scalar_one_or_none()
        if item is None:
            raise ValidationError("商品不属于该订单")
        if item.cancelled_qty >= item.qty:
            raise ConflictError("该商品已取消，不能申请维修")
        delivered_at = _delivered_at(session, order)
        active_ids = set(
            session.execute(
                select(RepairRequest.order_item_id).where(
                    RepairRequest.order_item_id == item.id,
                    RepairRequest.status.in_(ACTIVE_STATUSES),
                )
            ).scalars()
        )
        _, reason = _item_eligibility(
            order,
            item,
            delivered_at,
            active_ids,
            datetime.now(UTC),
        )
        if reason:
            raise ConflictError(reason)
        attachments = validate_attachment_ids(
            session, customer, attachment_ids or []
        )
        repair = RepairRequest(
            merchant_id=customer.merchant_id,
            order_id=order.id,
            customer_id=customer.id,
            order_item_id=item.id,
            status="requested",
            issue=issue_text,
            attachment_ids=attachments or None,
            approved_by=None,
        )
        session.add(repair)
        session.flush()
        append_event(
            session,
            customer.merchant_id,
            "repair_requested",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "sku": item.sku,
                "issue": issue_text,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="repair_requested",
            title=f"新的维修申请 #{repair.id}",
            body=f"订单 {order.order_no} · {item.sku} · {issue_text}",
            link="/admin",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def withdraw_repair(
    session: Session, customer: Customer, repair_id: int
) -> RepairView:
    try:
        row = session.execute(
            select(RepairRequest, Order, OrderItem)
            .join(Order, Order.id == RepairRequest.order_id)
            .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
            .where(
                RepairRequest.id == repair_id,
                RepairRequest.merchant_id == customer.merchant_id,
                RepairRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("维修申请不存在")
        repair, order, item = row
        if repair.status != "requested":
            raise ConflictError("只有待审核维修可以撤回")
        repair.status = "withdrawn"
        append_event(
            session,
            customer.merchant_id,
            "repair_withdrawn",
            {"repair_id": repair.id, "order_no": order.order_no},
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="repair_withdrawn",
            title=f"顾客撤回维修申请 #{repair.id}",
            body=f"订单 {order.order_no} · {item.sku}",
            link="/admin",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def approve_repair(
    session: Session, merchant_id: int, user_id: int, repair_id: int
) -> RepairView:
    try:
        repair, order, item = _repair_for_update(session, merchant_id, repair_id)
        if repair.status != "requested":
            raise ConflictError("只有待审核维修可以批准")
        repair.status = "approved"
        repair.approved_by = user_id
        repair.approved_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "repair_approved",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            repair.customer_id,
            notification_type="repair_approved",
            title="维修申请已批准",
            body=(
                f"订单 {order.order_no} · {item.sku} 已批准维修，"
                "请按售后页提示寄回商品"
            ),
            link="/after-sales",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def reject_repair(
    session: Session,
    merchant_id: int,
    user_id: int,
    repair_id: int,
    reason: str,
) -> RepairView:
    reason_text = reason.strip()
    try:
        repair, order, item = _repair_for_update(session, merchant_id, repair_id)
        if repair.status != "requested":
            raise ConflictError("只有待审核维修可以驳回")
        repair.status = "rejected"
        repair.rejected_reason = reason_text
        append_event(
            session,
            merchant_id,
            "repair_rejected",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "reason": reason_text,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            repair.customer_id,
            notification_type="repair_rejected",
            title="维修申请未通过",
            body=f"订单 {order.order_no} · {reason_text or '未说明原因'}",
            link="/after-sales",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def add_repair_tracking(
    session: Session,
    customer: Customer,
    repair_id: int,
    carrier: str,
    tracking_no: str,
) -> RepairView:
    carrier_value = carrier.strip()
    tracking_value = tracking_no.strip()
    if not carrier_value or not tracking_value:
        raise ValidationError("承运商与运单号不能为空")
    try:
        row = session.execute(
            select(RepairRequest, Order, OrderItem)
            .join(Order, Order.id == RepairRequest.order_id)
            .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
            .where(
                RepairRequest.id == repair_id,
                RepairRequest.merchant_id == customer.merchant_id,
                RepairRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("维修申请不存在")
        repair, order, item = row
        if repair.status != "approved":
            raise ConflictError("只有已批准维修可以登记回寄")
        repair.carrier = carrier_value
        repair.tracking_no = tracking_value
        repair.status = "in_transit"
        append_event(
            session,
            customer.merchant_id,
            "repair_tracking_added",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "carrier": carrier_value,
                "tracking_no": tracking_value,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="repair_tracking_added",
            title=f"顾客已寄回维修件 #{repair.id}",
            body=f"订单 {order.order_no} · {carrier_value} {tracking_value}",
            link="/admin",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def receive_repair(
    session: Session, merchant_id: int, user_id: int, repair_id: int
) -> RepairView:
    try:
        repair, order, item = _repair_for_update(session, merchant_id, repair_id)
        if repair.status not in ("approved", "in_transit"):
            raise ConflictError("当前维修状态不能确认收货")
        repair.status = "received"
        repair.received_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "repair_received",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            repair.customer_id,
            notification_type="repair_received",
            title="维修件已签收",
            body=f"订单 {order.order_no} · {item.sku} 已签收，维修处理中",
            link="/after-sales",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def quote_repair(
    session: Session,
    merchant_id: int,
    user_id: int,
    repair_id: int,
    amount_cents: int,
    note: str,
) -> RepairView:
    if amount_cents <= 0:
        raise ValidationError("维修报价金额必须大于 0")
    note_value = note.strip()
    if not note_value:
        raise ValidationError("请填写报价说明")
    if len(note_value) > 1000:
        raise ValidationError("报价说明不能超过 1000 个字符")
    try:
        repair, order, item = _repair_for_update(session, merchant_id, repair_id)
        if repair.status != "received":
            raise ConflictError("只有已收货维修可以报价")
        if repair.quote_status not in ("none", "pending", "declined"):
            raise ConflictError("报价已确认或已支付，不能修改")
        is_revision = repair.quote_status != "none"
        old_amount = repair.quote_cents or 0
        old_status = repair.quote_status
        now = datetime.now(UTC)
        repair.quote_cents = amount_cents
        repair.quote_status = "pending"
        repair.quote_note = note_value
        if is_revision:
            repair.quote_revised_at = now
        else:
            repair.quote_created_at = now
        repair.quote_responded_at = None
        repair.quote_paid_at = None
        repair.quote_payment_ref = ""
        repair.quote_reminded_at = None
        append_event(
            session,
            merchant_id,
            "repair_quote_revised" if is_revision else "repair_quote_issued",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "amount_cents": amount_cents,
                "note": note_value,
                "operator_user_id": user_id,
                **(
                    {
                        "old_amount_cents": old_amount,
                        "old_status": old_status,
                    }
                    if is_revision
                    else {}
                ),
            },
        )
        notify_customer(
            session,
            merchant_id,
            repair.customer_id,
            notification_type=(
                "repair_quote_updated"
                if is_revision
                else "repair_quote_issued"
            ),
            title="维修报价已更新" if is_revision else "维修报价待确认",
            body=(
                f"订单 {order.order_no} · {item.sku} · "
                + (
                    f"原报价 {format_cents(old_amount)} 元 → "
                    if is_revision
                    else ""
                )
                + f"维修费 {format_cents(amount_cents)} 元 · {note_value}"
            ),
            link="/after-sales",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def _repair_quote_for_customer(
    session: Session, customer: Customer, repair_id: int
) -> tuple[RepairRequest, Order, OrderItem]:
    row = session.execute(
        select(RepairRequest, Order, OrderItem)
        .join(Order, Order.id == RepairRequest.order_id)
        .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
        .where(
            RepairRequest.id == repair_id,
            RepairRequest.merchant_id == customer.merchant_id,
            RepairRequest.customer_id == customer.id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("维修申请不存在")
    return row


def accept_repair_quote(
    session: Session, customer: Customer, repair_id: int
) -> RepairView:
    try:
        repair, order, item = _repair_quote_for_customer(
            session, customer, repair_id
        )
        if repair.status != "received" or repair.quote_status != "pending":
            raise ConflictError("当前维修报价不能接受")
        repair.quote_status = "accepted"
        repair.quote_responded_at = datetime.now(UTC)
        append_event(
            session,
            customer.merchant_id,
            "repair_quote_accepted",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "amount_cents": repair.quote_cents,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="repair_quote_accepted",
            title=f"顾客接受维修报价 #{repair.id}",
            body=(
                f"订单 {order.order_no} · 维修费 "
                f"{format_cents(repair.quote_cents or 0)} 元"
            ),
            link="/admin",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def decline_repair_quote(
    session: Session, customer: Customer, repair_id: int
) -> RepairView:
    try:
        repair, order, item = _repair_quote_for_customer(
            session, customer, repair_id
        )
        if repair.status != "received" or repair.quote_status != "pending":
            raise ConflictError("当前维修报价不能拒绝")
        repair.quote_status = "declined"
        repair.quote_responded_at = datetime.now(UTC)
        append_event(
            session,
            customer.merchant_id,
            "repair_quote_declined",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "amount_cents": repair.quote_cents,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="repair_quote_declined",
            title=f"顾客拒绝维修报价 #{repair.id}",
            body=f"订单 {order.order_no} · 请按原样寄回维修件",
            link="/admin",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def pay_repair_quote(
    session: Session, customer: Customer, repair_id: int
) -> RepairView:
    try:
        repair, order, item = _repair_quote_for_customer(
            session, customer, repair_id
        )
        if repair.status != "received" or repair.quote_status != "accepted":
            raise ConflictError("当前维修报价不能支付")
        if repair.quote_paid_at is not None:
            raise ConflictError("维修费已支付")
        now = datetime.now(UTC)
        repair.quote_status = "paid"
        repair.quote_paid_at = now
        repair.quote_payment_ref = f"RPQ-{uuid4().hex[:12].upper()}"
        record_after_sales_charge(
            session,
            merchant_id=customer.merchant_id,
            customer_id=repair.customer_id,
            order_id=order.id,
            kind="repair_fee",
            amount_cents=repair.quote_cents or 0,
            reference=repair.quote_payment_ref,
            repair_request_id=repair.id,
        )
        append_event(
            session,
            customer.merchant_id,
            "repair_quote_paid",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "amount_cents": repair.quote_cents,
                "payment_ref": repair.quote_payment_ref,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="repair_quote_paid",
            title=f"顾客已支付维修费 #{repair.id}",
            body=(
                f"订单 {order.order_no} · "
                f"{format_cents(repair.quote_cents or 0)} 元 · "
                f"{repair.quote_payment_ref}"
            ),
            link="/admin",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def complete_repair(
    session: Session,
    merchant_id: int,
    user_id: int,
    repair_id: int,
    resolution_note: str,
    return_carrier: str,
    return_tracking_no: str,
    parts_cost_cents: int = 0,
    parts_note: str = "",
) -> RepairView:
    note = resolution_note.strip()
    if not note:
        raise ValidationError("请填写维修处理结果")
    carrier_value = return_carrier.strip()
    tracking_value = return_tracking_no.strip()
    if not carrier_value or not tracking_value:
        raise ValidationError("请填写维修寄回承运商与运单号")
    if parts_cost_cents < 0:
        raise ValidationError("配件成本不能为负数")
    parts_note_value = parts_note.strip()
    if len(parts_note_value) > 500:
        raise ValidationError("配件说明不能超过 500 个字符")
    try:
        repair, order, item = _repair_for_update(session, merchant_id, repair_id)
        if repair.status != "received":
            raise ConflictError("只有已收货维修可以完成")
        if repair.quote_status in ("pending", "accepted"):
            raise ConflictError(
                "维修报价待顾客确认或支付，暂不能完成"
            )
        now = datetime.now(UTC)
        repair.status = "return_shipped"
        repair.repaired_at = now
        repair.resolution_note = note
        repair.return_carrier = carrier_value
        repair.return_tracking_no = tracking_value
        repair.parts_cost_cents = parts_cost_cents
        repair.parts_note = parts_note_value
        append_event(
            session,
            merchant_id,
            "repair_completed",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "resolution_note": note,
                "return_carrier": carrier_value,
                "return_tracking_no": tracking_value,
                "parts_cost_cents": parts_cost_cents,
                "parts_note": parts_note_value,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            repair.customer_id,
            notification_type="repair_returned",
            title="维修完成，已寄回",
            body=(
                f"订单 {order.order_no} · {carrier_value} "
                f"{tracking_value} · 请收到后确认"
            ),
            link="/after-sales",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def confirm_repair_received(
    session: Session,
    customer: Customer,
    repair_id: int,
) -> RepairView:
    try:
        row = session.execute(
            select(RepairRequest, Order, OrderItem)
            .join(Order, Order.id == RepairRequest.order_id)
            .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
            .where(
                RepairRequest.id == repair_id,
                RepairRequest.merchant_id == customer.merchant_id,
                RepairRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("维修申请不存在")
        repair, order, item = row
        if repair.status != "return_shipped":
            raise ConflictError("只有已寄回维修可以确认收到")
        repair.status = "completed"
        repair.completed_at = datetime.now(UTC)
        append_event(
            session,
            customer.merchant_id,
            "repair_customer_received",
            {
                "repair_id": repair.id,
                "order_no": order.order_no,
                "return_tracking_no": repair.return_tracking_no,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="repair_customer_received",
            title=f"顾客已确认收到维修件 #{repair.id}",
            body=f"订单 {order.order_no} · {item.sku}",
            link="/admin",
        )
        session.commit()
        return _view(repair, order, item)
    except Exception:
        session.rollback()
        raise


def list_repairs_for_customer(
    session: Session, customer: Customer
) -> list[RepairView]:
    rows = session.execute(
        select(RepairRequest, Order, OrderItem)
        .join(Order, Order.id == RepairRequest.order_id)
        .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
        .where(
            RepairRequest.merchant_id == customer.merchant_id,
            RepairRequest.customer_id == customer.id,
        )
        .order_by(RepairRequest.id.desc())
        .limit(50)
    ).all()
    return [_view(repair, order, item) for repair, order, item in rows]


def list_repairs_for_admin(
    session: Session, merchant_id: int, status: str = "active"
) -> list[RepairView]:
    stmt = (
        select(RepairRequest, Order, OrderItem)
        .join(Order, Order.id == RepairRequest.order_id)
        .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
        .where(RepairRequest.merchant_id == merchant_id)
    )
    if status == "active":
        stmt = stmt.where(RepairRequest.status.in_(ACTIVE_STATUSES))
    elif status != "all":
        stmt = stmt.where(RepairRequest.status == status)
    rows = session.execute(
        stmt.order_by(RepairRequest.id.desc()).limit(100)
    ).all()
    return [_view(repair, order, item) for repair, order, item in rows]
