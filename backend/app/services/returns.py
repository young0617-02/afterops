from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Customer,
    Inventory,
    InventoryReservation,
    Order,
    OrderItem,
    Product,
    ReturnDeductionAppeal,
    ReturnItem,
    ReturnPickup,
    ReturnRequest,
    ReturnTrackingEvent,
    Shipment,
    ShipmentEvent,
)
from app.domain.money import format_cents
from app.services.after_sales_charges import record_after_sales_charge
from app.services.attachments import (
    save_staff_attachment,
    validate_attachment_ids,
)
from app.services.audit import append_event
from app.services.customer_risk import customer_after_sales_risk_map
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.inventory_ledger import record_movement
from app.services.notifications import notify_customer, notify_staff
from app.services.proposals import create_refund_proposal, refunded_total
from app.services.return_policy import ReturnPolicyView, get_return_policy
from app.services.return_reasons import normalize_reason_category
from app.settings import settings

ACTIVE_STATUSES = ("requested", "approved", "in_transit")
APPEAL_WINDOW_DAYS = 7
PICKUP_WINDOW_DAYS = 7
PICKUP_WINDOWS = ("09:00-12:00", "13:00-18:00", "18:00-21:00")

RETURN_TRACKING_LABELS = {
    "registered": "已登记回寄",
    "picked_up": "已揽收",
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "delivered": "已到达退货仓",
    "exception": "物流异常",
}
RETURN_TRACKING_FLOW = (
    "registered",
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
)
RETURN_TRACKING_DESCRIPTIONS = {
    "registered": "顾客已登记回寄信息，等待承运商揽收",
    "picked_up": "承运商已揽收退货包裹",
    "in_transit": "退货包裹运输中",
    "out_for_delivery": "退货包裹已到达退货仓网点，正在派送",
    "delivered": "退货包裹已到达退货仓，等待商家验收",
}


@dataclass(frozen=True)
class ReturnItemEligibility:
    sku: str
    title: str
    category: str
    window_days: int
    deadline: datetime | None
    eligible: bool
    reason: str


@dataclass(frozen=True)
class ReturnEligibility:
    eligible: bool
    reason: str
    window_days: int
    return_shipping_fee_cents: int
    delivered_at: datetime | None
    deadline: datetime | None
    allow_exchange: bool
    final_sale_skus: tuple[str, ...]
    items: tuple[ReturnItemEligibility, ...]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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


def _order_eligibility(
    session: Session,
    order: Order,
    resolution: str,
    policy: ReturnPolicyView,
) -> ReturnEligibility:
    delivered_at = _delivered_at(session, order)
    default_deadline = (
        delivered_at + timedelta(days=policy.window_days)
        if delivered_at is not None
        else None
    )
    category_windows = {
        item.category: item.window_days for item in policy.category_windows
    }
    final_sale_categories = set(policy.final_sale_categories)
    rows = session.execute(
        select(OrderItem, Product.category)
        .join(Product, Product.id == OrderItem.product_id)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
    ).all()
    now = datetime.now(UTC)
    active = session.execute(
        select(ReturnRequest.id).where(
            ReturnRequest.order_id == order.id,
            ReturnRequest.status.in_(ACTIVE_STATUSES),
        )
    ).scalar_one_or_none()
    order_block = ""
    if order.status not in ("delivered", "completed"):
        order_block = "只有已签收订单可以申请退货"
    elif order.payment_status not in ("paid", "partially_refunded"):
        order_block = "当前支付状态不能申请退货"
    elif active is not None:
        order_block = "该订单已有进行中的退货申请"

    items: list[ReturnItemEligibility] = []
    for item, category in rows:
        if item.qty - item.cancelled_qty <= 0:
            continue
        window_days = category_windows.get(category, policy.window_days)
        deadline = (
            delivered_at + timedelta(days=window_days)
            if delivered_at is not None
            else None
        )
        reason = order_block
        if not reason:
            if delivered_at is None:
                reason = "订单缺少签收时间，请联系人工处理"
            elif category in final_sale_categories:
                reason = f"商品 {item.sku} 属于不支持退货类目"
            elif deadline is not None and now > deadline:
                reason = (
                    f"商品 {item.sku} 已超过退货期限（类目窗口 "
                    f"{window_days} 天，截止 {deadline.astimezone(UTC):%Y-%m-%d}）"
                )
        items.append(
            ReturnItemEligibility(
                sku=item.sku,
                title=item.title_snapshot,
                category=category,
                window_days=window_days,
                deadline=deadline,
                eligible=not reason,
                reason=reason,
            )
        )
    final_sale_skus = tuple(
        item.sku for item, category in rows
        if category in final_sale_categories
        and item.qty - item.cancelled_qty > 0
    )
    eligible_items = [item for item in items if item.eligible]
    reason = ""
    if not eligible_items:
        reason = order_block or (
            items[0].reason if items else "订单没有可退商品"
        )
    elif resolution == "exchange" and not policy.allow_exchange:
        reason = "商家未开放换货，仅支持退货退款"
    return ReturnEligibility(
        eligible=not reason,
        reason=reason,
        window_days=policy.window_days,
        return_shipping_fee_cents=policy.return_shipping_fee_cents,
        delivered_at=delivered_at,
        deadline=default_deadline,
        allow_exchange=policy.allow_exchange,
        final_sale_skus=final_sale_skus,
        items=tuple(items),
    )


def return_eligibility(
    session: Session,
    customer: Customer,
    order_no: str,
    resolution: str = "refund",
) -> ReturnEligibility:
    if resolution not in ("refund", "exchange"):
        raise ValidationError("未知退货处理方式")
    order = session.execute(
        select(Order).where(
            Order.order_no == order_no,
            Order.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")
    return _order_eligibility(
        session,
        order,
        resolution,
        get_return_policy(session, customer.merchant_id),
    )


@dataclass(frozen=True)
class ReturnItemView:
    id: int
    sku: str
    title: str
    qty: int
    exchange_sku: str | None
    exchange_unit_price_cents: int | None
    disposition: str | None
    inspection_deduction_cents: int
    responsibility: str | None


@dataclass(frozen=True)
class ReturnTrackingEventView:
    status: str
    label: str
    description: str
    happened_at: datetime


@dataclass(frozen=True)
class ReplacementShipmentEventView:
    node: str
    description: str
    happened_at: datetime


@dataclass(frozen=True)
class ReplacementShipmentView:
    id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    events: tuple[ReplacementShipmentEventView, ...]


@dataclass(frozen=True)
class ReturnAppealView:
    id: int
    status: str
    reason: str
    attachment_ids: tuple[int, ...]
    requested_cents: int
    approved_cents: int | None
    resolution_note: str
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class ReturnPickupView:
    id: int
    pickup_no: str
    status: str
    scheduled_date: date
    time_window: str
    address_recipient: str
    address_phone: str
    address_region: str
    address_city: str
    address_detail: str
    courier_name: str
    courier_phone: str
    cancel_reason: str
    picked_up_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ReturnView:
    id: int
    order_no: str
    customer_phone: str | None
    customer_risk_level: str | None
    customer_risk_reasons: tuple[str, ...]
    status: str
    reason: str
    reason_category: str
    resolution: str
    exchange_sku: str | None
    exchange_unit_price_cents: int | None
    replacement_order_no: str | None
    replacement_shipments: tuple[ReplacementShipmentView, ...]
    exchange_supplement_cents: int
    exchange_payment_status: str
    exchange_payment_ref: str
    exchange_paid_at: datetime | None
    exchange_cancelled_at: datetime | None
    exchange_cancel_reason: str
    exchange_cancelled_by: str
    exchange_payment_deadline: datetime | None
    return_address: str
    carrier: str
    tracking_no: str
    shipment_status: str
    tracking_events: tuple[ReturnTrackingEventView, ...]
    rejected_reason: str
    refund_proposal_id: int | None
    refund_amount_cents: int | None
    inspection_deduction_cents: int
    inspection_note: str
    attachment_ids: tuple[int, ...]
    inspection_attachment_ids: tuple[int, ...]
    created_at: datetime
    approved_at: datetime | None
    received_at: datetime | None
    appeal: ReturnAppealView | None
    appeal_deadline: datetime | None
    can_appeal: bool
    pickup: ReturnPickupView | None
    can_schedule_pickup: bool
    items: tuple[ReturnItemView, ...]


def _replacement_shipments_map(
    session: Session, order_ids: list[int]
) -> dict[int, tuple[ReplacementShipmentView, ...]]:
    unique_ids = [order_id for order_id in dict.fromkeys(order_ids) if order_id]
    if not unique_ids:
        return {}
    shipments = session.execute(
        select(Shipment)
        .where(Shipment.order_id.in_(unique_ids))
        .order_by(Shipment.order_id, Shipment.package_no, Shipment.id)
    ).scalars().all()
    if not shipments:
        return {}
    events: dict[int, list[ShipmentEvent]] = {}
    for event in session.execute(
        select(ShipmentEvent)
        .where(ShipmentEvent.shipment_id.in_([shipment.id for shipment in shipments]))
        .order_by(ShipmentEvent.happened_at, ShipmentEvent.id)
    ).scalars().all():
        events.setdefault(event.shipment_id, []).append(event)
    grouped: dict[int, list[ReplacementShipmentView]] = {}
    for shipment in shipments:
        grouped.setdefault(shipment.order_id, []).append(
            ReplacementShipmentView(
                id=shipment.id,
                package_no=shipment.package_no,
                carrier=shipment.carrier,
                tracking_no=shipment.tracking_no,
                status=shipment.status,
                current_node=shipment.current_node,
                shipped_at=shipment.shipped_at,
                delivered_at=shipment.delivered_at,
                events=tuple(
                    ReplacementShipmentEventView(
                        node=event.node,
                        description=event.description,
                        happened_at=event.happened_at,
                    )
                    for event in events.get(shipment.id, [])
                ),
            )
        )
    return {order_id: tuple(items) for order_id, items in grouped.items()}


def _appeal_deadline(return_request: ReturnRequest) -> datetime | None:
    if (
        return_request.status != "received"
        or return_request.inspection_deduction_cents <= 0
        or return_request.received_at is None
    ):
        return None
    received_at = _as_utc(return_request.received_at)
    if received_at is None:
        return None
    return received_at + timedelta(days=APPEAL_WINDOW_DAYS)


def _appeal_view(
    appeal: ReturnDeductionAppeal | None,
) -> ReturnAppealView | None:
    if appeal is None:
        return None
    return ReturnAppealView(
        id=appeal.id,
        status=appeal.status,
        reason=appeal.reason,
        attachment_ids=tuple(appeal.attachment_ids or []),
        requested_cents=appeal.requested_cents,
        approved_cents=appeal.approved_cents,
        resolution_note=appeal.resolution_note,
        created_at=appeal.created_at,
        resolved_at=appeal.resolved_at,
    )


def _appeals_map(
    session: Session, return_ids: list[int]
) -> dict[int, ReturnDeductionAppeal]:
    unique_ids = [return_id for return_id in dict.fromkeys(return_ids) if return_id]
    if not unique_ids:
        return {}
    return {
        appeal.return_request_id: appeal
        for appeal in session.execute(
            select(ReturnDeductionAppeal).where(
                ReturnDeductionAppeal.return_request_id.in_(unique_ids)
            )
        ).scalars().all()
    }


def _pickup_view(pickup: ReturnPickup | None) -> ReturnPickupView | None:
    if pickup is None:
        return None
    return ReturnPickupView(
        id=pickup.id,
        pickup_no=pickup.pickup_no,
        status=pickup.status,
        scheduled_date=pickup.scheduled_date,
        time_window=pickup.time_window,
        address_recipient=pickup.address_recipient,
        address_phone=pickup.address_phone,
        address_region=pickup.address_region,
        address_city=pickup.address_city,
        address_detail=pickup.address_detail,
        courier_name=pickup.courier_name,
        courier_phone=pickup.courier_phone,
        cancel_reason=pickup.cancel_reason,
        picked_up_at=pickup.picked_up_at,
        cancelled_at=pickup.cancelled_at,
        created_at=pickup.created_at,
    )


def _pickups_map(
    session: Session, return_ids: list[int]
) -> dict[int, ReturnPickup]:
    unique_ids = [return_id for return_id in dict.fromkeys(return_ids) if return_id]
    if not unique_ids:
        return {}
    latest: dict[int, ReturnPickup] = {}
    for pickup in session.execute(
        select(ReturnPickup)
        .where(ReturnPickup.return_request_id.in_(unique_ids))
        .order_by(ReturnPickup.created_at.desc(), ReturnPickup.id.desc())
    ).scalars().all():
        current = latest.get(pickup.return_request_id)
        if current is None or (
            pickup.status == "scheduled" and current.status != "scheduled"
        ):
            latest[pickup.return_request_id] = pickup
    return latest


def _exchange_payment_deadline(
    return_request: ReturnRequest,
) -> datetime | None:
    if return_request.exchange_supplement_deadline is not None:
        return return_request.exchange_supplement_deadline
    timeout_hours = settings.after_sales_supplement_timeout_hours
    if (
        return_request.exchange_payment_status != "pending"
        or return_request.received_at is None
        or timeout_hours <= 0
    ):
        return None
    return return_request.received_at + timedelta(hours=timeout_hours)


def _view(
    return_request: ReturnRequest,
    order: Order,
    customer: Customer | None,
    replacement_order_no: str | None = None,
    replacement_shipments: tuple[ReplacementShipmentView, ...] = (),
    appeal: ReturnDeductionAppeal | None = None,
    pickup: ReturnPickup | None = None,
    customer_risk_level: str | None = None,
    customer_risk_reasons: tuple[str, ...] = (),
):
    appeal_deadline = _appeal_deadline(return_request)
    can_schedule_pickup = (
        return_request.status == "approved"
        and not return_request.tracking_no
        and return_request.shipment_status == "none"
        and (pickup is None or pickup.status != "scheduled")
    )
    return ReturnView(
        id=return_request.id,
        order_no=order.order_no,
        customer_phone=customer.phone if customer else None,
        customer_risk_level=customer_risk_level,
        customer_risk_reasons=customer_risk_reasons,
        status=return_request.status,
        reason=return_request.reason,
        reason_category=return_request.reason_category,
        resolution=return_request.resolution,
        exchange_sku=return_request.exchange_sku,
        exchange_unit_price_cents=return_request.exchange_unit_price_cents,
        replacement_order_no=replacement_order_no,
        replacement_shipments=replacement_shipments,
        exchange_supplement_cents=return_request.exchange_supplement_cents,
        exchange_payment_status=return_request.exchange_payment_status,
        exchange_payment_ref=return_request.exchange_payment_ref,
        exchange_paid_at=return_request.exchange_paid_at,
        exchange_cancelled_at=return_request.exchange_cancelled_at,
        exchange_cancel_reason=return_request.exchange_cancel_reason,
        exchange_cancelled_by=return_request.exchange_cancelled_by,
        exchange_payment_deadline=_exchange_payment_deadline(return_request),
        return_address=return_request.return_address,
        carrier=return_request.carrier,
        tracking_no=return_request.tracking_no,
        shipment_status=return_request.shipment_status,
        tracking_events=tuple(
            ReturnTrackingEventView(
                status=event.status,
                label=RETURN_TRACKING_LABELS.get(event.status, event.status),
                description=event.description,
                happened_at=event.happened_at,
            )
            for event in return_request.tracking_events
        ),
        rejected_reason=return_request.rejected_reason,
        refund_proposal_id=return_request.refund_proposal_id,
        refund_amount_cents=return_request.refund_amount_cents,
        inspection_deduction_cents=return_request.inspection_deduction_cents,
        inspection_note=return_request.inspection_note,
        attachment_ids=tuple(return_request.attachment_ids or []),
        inspection_attachment_ids=tuple(
            return_request.inspection_attachment_ids or []
        ),
        created_at=return_request.created_at,
        approved_at=return_request.approved_at,
        received_at=return_request.received_at,
        appeal=_appeal_view(appeal),
        appeal_deadline=appeal_deadline,
        can_appeal=(
            appeal is None
            and appeal_deadline is not None
            and datetime.now(UTC) <= appeal_deadline
        ),
        pickup=_pickup_view(pickup),
        can_schedule_pickup=can_schedule_pickup,
        items=tuple(
            ReturnItemView(
                id=item.id,
                sku=item.sku,
                title=item.title_snapshot,
                qty=item.qty,
                exchange_sku=item.exchange_sku,
                exchange_unit_price_cents=item.exchange_unit_price_cents,
                disposition=item.disposition,
                inspection_deduction_cents=item.inspection_deduction_cents,
                responsibility=item.responsibility,
            )
            for item in return_request.items
        ),
    )


@dataclass(frozen=True)
class RestockLine:
    sku: str
    warehouse_id: str
    qty: int


@dataclass(frozen=True)
class DamagedLine:
    sku: str
    title: str
    qty: int


@dataclass(frozen=True)
class ReceiveReturnResult:
    view: ReturnView
    restocked: tuple[RestockLine, ...]
    damaged: tuple[DamagedLine, ...]


def _restock_returned_items(
    session: Session,
    merchant_id: int,
    order: Order,
    request: ReturnRequest,
) -> tuple[RestockLine, ...]:
    quantities = {
        item.order_item_id: item.qty
        for item in request.items
        if item.disposition == "restock"
    }
    items = session.execute(
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
    ).scalars().all()
    reservations = {
        reservation.product_id: reservation
        for reservation in session.execute(
            select(InventoryReservation)
            .where(InventoryReservation.order_id == order.id)
            .with_for_update()
        ).scalars().all()
    }
    restocked: list[RestockLine] = []
    for item in items:
        if request.items:
            qty = quantities.get(item.id)
            if qty is None:
                continue
        else:
            qty = item.qty
        reservation = reservations.get(item.product_id)
        query = select(Inventory).where(
            Inventory.merchant_id == merchant_id,
            Inventory.product_id == item.product_id,
        )
        if reservation is not None:
            query = query.where(
                Inventory.warehouse_id == reservation.warehouse_id
            )
        inventory = session.execute(
            query.order_by(Inventory.warehouse_id).limit(1).with_for_update()
        ).scalar_one_or_none()
        if inventory is None:
            continue
        record_movement(
            session,
            inventory,
            qty,
            "return_restocked",
            order_id=order.id,
            return_id=request.id,
            note=f"退货回补 {item.sku} × {qty}",
        )
        restocked.append(
            RestockLine(
                sku=item.sku,
                warehouse_id=inventory.warehouse_id,
                qty=qty,
            )
        )
    return tuple(restocked)


def _return_refund_amount(
    session: Session,
    order: Order,
    request: ReturnRequest,
    fee_cents: int,
) -> tuple[int, str]:
    order_items = session.execute(
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
    ).scalars().all()
    returned = {item.order_item_id: item.qty for item in request.items}
    remaining = max(
        order.payable_cents - refunded_total(session, order.id), 0
    )
    is_full = not returned or (
        len(returned) == len(order_items)
        and all(
            returned.get(item.id, 0) == item.qty - item.cancelled_qty
            for item in order_items
        )
    )
    if is_full:
        gross, scope = remaining, "full"
    else:
        item_total = sum(
            item.unit_price_cents * item.qty for item in order_items
        )
        if item_total <= 0:
            gross, scope = remaining, "full"
        else:
            selected_subtotal = sum(
                item.unit_price_cents * returned.get(item.id, 0)
                for item in order_items
            )
            discount_share = (
                order.discount_cents * selected_subtotal // item_total
            )
            gross = min(selected_subtotal - discount_share, remaining)
            scope = "partial"
    return max(gross - fee_cents, 0), scope


def create_return(
    session: Session,
    customer: Customer,
    order_no: str,
    reason: str,
    attachment_ids: list[int] | None = None,
    resolution: str = "refund",
    exchange_sku: str | None = None,
    *,
    items: list[tuple[str, int, str | None]] | None = None,
    reason_category: str = "other",
    commit: bool = True,
) -> ReturnView:
    if resolution not in ("refund", "exchange"):
        raise ValidationError("未知退货处理方式")
    reason_text = reason.strip()
    if not reason_text:
        raise ValidationError("请填写退货原因")
    if len(reason_text) > 1000:
        raise ValidationError("退货原因不能超过 1000 个字符")
    category_value = normalize_reason_category(reason_category)
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
            raise ConflictError("只有已签收订单可以申请退货")
        if order.payment_status not in ("paid", "partially_refunded"):
            raise ConflictError("当前支付状态不能申请退货")
        policy = get_return_policy(session, customer.merchant_id)
        eligibility = _order_eligibility(
            session,
            order,
            resolution,
            policy,
        )
        active = session.execute(
            select(ReturnRequest.id).where(
                ReturnRequest.order_id == order.id,
                ReturnRequest.status.in_(ACTIVE_STATUSES),
            )
        ).scalar_one_or_none()
        if active is not None:
            raise ConflictError("该订单已有进行中的退货申请")
        if resolution == "exchange" and not policy.allow_exchange:
            raise ConflictError("商家未开放换货，仅支持退货退款")
        eligibility_by_sku = {
            item.sku: item for item in eligibility.items
        }
        order_items = session.execute(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.id)
        ).scalars().all()
        by_sku = {item.sku: item for item in order_items}
        if items:
            selected: list[tuple[OrderItem, int, str | None]] = []
            seen: set[str] = set()
            for raw_sku, qty, raw_exchange in items:
                sku_value = raw_sku.strip().upper()
                if sku_value in seen:
                    raise ValidationError("退货商品重复")
                order_item = by_sku.get(sku_value)
                if order_item is None:
                    raise ValidationError("退货商品不属于该订单")
                effective_qty = order_item.qty - order_item.cancelled_qty
                if effective_qty <= 0:
                    raise ValidationError("该商品已取消，不能申请退货")
                if qty < 1 or qty > effective_qty:
                    raise ValidationError("退货数量超出订单数量")
                exchange_value = (raw_exchange or "").strip().upper() or None
                selected.append((order_item, qty, exchange_value))
                seen.add(sku_value)
        else:
            selected = [
                (item, item.qty - item.cancelled_qty, None)
                for item in order_items
                if item.qty - item.cancelled_qty > 0
            ]
        if not selected:
            raise ConflictError("订单没有可退商品")
        if any(order_item.sku not in eligibility_by_sku for order_item, _qty, _exchange in selected):
            raise ValidationError("该商品已取消，不能申请退货")
        blocked = list(
            dict.fromkeys(
                eligibility_by_sku[order_item.sku].reason
                for order_item, _qty, _exchange in selected
                if not eligibility_by_sku[order_item.sku].eligible
            )
        )
        if blocked:
            raise ConflictError("；".join(blocked))
        is_full_return = len(selected) == len(
            [item for item in order_items if item.qty - item.cancelled_qty > 0]
        ) and all(
            order_item.qty - order_item.cancelled_qty == qty
            for order_item, qty, _exchange in selected
        )
        exchange_sku_value = (exchange_sku or "").strip().upper() or None
        if resolution != "exchange" and (
            exchange_sku_value
            or any(exchange for _item, _qty, exchange in selected)
        ):
            raise ValidationError("只有换货可以指定换货商品")
        targets: dict[int, tuple[str | None, int]] = {
            order_item.id: (None, order_item.unit_price_cents)
            for order_item, _qty, _exchange in selected
        }
        if resolution == "exchange":
            if exchange_sku_value and len(selected) != 1:
                raise ConflictError(
                    "多商品换货请按明细分别指定换货商品"
                )
            for order_item, _qty, item_exchange in selected:
                if (
                    exchange_sku_value
                    and item_exchange
                    and exchange_sku_value != item_exchange
                ):
                    raise ValidationError("换货商品与明细不一致")
                target_sku = exchange_sku_value or item_exchange
                if target_sku and target_sku != order_item.sku:
                    product = session.execute(
                        select(Product).where(
                            Product.merchant_id == customer.merchant_id,
                            Product.sku == target_sku,
                            Product.status == "active",
                        )
                    ).scalar_one_or_none()
                    if product is None:
                        raise NotFoundError("换货商品不存在或已下架")
                    targets[order_item.id] = (
                        target_sku,
                        product.price_cents,
                    )
                else:
                    targets[order_item.id] = (
                        None,
                        order_item.unit_price_cents,
                    )
        single_exchange_sku: str | None = None
        single_exchange_price: int | None = None
        if resolution == "exchange" and len(selected) == 1:
            target_sku, target_price = targets[selected[0][0].id]
            if target_sku:
                single_exchange_sku = target_sku
                single_exchange_price = target_price
        attachment_values = validate_attachment_ids(
            session, customer, attachment_ids or []
        )
        request = ReturnRequest(
            merchant_id=customer.merchant_id,
            order_id=order.id,
            customer_id=customer.id,
            status="requested",
            reason=reason_text,
            reason_category=category_value,
            resolution=resolution,
            exchange_sku=single_exchange_sku,
            exchange_unit_price_cents=single_exchange_price,
            attachment_ids=attachment_values or None,
            items=[
                ReturnItem(
                    order_item_id=order_item.id,
                    sku=order_item.sku,
                    title_snapshot=order_item.title_snapshot,
                    qty=qty,
                    exchange_sku=targets[order_item.id][0],
                    exchange_unit_price_cents=(
                        targets[order_item.id][1]
                        if targets[order_item.id][0]
                        else None
                    ),
                )
                for order_item, qty, _exchange in selected
            ],
        )
        session.add(request)
        session.flush()
        append_event(
            session,
            customer.merchant_id,
            "return_requested",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "reason": reason_text,
                "reason_category": category_value,
                "partial": not is_full_return,
                "items": [
                    {
                        "sku": order_item.sku,
                        "qty": qty,
                        "exchange_sku": targets[order_item.id][0],
                    }
                    for order_item, qty, _exchange in selected
                ],
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_requested",
            title=f"新的退货申请 #{request.id}",
            body=f"订单 {order.order_no} · {reason_text}",
            link="/admin",
        )
        if commit:
            session.commit()
        else:
            session.flush()
        return _view(request, order, customer)
    except Exception:
        if commit:
            session.rollback()
        raise


def withdraw_return(
    session: Session, customer: Customer, return_id: int
) -> ReturnView:
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.status != "requested":
            raise ConflictError("只有待审核退货可以撤回")
        request.status = "withdrawn"
        append_event(
            session,
            customer.merchant_id,
            "return_withdrawn",
            {
                "return_id": request.id,
                "order_no": order.order_no,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_withdrawn",
            title=f"顾客撤回退货申请 #{request.id}",
            body=f"订单 {order.order_no} · 顾客已撤回待审核退货",
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer)
    except Exception:
        session.rollback()
        raise


def add_return_attachments(
    session: Session,
    customer: Customer,
    return_id: int,
    attachment_ids: list[int],
) -> ReturnView:
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.status != "requested":
            raise ConflictError("只有待审核退货可以补充凭证")
        existing = list(request.attachment_ids or [])
        merged = list(dict.fromkeys(existing + attachment_ids))
        if len(merged) > 3:
            raise ValidationError("最多上传 3 张图片")
        validated = validate_attachment_ids(session, customer, merged)
        request.attachment_ids = validated or None
        append_event(
            session,
            customer.merchant_id,
            "return_evidence_added",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "attachment_ids": validated,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_evidence_added",
            title=f"退货申请 #{request.id} 补充凭证",
            body=f"订单 {order.order_no} · 已上传 {len(validated)} 张凭证",
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer)
    except Exception:
        session.rollback()
        raise


def create_return_appeal(
    session: Session,
    customer: Customer,
    return_id: int,
    reason: str,
    attachment_ids: list[int],
) -> ReturnView:
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.status != "received":
            raise ConflictError("只有已收货退货可以申诉")
        if request.inspection_deduction_cents <= 0:
            raise ConflictError("该退货没有质检扣减，无需申诉")
        deadline = _appeal_deadline(request)
        if deadline is None or datetime.now(UTC) > deadline:
            raise ConflictError(
                f"已超过收货后 {APPEAL_WINDOW_DAYS} 天申诉期限"
            )
        existing = session.execute(
            select(ReturnDeductionAppeal.id).where(
                ReturnDeductionAppeal.return_request_id == request.id
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("该退货已提交过扣减申诉")
        reason_value = reason.strip()
        if not reason_value:
            raise ValidationError("请填写申诉原因")
        if len(reason_value) > 1000:
            raise ValidationError("申诉原因不能超过 1000 个字符")
        if len(attachment_ids) > 3:
            raise ValidationError("最多上传 3 张图片")
        validated = validate_attachment_ids(
            session, customer, attachment_ids or []
        )
        appeal = ReturnDeductionAppeal(
            merchant_id=customer.merchant_id,
            return_request_id=request.id,
            customer_id=customer.id,
            status="pending",
            reason=reason_value,
            attachment_ids=validated or None,
            requested_cents=request.inspection_deduction_cents,
        )
        session.add(appeal)
        session.flush()
        append_event(
            session,
            customer.merchant_id,
            "return_appeal_created",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "appeal_id": appeal.id,
                "requested_cents": appeal.requested_cents,
                "attachment_ids": validated,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_appeal_created",
            title=f"退货扣减申诉待处理 #{request.id}",
            body=(
                f"订单 {order.order_no} · 申诉扣减 "
                f"{format_cents(appeal.requested_cents)} 元"
            ),
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer, appeal=appeal)
    except Exception:
        session.rollback()
        raise


def withdraw_return_appeal(
    session: Session, customer: Customer, return_id: int
) -> ReturnView:
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        appeal = session.execute(
            select(ReturnDeductionAppeal)
            .where(ReturnDeductionAppeal.return_request_id == request.id)
            .with_for_update()
        ).scalar_one_or_none()
        if appeal is None:
            raise NotFoundError("扣减申诉不存在")
        if appeal.status != "pending":
            raise ConflictError("当前申诉状态不能撤回")
        appeal.status = "withdrawn"
        appeal.resolved_at = datetime.now(UTC)
        append_event(
            session,
            customer.merchant_id,
            "return_appeal_withdrawn",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "appeal_id": appeal.id,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_appeal_withdrawn",
            title=f"顾客撤回扣减申诉 #{request.id}",
            body=f"订单 {order.order_no} · 申诉已撤回",
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer, appeal=appeal)
    except Exception:
        session.rollback()
        raise


def resolve_return_appeal(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    *,
    accept: bool,
    approved_cents: int = 0,
    note: str = "",
) -> ReturnView:
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        appeal = session.execute(
            select(ReturnDeductionAppeal)
            .where(ReturnDeductionAppeal.return_request_id == request.id)
            .with_for_update()
        ).scalar_one_or_none()
        if appeal is None:
            raise NotFoundError("扣减申诉不存在")
        if appeal.status != "pending":
            raise ConflictError("该申诉已处理")
        note_value = note.strip()
        if len(note_value) > 1000:
            raise ValidationError("处理说明不能超过 1000 个字符")
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise NotFoundError("退货顾客不存在")
        resolved_at = datetime.now(UTC)
        if accept:
            if approved_cents <= 0 or approved_cents > appeal.requested_cents:
                raise ValidationError(
                    "核准金额需在 1 到 "
                    f"{format_cents(appeal.requested_cents)} 元之间"
                )
            proposal = create_refund_proposal(
                session,
                customer,
                order.order_no,
                approved_cents,
                f"退货 #{request.id} 质检扣减申诉补偿"
                + (f"：{note_value}" if note_value else ""),
                created_by="staff",
            )
            appeal.status = "accepted"
            appeal.approved_cents = approved_cents
            append_event(
                session,
                merchant_id,
                "return_appeal_accepted",
                {
                    "return_id": request.id,
                    "order_no": order.order_no,
                    "appeal_id": appeal.id,
                    "approved_cents": approved_cents,
                    "refund_proposal_id": proposal.proposal_id,
                    "operator_user_id": user_id,
                },
            )
            notify_customer(
                session,
                merchant_id,
                request.customer_id,
                notification_type="return_appeal_accepted",
                title="质检扣减申诉已支持",
                body=(
                    f"退货 #{request.id} · 核准追加退款 "
                    f"{format_cents(approved_cents)} 元 · 退款提案 "
                    f"#{proposal.proposal_id} 待审批"
                ),
                link="/after-sales",
            )
        else:
            if not note_value:
                raise ValidationError("驳回申诉需填写说明")
            appeal.status = "rejected"
            appeal.approved_cents = None
            append_event(
                session,
                merchant_id,
                "return_appeal_rejected",
                {
                    "return_id": request.id,
                    "order_no": order.order_no,
                    "appeal_id": appeal.id,
                    "operator_user_id": user_id,
                },
            )
            notify_customer(
                session,
                merchant_id,
                request.customer_id,
                notification_type="return_appeal_rejected",
                title="质检扣减申诉未通过",
                body=f"退货 #{request.id} · {note_value}",
                link="/after-sales",
            )
        appeal.resolution_note = note_value
        appeal.resolved_by = user_id
        appeal.resolved_at = resolved_at
        session.commit()
        return _view(request, order, customer, appeal=appeal)
    except Exception:
        session.rollback()
        raise


def schedule_return_pickup(
    session: Session,
    customer: Customer,
    return_id: int,
    scheduled_date: date,
    time_window: str,
) -> ReturnView:
    if time_window not in PICKUP_WINDOWS:
        raise ValidationError("无效的取件时段")
    today = datetime.now(UTC).date()
    if scheduled_date < today:
        raise ValidationError("取件日期不能早于今天")
    if scheduled_date > today + timedelta(days=PICKUP_WINDOW_DAYS):
        raise ValidationError(
            f"取件日期最多可预约 {PICKUP_WINDOW_DAYS} 天内"
        )
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.tracking_no or request.shipment_status != "none":
            raise ConflictError("已登记回寄，不再支持预约上门取件")
        if request.status != "approved":
            raise ConflictError("只有待回寄退货可以预约上门取件")
        active = session.execute(
            select(ReturnPickup).where(
                ReturnPickup.return_request_id == request.id,
                ReturnPickup.status == "scheduled",
            ).with_for_update()
        ).scalar_one_or_none()
        if active is not None:
            raise ConflictError("该退货已有待上门的取件预约")
        now = datetime.now(UTC)
        pickup = ReturnPickup(
            merchant_id=customer.merchant_id,
            return_request_id=request.id,
            customer_id=customer.id,
            pickup_no=f"PU-{now:%Y%m%d}-{uuid4().hex[:6].upper()}",
            status="scheduled",
            scheduled_date=scheduled_date,
            time_window=time_window,
            address_recipient=order.recipient_name,
            address_phone=order.recipient_phone,
            address_region=order.address_region,
            address_city=order.address_city,
            address_detail=order.address_detail,
            courier_name="顺丰速运",
        )
        session.add(pickup)
        session.flush()
        append_event(
            session,
            customer.merchant_id,
            "return_pickup_scheduled",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "pickup_id": pickup.id,
                "pickup_no": pickup.pickup_no,
                "scheduled_date": scheduled_date.isoformat(),
                "time_window": time_window,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_pickup_scheduled",
            title=f"退货上门取件待揽收 #{request.id}",
            body=(
                f"订单 {order.order_no} · {scheduled_date.isoformat()} "
                f"{time_window} · {order.address_city}{order.address_detail}"
            ),
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer, pickup=pickup)
    except Exception:
        session.rollback()
        raise


def cancel_return_pickup(
    session: Session,
    customer: Customer,
    return_id: int,
    reason: str = "",
) -> ReturnView:
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        pickup = session.execute(
            select(ReturnPickup)
            .where(
                ReturnPickup.return_request_id == request.id,
                ReturnPickup.status == "scheduled",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if pickup is None:
            raise NotFoundError("没有待上门的取件预约")
        pickup.status = "cancelled"
        pickup.cancel_reason = reason.strip()[:1000]
        pickup.cancelled_at = datetime.now(UTC)
        append_event(
            session,
            customer.merchant_id,
            "return_pickup_cancelled",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "pickup_id": pickup.id,
                "pickup_no": pickup.pickup_no,
                "cancelled_by": "customer",
                "reason": pickup.cancel_reason,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_pickup_cancelled",
            title=f"顾客取消上门取件 #{request.id}",
            body=f"订单 {order.order_no} · 取件预约 {pickup.pickup_no} 已取消",
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer, pickup=pickup)
    except Exception:
        session.rollback()
        raise


def mark_return_pickup_picked_up(
    session: Session, merchant_id: int, user_id: int, return_id: int
) -> ReturnView:
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        if request.status != "approved":
            raise ConflictError("当前退货状态不能揽收")
        if request.tracking_no or request.shipment_status != "none":
            raise ConflictError("退货已登记回寄，不能重复揽收")
        pickup = session.execute(
            select(ReturnPickup)
            .where(
                ReturnPickup.return_request_id == request.id,
                ReturnPickup.status == "scheduled",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if pickup is None:
            raise ConflictError("没有待上门的取件预约")
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise NotFoundError("退货顾客不存在")
        now = datetime.now(UTC)
        carrier = pickup.courier_name.strip() or "顺丰速运"
        pickup.status = "picked_up"
        pickup.picked_up_at = now
        pickup.courier_name = carrier
        request.carrier = carrier
        request.tracking_no = pickup.pickup_no
        request.status = "in_transit"
        request.shipment_status = "picked_up"
        request.tracking_events.append(
            ReturnTrackingEvent(
                status="picked_up",
                description=(
                    f"上门取件已揽收，承运商 {carrier}，"
                    f"运单 {pickup.pickup_no}"
                ),
                created_by=user_id,
                happened_at=now,
            )
        )
        append_event(
            session,
            merchant_id,
            "return_pickup_picked_up",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "pickup_id": pickup.id,
                "pickup_no": pickup.pickup_no,
                "carrier": carrier,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_pickup_picked_up",
            title="退货包裹已揽收",
            body=(
                f"退货 #{request.id} · {carrier} 运单 {pickup.pickup_no} "
                "已由上门取件揽收"
            ),
            link="/after-sales",
        )
        session.commit()
        return _view(request, order, customer, pickup=pickup)
    except Exception:
        session.rollback()
        raise


def cancel_return_pickup_by_staff(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    note: str,
) -> ReturnView:
    note_value = note.strip()
    if not note_value:
        raise ValidationError("取消取件需填写说明")
    if len(note_value) > 1000:
        raise ValidationError("取消说明不能超过 1000 个字符")
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        pickup = session.execute(
            select(ReturnPickup)
            .where(
                ReturnPickup.return_request_id == request.id,
                ReturnPickup.status == "scheduled",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if pickup is None:
            raise NotFoundError("没有待上门的取件预约")
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise NotFoundError("退货顾客不存在")
        pickup.status = "cancelled"
        pickup.cancel_reason = note_value
        pickup.cancelled_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "return_pickup_cancelled",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "pickup_id": pickup.id,
                "pickup_no": pickup.pickup_no,
                "cancelled_by": "staff",
                "reason": note_value,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_pickup_cancelled",
            title="上门取件预约已取消",
            body=f"退货 #{request.id} · {note_value}",
            link="/after-sales",
        )
        session.commit()
        return _view(request, order, customer, pickup=pickup)
    except Exception:
        session.rollback()
        raise


def pay_exchange_supplement(
    session: Session, customer: Customer, return_id: int
) -> ReturnView:
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.resolution != "exchange":
            raise ConflictError("该退货不是换货申请")
        if request.status != "received":
            raise ConflictError("换货尚未确认收货")
        if (
            request.exchange_supplement_cents <= 0
            or request.exchange_payment_status != "pending"
        ):
            raise ConflictError("当前没有待支付的换货补差价")
        now = datetime.now(UTC)
        request.exchange_payment_status = "paid"
        request.exchange_payment_ref = f"EXS-{uuid4().hex[:12].upper()}"
        request.exchange_paid_at = now
        record_after_sales_charge(
            session,
            merchant_id=customer.merchant_id,
            customer_id=request.customer_id,
            order_id=order.id,
            kind="exchange_supplement",
            amount_cents=request.exchange_supplement_cents,
            reference=request.exchange_payment_ref,
            return_request_id=request.id,
        )
        replacement_no = None
        if request.replacement_order_id is not None:
            replacement = session.get(Order, request.replacement_order_id)
            replacement_no = replacement.order_no if replacement else None
        append_event(
            session,
            customer.merchant_id,
            "exchange_supplement_paid",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "replacement_order_no": replacement_no,
                "amount_cents": request.exchange_supplement_cents,
                "payment_ref": request.exchange_payment_ref,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="exchange_supplement_paid",
            title=f"换货补差价已支付 #{request.id}",
            body=(
                f"订单 {order.order_no} · "
                f"{format_cents(request.exchange_supplement_cents)} 元 · "
                f"{request.exchange_payment_ref} · 可以发货"
            ),
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer, replacement_no)
    except Exception:
        session.rollback()
        raise


def extend_exchange_supplement_deadline(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    hours: int = 24,
) -> ReturnView:
    if hours < 1 or hours > 72:
        raise ValidationError("延期时长需在 1 到 72 小时之间")
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == merchant_id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.resolution != "exchange":
            raise ConflictError("该退货不是换货申请")
        if request.status != "received":
            raise ConflictError("换货已结束，不能延期")
        if (
            request.exchange_supplement_cents <= 0
            or request.exchange_payment_status != "pending"
        ):
            raise ConflictError("当前没有待支付的换货补差价")
        current_deadline = _exchange_payment_deadline(request)
        now = datetime.now(UTC)
        if current_deadline is None:
            raise ConflictError("该换货没有自动取消期限")
        if current_deadline.tzinfo is None:
            current_deadline = current_deadline.replace(tzinfo=UTC)
        if current_deadline <= now:
            raise ConflictError("补差支付已超期，不能延期")
        new_deadline = current_deadline + timedelta(hours=hours)
        request.exchange_supplement_deadline = new_deadline
        replacement_no = None
        if request.replacement_order_id is not None:
            replacement = session.get(Order, request.replacement_order_id)
            replacement_no = replacement.order_no if replacement else None
        append_event(
            session,
            merchant_id,
            "exchange_supplement_extended",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "replacement_order_no": replacement_no,
                "hours": hours,
                "old_deadline": current_deadline.isoformat(),
                "new_deadline": new_deadline.isoformat(),
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_exchange_supplement_extended",
            title="补差支付期限已延长",
            body=(
                f"订单 {order.order_no} · 补差 "
                f"{format_cents(request.exchange_supplement_cents)} 元 · "
                f"支付期限已延长至 {new_deadline:%Y-%m-%d %H:%M}"
            ),
            link="/after-sales",
        )
        session.commit()
        customer = session.get(Customer, request.customer_id)
        return _view(request, order, customer, replacement_no)
    except Exception:
        session.rollback()
        raise


def cancel_exchange_supplement(
    session: Session,
    customer: Customer,
    return_id: int,
    reason: str = "",
) -> ReturnView:
    return _cancel_exchange_supplement(
        session,
        return_id,
        merchant_id=customer.merchant_id,
        customer_id=customer.id,
        actor="customer",
        operator_user_id=None,
        reason=reason,
    )


def cancel_exchange_supplement_by_staff(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    reason: str = "",
) -> ReturnView:
    return _cancel_exchange_supplement(
        session,
        return_id,
        merchant_id=merchant_id,
        customer_id=None,
        actor="staff",
        operator_user_id=user_id,
        reason=reason,
    )


def _cancel_exchange_supplement(
    session: Session,
    return_id: int,
    *,
    merchant_id: int,
    customer_id: int | None,
    actor: str,
    operator_user_id: int | None,
    reason: str,
) -> ReturnView:
    try:
        stmt = (
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == merchant_id,
            )
            .with_for_update()
        )
        if customer_id is not None:
            stmt = stmt.where(ReturnRequest.customer_id == customer_id)
        row = session.execute(stmt).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.resolution != "exchange":
            raise ConflictError("该退货不是换货申请")
        if request.status != "received":
            raise ConflictError("换货已结束，不能取消")
        if (
            request.exchange_supplement_cents <= 0
            or request.exchange_payment_status != "pending"
        ):
            raise ConflictError("当前没有待支付的换货补差价")
        if request.replacement_order_id is None:
            raise ConflictError("换货订单不存在，不能取消")
        replacement = session.execute(
            select(Order)
            .where(
                Order.id == request.replacement_order_id,
                Order.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if replacement is None:
            raise ConflictError("换货订单不存在，不能取消")
        if replacement.status == "cancelled":
            raise ConflictError("换货订单已取消")
        if replacement.fulfillment_status != "unfulfilled":
            raise ConflictError("换货商品已进入履约，不能取消")
        shipment = session.execute(
            select(Shipment.id)
            .where(Shipment.order_id == replacement.id)
            .limit(1)
        ).scalar_one_or_none()
        if shipment is not None:
            raise ConflictError("换货商品已创建运单，不能取消")
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise ConflictError("顾客不存在，不能取消换货")

        reason_text = reason.strip()[:500] or (
            "顾客放弃补差换货"
            if actor == "customer"
            else "补差超时未支付，系统自动取消换货"
            if actor == "system"
            else "商家取消补差换货"
        )
        now = datetime.now(UTC)
        released: list[dict[str, object]] = []
        reservations = session.execute(
            select(InventoryReservation)
            .where(
                InventoryReservation.order_id == replacement.id,
                InventoryReservation.status == "committed",
            )
            .order_by(InventoryReservation.product_id)
            .with_for_update()
        ).scalars().all()
        for reservation in reservations:
            inventory = session.execute(
                select(Inventory)
                .where(
                    Inventory.product_id == reservation.product_id,
                    Inventory.warehouse_id == reservation.warehouse_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if inventory is None:
                raise ConflictError("换货库存行不存在，不能取消")
            record_movement(
                session,
                inventory,
                reservation.qty,
                "exchange_cancelled_restock",
                order_id=replacement.id,
                return_id=request.id,
                operator_user_id=operator_user_id,
                note="取消补差换货，释放替换商品库存",
            )
            reservation.status = "released"
            released.append(
                {
                    "product_id": reservation.product_id,
                    "warehouse_id": reservation.warehouse_id,
                    "qty": reservation.qty,
                }
            )
        replacement.status = "cancelled"
        replacement.payment_status = "refunded"

        fee_cents = get_return_policy(
            session, merchant_id
        ).return_shipping_fee_cents
        base_refund, _scope = _return_refund_amount(
            session, order, request, fee_cents
        )
        refund_amount = base_refund
        proposal = None
        if refund_amount > 0:
            proposal = create_refund_proposal(
                session,
                customer,
                order.order_no,
                refund_amount,
                f"放弃补差换货退款：{reason_text}",
                created_by="system",
            )
            request.refund_proposal_id = proposal.proposal_id
        request.refund_amount_cents = refund_amount
        request.status = "exchange_cancelled"
        request.exchange_payment_status = "cancelled"
        request.exchange_cancelled_at = now
        request.exchange_cancel_reason = reason_text
        request.exchange_cancelled_by = actor
        append_event(
            session,
            merchant_id,
            "exchange_cancelled",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "replacement_order_no": replacement.order_no,
                "supplement_cents": request.exchange_supplement_cents,
                "refund_proposal_id": (
                    proposal.proposal_id if proposal else None
                ),
                "refund_amount_cents": refund_amount,
                "reason": reason_text,
                "cancelled_by": actor,
                "operator_user_id": operator_user_id,
                "released_items": released,
            },
        )
        if actor != "staff":
            notify_staff(
                session,
                merchant_id,
                notification_type="exchange_cancelled",
                title=(
                    f"补差超时换货已自动取消 #{request.id}"
                    if actor == "system"
                    else f"顾客放弃补差换货 #{request.id}"
                ),
                body=(
                    f"订单 {order.order_no} · 换货订单 "
                    f"{replacement.order_no} 已取消 · "
                    + (
                        f"退款提案 #{proposal.proposal_id} 待审批 "
                        f"{format_cents(refund_amount)} 元"
                        if proposal
                        else "无需退款"
                    )
                ),
                link="/admin",
            )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_exchange_cancelled",
            title=(
                "换货已取消，退款提案待审批"
                if proposal
                else "换货已取消"
            ),
            body=(
                f"退货 #{request.id} · 原订单 {order.order_no} · "
                f"换货订单 {replacement.order_no} 已取消"
                + (
                    f" · 退款 {format_cents(refund_amount)} 元提案 "
                    f"#{proposal.proposal_id} 等待商家审批"
                    if proposal
                    else " · 无需退款"
                )
            ),
            link="/after-sales",
        )
        session.commit()
        return _view(request, order, customer, replacement.order_no)
    except Exception:
        session.rollback()
        raise


def auto_cancel_overdue_exchange_supplements(
    session: Session,
    *,
    now: datetime | None = None,
    hours: int = 72,
    limit: int = 50,
) -> int:
    if hours <= 0:
        return 0
    current = now or datetime.now(UTC)
    rows = session.execute(
        select(ReturnRequest.id, ReturnRequest.merchant_id)
        .where(
            ReturnRequest.status == "received",
            ReturnRequest.resolution == "exchange",
            ReturnRequest.exchange_payment_status == "pending",
            ReturnRequest.exchange_supplement_cents > 0,
            ReturnRequest.exchange_supplement_deadline.is_not(None),
            ReturnRequest.exchange_supplement_deadline <= current,
        )
        .order_by(ReturnRequest.id)
        .limit(limit)
    ).all()
    cancelled = 0
    for return_id, merchant_id in rows:
        try:
            _cancel_exchange_supplement(
                session,
                return_id,
                merchant_id=merchant_id,
                customer_id=None,
                actor="system",
                operator_user_id=None,
                reason="补差支付期限已过，系统自动取消换货",
            )
            cancelled += 1
        except (ConflictError, NotFoundError):
            pass
    return cancelled


def list_returns_for_customer(
    session: Session, customer: Customer
) -> list[ReturnView]:
    rows = session.execute(
        select(ReturnRequest, Order)
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(
            ReturnRequest.merchant_id == customer.merchant_id,
            ReturnRequest.customer_id == customer.id,
        )
        .order_by(ReturnRequest.created_at.desc(), ReturnRequest.id.desc())
        .limit(50)
    ).all()
    replacement_ids = [
        request.replacement_order_id
        for request, _order in rows
        if request.replacement_order_id is not None
    ]
    replacement_nos = {
        order.id: order.order_no
        for order in session.execute(
            select(Order).where(Order.id.in_(replacement_ids))
        ).scalars().all()
    } if replacement_ids else {}
    replacement_shipments = _replacement_shipments_map(session, replacement_ids)
    appeals = _appeals_map(session, [request.id for request, _order in rows])
    pickups = _pickups_map(session, [request.id for request, _order in rows])
    return [
        _view(
            request,
            order,
            customer,
            replacement_nos.get(request.replacement_order_id),
            replacement_shipments.get(request.replacement_order_id, ()),
            appeals.get(request.id),
            pickups.get(request.id),
        )
        for request, order in rows
    ]


def list_returns_for_admin(
    session: Session,
    merchant_id: int,
    *,
    status: str = "active",
    limit: int = 100,
) -> list[ReturnView]:
    stmt = (
        select(ReturnRequest, Order, Customer)
        .join(Order, Order.id == ReturnRequest.order_id)
        .outerjoin(Customer, Customer.id == ReturnRequest.customer_id)
        .where(ReturnRequest.merchant_id == merchant_id)
    )
    if status == "active":
        stmt = stmt.where(ReturnRequest.status.in_(ACTIVE_STATUSES))
    elif status == "appeal":
        stmt = stmt.join(
            ReturnDeductionAppeal,
            ReturnDeductionAppeal.return_request_id == ReturnRequest.id,
        ).where(ReturnDeductionAppeal.status == "pending")
    elif status == "pickup":
        stmt = stmt.join(
            ReturnPickup,
            ReturnPickup.return_request_id == ReturnRequest.id,
        ).where(ReturnPickup.status == "scheduled")
    elif status != "all":
        stmt = stmt.where(ReturnRequest.status == status)
    rows = session.execute(
        stmt.order_by(ReturnRequest.updated_at.desc(), ReturnRequest.id.desc())
        .limit(limit)
    ).all()
    replacement_ids = [
        request.replacement_order_id
        for request, _order, _customer in rows
        if request.replacement_order_id is not None
    ]
    replacement_nos = {
        order.id: order.order_no
        for order in session.execute(
            select(Order).where(Order.id.in_(replacement_ids))
        ).scalars().all()
    } if replacement_ids else {}
    replacement_shipments = _replacement_shipments_map(session, replacement_ids)
    appeals = _appeals_map(
        session, [request.id for request, _order, _customer in rows]
    )
    pickups = _pickups_map(
        session, [request.id for request, _order, _customer in rows]
    )
    risk_map = customer_after_sales_risk_map(
        session,
        merchant_id,
        [request.customer_id for request, _order, _customer in rows],
    )
    views = []
    for request, order, customer in rows:
        risk = risk_map.get(request.customer_id)
        views.append(
            _view(
                request,
                order,
                customer,
                replacement_nos.get(request.replacement_order_id),
                replacement_shipments.get(
                    request.replacement_order_id, ()
                ),
                appeals.get(request.id),
                pickups.get(request.id),
                customer_risk_level=risk.level if risk else None,
                customer_risk_reasons=risk.reasons if risk else (),
            )
        )
    return views


def count_returns(session: Session, merchant_id: int) -> dict[str, int]:
    counts = {
        "requested": 0,
        "approved": 0,
        "in_transit": 0,
        "received": 0,
        "rejected": 0,
        "exchange_cancelled": 0,
    }
    rows = session.execute(
        select(ReturnRequest.status, func.count(ReturnRequest.id))
        .where(ReturnRequest.merchant_id == merchant_id)
        .group_by(ReturnRequest.status)
    ).all()
    for status, count in rows:
        if status in counts:
            counts[status] = int(count)
    counts["appeals"] = int(
        session.execute(
            select(func.count(ReturnDeductionAppeal.id)).where(
                ReturnDeductionAppeal.merchant_id == merchant_id,
                ReturnDeductionAppeal.status == "pending",
            )
        ).scalar_one()
    )
    counts["pickups"] = int(
        session.execute(
            select(func.count(ReturnPickup.id)).where(
                ReturnPickup.merchant_id == merchant_id,
                ReturnPickup.status == "scheduled",
            )
        ).scalar_one()
    )
    return counts


def add_return_inspection_attachment(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    *,
    original_name: str,
    data: bytes,
) -> ReturnView:
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        if request.status not in ("approved", "in_transit", "received"):
            raise ConflictError("当前退货状态不能上传质检照片")
        existing = list(request.inspection_attachment_ids or [])
        if len(existing) >= 3:
            raise ValidationError("质检照片最多 3 张")
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise NotFoundError("退货顾客不存在")
        attachment = save_staff_attachment(
            session,
            merchant_id=merchant_id,
            customer_id=request.customer_id,
            user_id=user_id,
            original_name=original_name,
            data=data,
        )
        existing.append(attachment.id)
        request.inspection_attachment_ids = existing
        append_event(
            session,
            merchant_id,
            "return_inspection_attachment_added",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "attachment_id": attachment.id,
                "attachment_count": len(existing),
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(request, order, customer)
    except Exception:
        session.rollback()
        raise


def add_return_tracking(
    session: Session,
    customer: Customer,
    return_id: int,
    carrier: str,
    tracking_no: str,
) -> ReturnView:
    carrier_value = carrier.strip()
    tracking_value = tracking_no.strip()
    if not carrier_value or not tracking_value:
        raise ValidationError("请填写承运商和退货运单号")
    try:
        row = session.execute(
            select(ReturnRequest, Order)
            .join(Order, Order.id == ReturnRequest.order_id)
            .where(
                ReturnRequest.id == return_id,
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("退货申请不存在")
        request, order = row
        if request.status not in ("approved", "in_transit"):
            raise ConflictError("退货申请尚未批准或已收货")
        if request.tracking_no and request.shipment_status != "none":
            raise ConflictError("回寄单号已登记，不能修改")
        active_pickup = session.execute(
            select(ReturnPickup.id).where(
                ReturnPickup.return_request_id == request.id,
                ReturnPickup.status == "scheduled",
            )
        ).scalar_one_or_none()
        if active_pickup is not None:
            raise ConflictError("已有上门取件预约，请先取消再登记回寄")
        duplicate = session.execute(
            select(ReturnRequest.id).where(
                ReturnRequest.merchant_id == customer.merchant_id,
                ReturnRequest.tracking_no == tracking_value,
                ReturnRequest.id != request.id,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ConflictError("退货单号已存在")
        request.carrier = carrier_value[:64]
        request.tracking_no = tracking_value[:64]
        request.status = "in_transit"
        if request.shipment_status == "none":
            request.shipment_status = "registered"
            request.tracking_events.append(
                ReturnTrackingEvent(
                    status="registered",
                    description=RETURN_TRACKING_DESCRIPTIONS["registered"],
                    happened_at=datetime.now(UTC),
                )
            )
        append_event(
            session,
            customer.merchant_id,
            "return_tracking_added",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "carrier": request.carrier,
                "tracking_no": request.tracking_no,
                "shipment_status": request.shipment_status,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="return_tracking_added",
            title=f"退货已寄回 {request.tracking_no}",
            body=f"订单 {order.order_no} · 等待收货验收",
            link="/admin",
        )
        session.commit()
        return _view(request, order, customer)
    except Exception:
        session.rollback()
        raise


def _return_for_update(
    session: Session, merchant_id: int, return_id: int
) -> tuple[ReturnRequest, Order]:
    row = session.execute(
        select(ReturnRequest, Order)
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(
            ReturnRequest.id == return_id,
            ReturnRequest.merchant_id == merchant_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("退货申请不存在")
    return row


def report_return_tracking_exception(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    description: str,
) -> ReturnView:
    description_value = description.strip()
    if not description_value:
        raise ValidationError("请填写物流异常说明")
    if len(description_value) > 500:
        raise ValidationError("物流异常说明不能超过 500 个字符")
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        if request.status not in ("approved", "in_transit"):
            raise ConflictError("当前退货状态不能登记物流异常")
        if request.shipment_status == "none":
            raise ConflictError("退货还没有回寄单号")
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise NotFoundError("退货顾客不存在")
        request.tracking_events.append(
            ReturnTrackingEvent(
                status="exception",
                description=description_value,
                created_by=user_id,
                happened_at=datetime.now(UTC),
            )
        )
        append_event(
            session,
            merchant_id,
            "return_tracking_exception",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "carrier": request.carrier,
                "tracking_no": request.tracking_no,
                "shipment_status": request.shipment_status,
                "description": description_value,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_tracking_exception",
            title="退货物流异常",
            body=(
                f"退货 #{request.id} · {request.carrier} "
                f"{request.tracking_no} · {description_value}"
            ),
            link="/after-sales",
        )
        session.commit()
        return _view(request, order, customer)
    except Exception:
        session.rollback()
        raise


def advance_return_tracking(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    status: str,
    description: str = "",
) -> ReturnView:
    if status not in RETURN_TRACKING_LABELS or status == "registered":
        raise ValidationError("未知退货物流状态")
    description_value = description.strip()
    if len(description_value) > 500:
        raise ValidationError("物流说明不能超过 500 个字符")
    if not description_value:
        description_value = RETURN_TRACKING_DESCRIPTIONS[status]
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        if request.status not in ("approved", "in_transit"):
            raise ConflictError("当前退货状态不能更新物流")
        current = request.shipment_status
        if current == "none":
            raise ConflictError("退货还没有回寄单号")
        index = RETURN_TRACKING_FLOW.index(current)
        if index + 1 >= len(RETURN_TRACKING_FLOW):
            raise ConflictError("退货物流已到达退货仓")
        expected = RETURN_TRACKING_FLOW[index + 1]
        if status != expected:
            raise ConflictError(
                f"退货物流不能从{RETURN_TRACKING_LABELS[current]}变更为"
                f"{RETURN_TRACKING_LABELS[status]}"
            )
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise NotFoundError("退货顾客不存在")
        request.shipment_status = status
        request.tracking_events.append(
            ReturnTrackingEvent(
                status=status,
                description=description_value,
                created_by=user_id,
                happened_at=datetime.now(UTC),
            )
        )
        append_event(
            session,
            merchant_id,
            "return_tracking_advanced",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "carrier": request.carrier,
                "tracking_no": request.tracking_no,
                "from_status": current,
                "status": status,
                "description": description_value,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_tracking_updated",
            title="退货物流更新",
            body=(
                f"退货 #{request.id} · {RETURN_TRACKING_LABELS[status]} · "
                f"{request.carrier} {request.tracking_no}"
            ),
            link="/after-sales",
        )
        session.commit()
        return _view(request, order, customer)
    except Exception:
        session.rollback()
        raise


def approve_return(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
) -> ReturnView:
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        if request.status != "requested":
            raise ConflictError("只有待审核退货可以批准")
        request.status = "approved"
        request.return_address = settings.return_address
        request.approved_by = user_id
        request.approved_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "return_approved",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "return_address": request.return_address,
            },
        )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_approved",
            title="退货申请已通过",
            body=f"订单 {order.order_no} · 请寄回：{request.return_address}",
            link="/after-sales",
        )
        session.commit()
        customer = session.get(Customer, request.customer_id)
        replacement = (
            session.get(Order, request.replacement_order_id)
            if request.replacement_order_id
            else None
        )
        return _view(
            request,
            order,
            customer,
            replacement.order_no if replacement else None,
        )
    except Exception:
        session.rollback()
        raise


def reject_return(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    reason: str,
) -> ReturnView:
    reason_value = reason.strip() or "人工驳回"
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        if request.status != "requested":
            raise ConflictError("只有待审核退货可以驳回")
        request.status = "rejected"
        request.rejected_reason = reason_value[:1000]
        request.approved_by = user_id
        request.approved_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "return_rejected",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "reason": reason_value,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            request.customer_id,
            notification_type="return_rejected",
            title="退货申请未通过",
            body=f"订单 {order.order_no} · 原因：{reason_value}",
            link="/after-sales",
        )
        session.commit()
        customer = session.get(Customer, request.customer_id)
        replacement = (
            session.get(Order, request.replacement_order_id)
            if request.replacement_order_id
            else None
        )
        return _view(
            request,
            order,
            customer,
            replacement.order_no if replacement else None,
        )
    except Exception:
        session.rollback()
        raise


def _create_replacement_order(
    session: Session,
    merchant_id: int,
    original: Order,
    request: ReturnRequest,
) -> tuple[Order, int]:
    items = session.execute(
        select(OrderItem)
        .where(OrderItem.order_id == original.id)
        .order_by(OrderItem.id)
    ).scalars().all()
    by_id = {item.id: item for item in items}
    if not items or not request.items:
        raise ConflictError("原订单没有可换货商品")
    replacement_qty: dict[int, tuple[str, str, int]] = {}
    balance_cents = 0
    for return_item in request.items:
        original_item = by_id.get(return_item.order_item_id)
        if original_item is None:
            raise ConflictError("退货明细对应的订单商品不存在")
        legacy_sku = None
        legacy_price = None
        if (
            len(request.items) == 1
            and not return_item.exchange_sku
            and request.exchange_sku
        ):
            legacy_sku = request.exchange_sku
            legacy_price = request.exchange_unit_price_cents
        target_sku = (
            return_item.exchange_sku or legacy_sku or return_item.sku
        )
        if target_sku == return_item.sku:
            product_id = original_item.product_id
            title = original_item.title_snapshot
            target_price = original_item.unit_price_cents
        else:
            target = session.execute(
                select(Product).where(
                    Product.merchant_id == merchant_id,
                    Product.sku == target_sku,
                    Product.status == "active",
                )
            ).scalar_one_or_none()
            if target is None:
                raise ConflictError("换货商品已下架或不存在")
            product_id = target.id
            title = target.title
            target_price = (
                return_item.exchange_unit_price_cents
                if return_item.exchange_unit_price_cents is not None
                else legacy_price
                if legacy_price is not None
                else target.price_cents
            )
        balance_cents += (
            target_price - original_item.unit_price_cents
        ) * return_item.qty
        existing = replacement_qty.get(product_id)
        replacement_qty[product_id] = (
            target_sku,
            title,
            (existing[2] if existing else 0) + return_item.qty,
        )
    replacement_items = [
        (product_id, sku, title, qty)
        for product_id, (sku, title, qty) in replacement_qty.items()
    ]
    now = datetime.now(UTC)
    replacement = Order(
        order_no=f"ORD-{now:%Y%m%d}-{uuid4().hex[:8].upper()}",
        merchant_id=merchant_id,
        customer_id=request.customer_id,
        recipient_name=original.recipient_name,
        recipient_phone=original.recipient_phone,
        address_region=original.address_region,
        address_city=original.address_city,
        address_detail=original.address_detail,
        status="paid",
        payment_status="paid",
        fulfillment_status="unfulfilled",
        item_total_cents=0,
        freight_cents=0,
        discount_cents=0,
        payable_cents=0,
        currency=original.currency,
        placed_at=now,
    )
    session.add(replacement)
    session.flush()
    for product_id, sku, title, qty in replacement_items:
        inventory = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == product_id,
                Inventory.qty_available >= qty,
            )
            .order_by(Inventory.qty_available.desc(), Inventory.warehouse_id)
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if inventory is None:
            raise ConflictError(f"换货库存不足：{sku}")
        record_movement(
            session,
            inventory,
            -qty,
            "exchange_allocated",
            order_id=replacement.id,
            return_id=request.id,
            note=f"换货占用 {sku} × {qty}",
        )
        session.add(
            InventoryReservation(
                merchant_id=merchant_id,
                order_id=replacement.id,
                product_id=product_id,
                warehouse_id=inventory.warehouse_id,
                qty=qty,
                status="committed",
            )
        )
        session.add(
            OrderItem(
                order_id=replacement.id,
                product_id=product_id,
                sku=sku,
                title_snapshot=title,
                qty=qty,
                unit_price_cents=0,
            )
        )
    append_event(
        session,
        merchant_id,
        "exchange_order_created",
        {
            "return_id": request.id,
            "original_order_no": original.order_no,
            "replacement_order_no": replacement.order_no,
            "difference_cents": max(0, -balance_cents),
            "supplement_cents": max(0, balance_cents),
            "items": [
                {"sku": sku, "qty": qty}
                for _product_id, sku, _title, qty in replacement_items
            ],
        },
    )
    notify_staff(
        session,
        merchant_id,
        notification_type="exchange_order_created",
        title=f"换货订单待发货 {replacement.order_no}",
        body=f"原订单 {original.order_no} · 退货 #{request.id}",
        link="/admin",
    )
    return (
        replacement,
        max(0, -balance_cents),
        max(0, balance_cents),
    )


def receive_return(
    session: Session,
    merchant_id: int,
    user_id: int,
    return_id: int,
    *,
    restock: bool = True,
    dispositions: dict[int, str] | None = None,
    deduction_cents: int = 0,
    inspection_note: str = "",
    item_deductions: dict[int, int] | None = None,
    responsibilities: dict[int, str] | None = None,
) -> ReceiveReturnResult:
    try:
        request, order = _return_for_update(session, merchant_id, return_id)
        if request.status not in ("approved", "in_transit"):
            raise ConflictError("当前退货状态不能确认收货")
        if request.refund_proposal_id is not None or request.replacement_order_id is not None:
            raise ConflictError("退货已生成退款或换货结果")
        active_pickup = session.execute(
            select(ReturnPickup.id).where(
                ReturnPickup.return_request_id == request.id,
                ReturnPickup.status == "scheduled",
            )
        ).scalar_one_or_none()
        if active_pickup is not None:
            raise ConflictError(
                "退货有上门取件预约，请先揽收或取消后再确认收货"
            )
        if deduction_cents < 0:
            raise ValidationError("质检扣减金额不能为负数")
        note_value = inspection_note.strip()
        if len(note_value) > 1000:
            raise ValidationError("质检说明不能超过 1000 个字符")
        if request.resolution == "exchange":
            if deduction_cents > 0 or note_value:
                raise ValidationError("换货不支持质检扣减")
        elif deduction_cents > 0 and not note_value:
            raise ValidationError("质检扣减需填写原因")
        customer = session.get(Customer, request.customer_id)
        if customer is None:
            raise NotFoundError("退货顾客不存在")
        provided = dispositions or {}
        valid_item_ids = {item.id for item in request.items}
        if set(provided) - valid_item_ids:
            raise ValidationError("退货明细不存在")
        if any(value not in ("restock", "damaged") for value in provided.values()):
            raise ValidationError("无效的质检处置")
        item_level_deductions = dict(item_deductions or {})
        if set(item_level_deductions) - valid_item_ids:
            raise ValidationError("退货明细不存在")
        if any(value < 0 for value in item_level_deductions.values()):
            raise ValidationError("逐件质检扣减金额不能为负数")
        has_item_deductions = any(
            value > 0 for value in item_level_deductions.values()
        )
        if has_item_deductions and deduction_cents > 0:
            raise ValidationError("不能同时按整单和逐件扣减")
        if has_item_deductions:
            if request.resolution == "exchange":
                raise ValidationError("换货不支持质检扣减")
            if not note_value:
                raise ValidationError("质检扣减需填写原因")
            deduction_cents = sum(item_level_deductions.values())
        provided_responsibilities = dict(responsibilities or {})
        if set(provided_responsibilities) - valid_item_ids:
            raise ValidationError("退货明细不存在")
        allowed_responsibilities = (
            "merchant",
            "customer",
            "logistics",
            "other",
        )
        if any(
            value not in allowed_responsibilities
            for value in provided_responsibilities.values()
        ):
            raise ValidationError("无效的责任判定")
        if any(
            provided_responsibilities.get(item.id) == "merchant"
            and item_level_deductions.get(item.id, 0) > 0
            for item in request.items
        ):
            raise ValidationError("商家责任不支持质检扣减")
        default_disposition = "restock" if restock else "damaged"
        inspected_at = datetime.now(UTC)
        for item in request.items:
            item.disposition = provided.get(item.id, default_disposition)
            item.inspection_deduction_cents = item_level_deductions.get(
                item.id, 0
            )
            item.responsibility = provided_responsibilities.get(item.id)
            item.inspected_by = user_id
            item.inspected_at = inspected_at
        damaged = tuple(
            DamagedLine(
                sku=item.sku,
                title=item.title_snapshot,
                qty=item.qty,
            )
            for item in request.items
            if item.disposition == "damaged"
        )
        should_restock = (
            any(item.disposition == "restock" for item in request.items)
            if request.items
            else restock
        )
        restocked = (
            _restock_returned_items(session, merchant_id, order, request)
            if should_restock
            else ()
        )
        if request.resolution == "exchange":
            replacement, refund_due, supplement_due = (
                _create_replacement_order(
                    session, merchant_id, order, request
                )
            )
            request.replacement_order_id = replacement.id
            exchange_targets = list(
                dict.fromkeys(
                    item.exchange_sku or item.sku for item in request.items
                )
            )
            if refund_due > 0:
                proposal = create_refund_proposal(
                    session,
                    customer,
                    order.order_no,
                    refund_due,
                    f"换货差价：{', '.join(exchange_targets)}",
                    created_by="system",
                )
                request.refund_proposal_id = proposal.proposal_id
                request.refund_amount_cents = refund_due
            if supplement_due > 0:
                request.exchange_supplement_cents = supplement_due
                request.exchange_payment_status = "pending"
                request.exchange_payment_ref = ""
                request.exchange_paid_at = None
                timeout_hours = settings.after_sales_supplement_timeout_hours
                request.exchange_supplement_deadline = (
                    datetime.now(UTC) + timedelta(hours=timeout_hours)
                    if timeout_hours > 0
                    else None
                )
            else:
                request.exchange_supplement_cents = 0
                request.exchange_payment_status = "none"
            request.status = "received"
            request.received_at = datetime.now(UTC)
            append_event(
                session,
                merchant_id,
                "return_received",
                {
                    "return_id": request.id,
                    "order_no": order.order_no,
                    "replacement_order_no": replacement.order_no,
                    "exchange_sku": request.exchange_sku,
                    "exchange_items": [
                        {
                            "sku": item.sku,
                            "exchange_sku": item.exchange_sku,
                            "qty": item.qty,
                        }
                        for item in request.items
                    ],
                    "difference_cents": refund_due,
                    "supplement_cents": supplement_due,
                    "operator_user_id": user_id,
                    "restock": restock,
                    "restocked_items": [
                        {
                            "sku": line.sku,
                            "warehouse_id": line.warehouse_id,
                            "qty": line.qty,
                        }
                        for line in restocked
                    ],
                    "damaged_items": [
                        {"sku": line.sku, "qty": line.qty}
                        for line in damaged
                    ],
                },
            )
            if supplement_due > 0:
                title = "退货已签收，换货待支付补差价"
                body = (
                    f"原订单 {order.order_no} · 换货订单 "
                    f"{replacement.order_no} 已创建 · 需补差价 "
                    f"{format_cents(supplement_due)} 元，支付后发货"
                )
            elif refund_due > 0:
                title = "退货已签收，换货与差价退款待审批"
                body = (
                    f"原订单 {order.order_no} · 换货订单 "
                    f"{replacement.order_no} 已创建 · 差价 "
                    f"{format_cents(refund_due)} 元退款提案 "
                    f"#{request.refund_proposal_id} 等待审批"
                )
            else:
                title = "退货已签收，换货订单已创建"
                body = (
                    f"原订单 {order.order_no} · 换货订单 "
                    f"{replacement.order_no} 已创建，等待发货"
                )
            notify_customer(
                session,
                merchant_id,
                request.customer_id,
                notification_type="return_received",
                title=title,
                body=body,
                link="/orders",
            )
            session.commit()
            return ReceiveReturnResult(
                view=_view(request, order, customer, replacement.order_no),
                restocked=restocked,
                damaged=damaged,
            )
        fee_cents = get_return_policy(
            session, merchant_id
        ).return_shipping_fee_cents
        base_refund, refund_scope = _return_refund_amount(
            session, order, request, fee_cents
        )
        if deduction_cents > base_refund:
            raise ConflictError(
                f"质检扣减不能超过可退金额 {format_cents(base_refund)} 元"
            )
        refund_amount = base_refund - deduction_cents
        request.inspection_deduction_cents = deduction_cents
        request.inspection_note = note_value
        proposal = None
        if refund_amount > 0:
            deduction_text = (
                f"；质检扣减 {format_cents(deduction_cents)} 元：{note_value}"
                if deduction_cents > 0
                else (f"；质检说明：{note_value}" if note_value else "")
            )
            proposal = create_refund_proposal(
                session,
                customer,
                order.order_no,
                refund_amount,
                f"退货验收：{request.reason}{deduction_text}",
            )
            request.refund_proposal_id = proposal.proposal_id
        request.refund_amount_cents = refund_amount
        request.status = "received"
        request.received_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "return_received",
            {
                "return_id": request.id,
                "order_no": order.order_no,
                "refund_proposal_id": (
                    proposal.proposal_id if proposal else None
                ),
                "base_refund_cents": base_refund,
                "inspection_deduction_cents": deduction_cents,
                "item_deductions": [
                    {
                        "return_item_id": item.id,
                        "sku": item.sku,
                        "qty": item.qty,
                        "deduction_cents": item.inspection_deduction_cents,
                    }
                    for item in request.items
                    if item.inspection_deduction_cents > 0
                ],
                "inspection_note": note_value,
                "responsibilities": [
                    {
                        "return_item_id": item.id,
                        "sku": item.sku,
                        "responsibility": item.responsibility,
                    }
                    for item in request.items
                    if item.responsibility is not None
                ],
                "refund_amount_cents": refund_amount,
                "return_shipping_fee_cents": fee_cents,
                "refund_scope": refund_scope,
                "operator_user_id": user_id,
                "restock": restock,
                "restocked_items": [
                    {
                        "sku": line.sku,
                        "warehouse_id": line.warehouse_id,
                        "qty": line.qty,
                    }
                    for line in restocked
                ],
                "damaged_items": [
                    {"sku": line.sku, "qty": line.qty}
                    for line in damaged
                ],
            },
        )
        if proposal is not None:
            fee_note = (
                f"，已扣退货运费 {format_cents(fee_cents)} 元"
                if fee_cents > 0
                else ""
            )
            deduction_note = (
                f"，质检扣减 {format_cents(deduction_cents)} 元"
                if deduction_cents > 0
                else ""
            )
            notify_customer(
                session,
                merchant_id,
                request.customer_id,
                notification_type="return_received",
                title=(
                    "退货已签收，部分退款待审批"
                    if refund_scope == "partial"
                    else "退货已签收，退款待审批"
                ),
                body=(
                    f"订单 {order.order_no} · 退款提案 "
                    f"#{proposal.proposal_id} 已提交"
                    f"（{format_cents(refund_amount)} 元{fee_note}{deduction_note}），"
                    "等待店主审批"
                ),
                link="/after-sales",
            )
        else:
            notify_customer(
                session,
                merchant_id,
                request.customer_id,
                notification_type="return_received",
                title=(
                    "退货已签收，质检扣减后无需退款"
                    if deduction_cents > 0
                    else (
                        "退货已签收，扣除退货运费后无需退款"
                        if fee_cents > 0
                        else "退货已签收，无需退款"
                    )
                ),
                body=(
                    f"订单 {order.order_no} · "
                    + (
                        f"质检扣减 {format_cents(deduction_cents)} 元后无需退款"
                        if deduction_cents > 0
                        else (
                            f"扣除退货运费 {format_cents(fee_cents)} 元后无需退款"
                            if fee_cents > 0
                            else "该订单已无可退金额"
                        )
                    )
                ),
                link="/after-sales",
            )
        session.commit()
        return ReceiveReturnResult(
            view=_view(request, order, customer),
            restocked=restocked,
            damaged=damaged,
        )
    except Exception:
        session.rollback()
        raise
