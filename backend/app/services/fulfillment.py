from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import (
    Order,
    OrderItem,
    ReturnRequest,
    Shipment,
    ShipmentEvent,
    ShipmentItem,
)
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.notifications import notify_customer

STATUS_LABELS = {
    "pending": "待揽收",
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "delivered": "已签收",
    "exception": "异常件",
}

DEFAULT_NODES = {
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "delivered": "已签收",
    "exception": "物流异常",
}

DEFAULT_DESCRIPTIONS = {
    "in_transit": "快件已由承运商揽收，开始运输",
    "out_for_delivery": "快件已到达派送网点，正在派送",
    "delivered": "快件已签收",
    "exception": "快件运输异常，商家处理中",
}

ALLOWED_TRANSITIONS = {
    "pending": {"in_transit", "exception"},
    "in_transit": {"out_for_delivery", "exception"},
    "out_for_delivery": {"delivered", "exception"},
    "exception": {"in_transit", "out_for_delivery", "delivered"},
    "delivered": set(),
}


@dataclass(frozen=True)
class ShipmentEventView:
    happened_at: datetime
    node: str
    description: str


@dataclass(frozen=True)
class ShipmentItemView:
    order_item_id: int
    sku: str
    title: str
    qty: int


@dataclass(frozen=True)
class ShipmentView:
    id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    items: tuple[ShipmentItemView, ...]
    events: tuple[ShipmentEventView, ...]


@dataclass(frozen=True)
class FulfillmentItemView:
    order_item_id: int
    sku: str
    title: str
    qty: int
    shipped_qty: int
    cancelled_qty: int
    remaining_qty: int


@dataclass(frozen=True)
class FulfillmentOrderView:
    order_no: str
    status: str
    payment_status: str
    fulfillment_status: str
    payable_cents: int
    placed_at: datetime
    recipient_name: str
    recipient_phone: str
    address_region: str
    address_city: str
    address_detail: str
    item_count: int
    items: tuple[FulfillmentItemView, ...]
    shipments: tuple[ShipmentView, ...]


@dataclass(frozen=True)
class ShipmentActionResult:
    order_no: str
    shipment_id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    item_count: int
    order_status: str
    fulfillment_status: str


def _clean(value: str, field: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field}不能为空")
    if len(cleaned) > max_length:
        raise ValidationError(f"{field}不能超过 {max_length} 个字符")
    return cleaned


def _order_for_update(session: Session, merchant_id: int, order_no: str) -> Order:
    order = session.execute(
        select(Order)
        .where(Order.merchant_id == merchant_id, Order.order_no == order_no)
        .with_for_update()
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")
    return order


def _replacement_return(
    session: Session, order: Order
) -> ReturnRequest | None:
    return session.execute(
        select(ReturnRequest).where(
            ReturnRequest.replacement_order_id == order.id
        )
    ).scalar_one_or_none()


def shipments_for_order(
    session: Session, order_id: int, *, for_update: bool = False
) -> list[Shipment]:
    stmt = (
        select(Shipment)
        .where(Shipment.order_id == order_id)
        .order_by(Shipment.package_no, Shipment.id)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return list(session.execute(stmt).scalars().all())


def allocated_quantities(
    session: Session,
    items: list[OrderItem],
    shipments: list[Shipment],
) -> dict[int, int]:
    if not shipments:
        return {}
    shipment_ids = [shipment.id for shipment in shipments]
    allocated_shipment_ids = set(
        session.execute(
            select(ShipmentItem.shipment_id)
            .where(ShipmentItem.shipment_id.in_(shipment_ids))
            .distinct()
        ).scalars().all()
    )
    # A shipment created before per-item allocation existed covers the whole order.
    if any(shipment.id not in allocated_shipment_ids for shipment in shipments):
        return {item.id: item.qty for item in items}
    rows = session.execute(
        select(ShipmentItem.order_item_id, func.sum(ShipmentItem.qty))
        .where(ShipmentItem.shipment_id.in_(shipment_ids))
        .group_by(ShipmentItem.order_item_id)
    ).all()
    return {int(order_item_id): int(qty or 0) for order_item_id, qty in rows}


def cancelable_quantities(
    session: Session,
    items: list[OrderItem],
    shipments: list[Shipment],
) -> dict[int, int]:
    allocated = allocated_quantities(session, items, shipments)
    return {
        item.id: max(item.qty - item.cancelled_qty - allocated.get(item.id, 0), 0)
        for item in items
    }


def refresh_order_status(
    session: Session,
    order: Order,
    items: list[OrderItem],
    shipments: list[Shipment],
) -> None:
    if not shipments:
        return
    allocated = allocated_quantities(session, items, shipments)
    fully_accounted = all(
        allocated.get(item.id, 0) + item.cancelled_qty >= item.qty
        for item in items
    )
    all_delivered = all(shipment.status == "delivered" for shipment in shipments)
    any_shipped = any(
        shipment.shipped_at is not None
        or shipment.status in ("in_transit", "out_for_delivery", "delivered")
        for shipment in shipments
    )
    if fully_accounted and all_delivered:
        order.fulfillment_status = "delivered"
        if order.status != "completed":
            order.status = "delivered"
        return
    if any_shipped:
        order.fulfillment_status = "shipped"
        if order.status in ("paid", "fulfilling", "partially_refunded"):
            order.status = "shipped"
        return
    order.fulfillment_status = "allocated"
    if order.status in ("paid", "partially_refunded"):
        order.status = "fulfilling"


def _requested_items(
    session: Session,
    items: list[OrderItem],
    shipments: list[Shipment],
    requested: list[tuple[int, int]] | None,
) -> list[tuple[OrderItem, int]]:
    allocated = allocated_quantities(session, items, shipments)
    item_by_id = {item.id: item for item in items}
    remaining = {
        item.id: max(
            item.qty - item.cancelled_qty - allocated.get(item.id, 0), 0
        )
        for item in items
    }
    if requested is None:
        requested = [
            (item.id, remaining[item.id])
            for item in items
            if remaining[item.id] > 0
        ]
        if not requested:
            raise ConflictError("订单已全部发货")
    if not requested:
        raise ValidationError("请选择要发货的商品")
    merged: dict[int, int] = {}
    for order_item_id, qty in requested:
        item = item_by_id.get(order_item_id)
        if item is None:
            raise ValidationError("发货商品不属于该订单")
        if qty <= 0:
            raise ValidationError("发货数量必须大于 0")
        merged[order_item_id] = merged.get(order_item_id, 0) + qty
    result: list[tuple[OrderItem, int]] = []
    for order_item_id, qty in merged.items():
        item = item_by_id[order_item_id]
        if qty > remaining[order_item_id]:
            raise ConflictError(
                f"{item.sku} 最多还能发 {remaining[order_item_id]} 件"
            )
        result.append((item, qty))
    return result


def create_shipment(
    session: Session,
    merchant_id: int,
    user_id: int,
    order_no: str,
    carrier: str,
    tracking_no: str,
    items: list[tuple[int, int]] | None = None,
) -> ShipmentActionResult:
    carrier_value = _clean(carrier, "承运商", 64)
    tracking_value = _clean(tracking_no, "运单号", 64)
    try:
        order = _order_for_update(session, merchant_id, order_no)
        if order.payment_status not in ("paid", "partially_refunded"):
            raise ConflictError("只有已支付订单可以创建运单")
        if order.status not in ("paid", "fulfilling", "shipped", "partially_refunded"):
            raise ConflictError("当前订单状态不能创建运单")
        if order.cancel_requested_at is not None:
            raise ConflictError("订单有取消申请待审批，暂不能发货")
        replacement_return = session.execute(
            select(ReturnRequest).where(
                ReturnRequest.replacement_order_id == order.id
            )
        ).scalar_one_or_none()
        if (
            replacement_return is not None
            and replacement_return.exchange_payment_status == "pending"
        ):
            raise ConflictError("换货补差价待顾客支付，暂不能发货")
        duplicate = session.execute(
            select(Shipment.id).where(
                Shipment.merchant_id == merchant_id,
                Shipment.tracking_no == tracking_value,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ConflictError("运单号已存在")

        order_items = list(
            session.execute(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItem.id)
            ).scalars().all()
        )
        shipments = shipments_for_order(session, order.id, for_update=True)
        package_items = _requested_items(
            session, order_items, shipments, items
        )

        now = datetime.now(UTC)
        package_no = (
            max((shipment.package_no for shipment in shipments), default=0) + 1
        )
        shipment = Shipment(
            order_id=order.id,
            merchant_id=merchant_id,
            package_no=package_no,
            carrier=carrier_value,
            tracking_no=tracking_value,
            status="pending",
            current_node=STATUS_LABELS["pending"],
        )
        session.add(shipment)
        session.flush()
        for item, qty in package_items:
            session.add(
                ShipmentItem(
                    shipment_id=shipment.id,
                    order_item_id=item.id,
                    qty=qty,
                )
            )
        session.add(
            ShipmentEvent(
                shipment_id=shipment.id,
                happened_at=now,
                node=STATUS_LABELS["pending"],
                description=(
                    f"已创建 {carrier_value} 运单，包裹 #{package_no} 等待揽收"
                ),
            )
        )
        shipments.append(shipment)
        refresh_order_status(session, order, order_items, shipments)
        append_event(
            session,
            merchant_id,
            "shipment_created",
            {
                "order_no": order.order_no,
                "shipment_id": shipment.id,
                "package_no": package_no,
                "carrier": carrier_value,
                "tracking_no": tracking_value,
                "items": [
                    {
                        "order_item_id": item.id,
                        "sku": item.sku,
                        "qty": qty,
                    }
                    for item, qty in package_items
                ],
                "status": shipment.status,
                "operator_user_id": user_id,
            },
        )
        replacement_return = _replacement_return(session, order)
        notify_customer(
            session,
            merchant_id,
            order.customer_id,
            notification_type="shipment_created",
            title=(
                "换货补发包裹已创建"
                if replacement_return is not None
                else "订单已创建包裹"
            ),
            body=(
                f"{'换货订单' if replacement_return is not None else '订单'} "
                f"{order.order_no} · 包裹 #{package_no} · "
                f"{carrier_value} {tracking_value} · "
                f"{sum(qty for _, qty in package_items)} 件商品"
            ),
            link="/after-sales" if replacement_return is not None else "/orders",
        )
        session.commit()
        return ShipmentActionResult(
            order_no=order.order_no,
            shipment_id=shipment.id,
            package_no=shipment.package_no,
            carrier=shipment.carrier,
            tracking_no=shipment.tracking_no,
            status=shipment.status,
            current_node=shipment.current_node,
            item_count=sum(qty for _, qty in package_items),
            order_status=order.status,
            fulfillment_status=order.fulfillment_status,
        )
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("运单号已存在") from exc
    except Exception:
        session.rollback()
        raise


def advance_shipment(
    session: Session,
    merchant_id: int,
    user_id: int,
    order_no: str,
    target_status: str,
    node: str = "",
    description: str = "",
    shipment_id: int | None = None,
) -> ShipmentActionResult:
    if target_status not in STATUS_LABELS:
        raise ValidationError("未知物流状态")
    node_value = (
        _clean(node, "物流节点", 64)
        if node.strip()
        else DEFAULT_NODES[target_status]
    )
    description_value = (
        _clean(description, "物流说明", 500)
        if description.strip()
        else DEFAULT_DESCRIPTIONS[target_status]
    )
    try:
        order = _order_for_update(session, merchant_id, order_no)
        shipments = shipments_for_order(session, order.id, for_update=True)
        if not shipments:
            raise NotFoundError("订单还没有运单")
        if shipment_id is None:
            if len(shipments) > 1:
                raise ValidationError("订单有多个包裹，请指定要更新的包裹")
            shipment = shipments[0]
        else:
            shipment = next(
                (item for item in shipments if item.id == shipment_id), None
            )
            if shipment is None:
                raise NotFoundError("包裹不存在")
        previous_status = shipment.status
        if target_status not in ALLOWED_TRANSITIONS[previous_status]:
            raise ConflictError(
                f"包裹不能从{STATUS_LABELS[previous_status]}变更为"
                f"{STATUS_LABELS[target_status]}"
            )

        now = datetime.now(UTC)
        shipment.status = target_status
        shipment.current_node = node_value
        session.add(
            ShipmentEvent(
                shipment_id=shipment.id,
                happened_at=now,
                node=node_value,
                description=description_value,
            )
        )
        if target_status in ("in_transit", "out_for_delivery"):
            if shipment.shipped_at is None:
                shipment.shipped_at = now
        elif target_status == "delivered":
            shipment.delivered_at = now
            if shipment.shipped_at is None:
                shipment.shipped_at = now

        order_items = list(
            session.execute(
                select(OrderItem).where(OrderItem.order_id == order.id)
            ).scalars().all()
        )
        refresh_order_status(session, order, order_items, shipments)
        append_event(
            session,
            merchant_id,
            "shipment_status_changed",
            {
                "order_no": order.order_no,
                "shipment_id": shipment.id,
                "package_no": shipment.package_no,
                "carrier": shipment.carrier,
                "tracking_no": shipment.tracking_no,
                "from_status": previous_status,
                "status": target_status,
                "node": node_value,
                "operator_user_id": user_id,
            },
        )
        replacement_return = _replacement_return(session, order)
        is_replacement = replacement_return is not None
        order_label = "换货订单" if is_replacement else "订单"
        all_delivered = all(item.status == "delivered" for item in shipments)
        if target_status == "delivered" and not all_delivered:
            title = "换货补发包裹已签收" if is_replacement else "包裹已签收"
            body = (
                f"{order_label} {order.order_no} · 包裹 #{shipment.package_no} "
                "已签收，其余包裹仍在配送中"
            )
        else:
            title = {
                "in_transit": "换货补发运输中" if is_replacement else "订单运输中",
                "out_for_delivery": (
                    "换货补发派送中" if is_replacement else "订单派送中"
                ),
                "delivered": "换货补发已签收" if is_replacement else "订单已签收",
                "exception": "换货补发物流异常" if is_replacement else "物流异常",
            }[target_status]
            body = (
                f"{order_label} {order.order_no} · 包裹 #{shipment.package_no} · "
                f"{shipment.carrier} {shipment.tracking_no} · {node_value}"
            )
        notify_customer(
            session,
            merchant_id,
            order.customer_id,
            notification_type="shipment_updated",
            title=title,
            body=body,
            link="/after-sales" if is_replacement else "/orders",
        )
        session.commit()
        package_item_count = session.execute(
            select(func.coalesce(func.sum(ShipmentItem.qty), 0)).where(
                ShipmentItem.shipment_id == shipment.id
            )
        ).scalar_one()
        return ShipmentActionResult(
            order_no=order.order_no,
            shipment_id=shipment.id,
            package_no=shipment.package_no,
            carrier=shipment.carrier,
            tracking_no=shipment.tracking_no,
            status=shipment.status,
            current_node=shipment.current_node,
            item_count=int(package_item_count),
            order_status=order.status,
            fulfillment_status=order.fulfillment_status,
        )
    except Exception:
        session.rollback()
        raise


def list_fulfillment_orders(
    session: Session,
    merchant_id: int,
    *,
    open_only: bool = True,
    q: str | None = None,
    limit: int = 50,
) -> list[FulfillmentOrderView]:
    stmt = select(Order).where(Order.merchant_id == merchant_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Order.order_no.ilike(pattern),
                Order.recipient_name.ilike(pattern),
                Order.recipient_phone.ilike(pattern),
            )
        )
    if open_only:
        stmt = stmt.where(
            Order.status.in_(("paid", "fulfilling", "shipped", "partially_refunded")),
            Order.fulfillment_status != "delivered",
        )
    else:
        stmt = stmt.where(Order.status != "pending_payment")
    orders = session.execute(
        stmt.order_by(Order.placed_at.desc(), Order.id.desc()).limit(limit)
    ).scalars().all()
    if not orders:
        return []

    order_ids = [order.id for order in orders]
    items = session.execute(
        select(OrderItem)
        .where(OrderItem.order_id.in_(order_ids))
        .order_by(OrderItem.order_id, OrderItem.id)
    ).scalars().all()
    items_by_order: dict[int, list[OrderItem]] = {}
    for item in items:
        items_by_order.setdefault(item.order_id, []).append(item)

    shipments = session.execute(
        select(Shipment)
        .where(Shipment.order_id.in_(order_ids))
        .order_by(Shipment.order_id, Shipment.package_no, Shipment.id)
    ).scalars().all()
    shipments_by_order: dict[int, list[Shipment]] = {}
    for shipment in shipments:
        shipments_by_order.setdefault(shipment.order_id, []).append(shipment)

    shipment_item_rows = []
    if shipments:
        shipment_item_rows = session.execute(
            select(ShipmentItem, OrderItem)
            .join(OrderItem, OrderItem.id == ShipmentItem.order_item_id)
            .where(ShipmentItem.shipment_id.in_([s.id for s in shipments]))
            .order_by(ShipmentItem.shipment_id, ShipmentItem.id)
        ).all()
    package_items_by_shipment: dict[int, list[ShipmentItemView]] = {}
    allocated_by_order_item: dict[int, int] = {}
    for shipment_item, order_item in shipment_item_rows:
        package_items_by_shipment.setdefault(
            shipment_item.shipment_id, []
        ).append(
            ShipmentItemView(
                order_item_id=order_item.id,
                sku=order_item.sku,
                title=order_item.title_snapshot,
                qty=shipment_item.qty,
            )
        )
        allocated_by_order_item[order_item.id] = (
            allocated_by_order_item.get(order_item.id, 0) + shipment_item.qty
        )
    legacy_orders = {
        shipment.order_id
        for shipment in shipments
        if shipment.id not in package_items_by_shipment
    }

    events_by_shipment: dict[int, list[ShipmentEventView]] = {}
    if shipments:
        events = session.execute(
            select(ShipmentEvent)
            .where(ShipmentEvent.shipment_id.in_([s.id for s in shipments]))
            .order_by(ShipmentEvent.happened_at, ShipmentEvent.id)
        ).scalars().all()
        for event in events:
            events_by_shipment.setdefault(event.shipment_id, []).append(
                ShipmentEventView(
                    happened_at=event.happened_at,
                    node=event.node,
                    description=event.description,
                )
            )

    views: list[FulfillmentOrderView] = []
    for order in orders:
        order_items = items_by_order.get(order.id, [])
        if order.id in legacy_orders:
            remaining_by_item = {item.id: 0 for item in order_items}
        else:
            remaining_by_item = {
                item.id: max(
                    item.qty
                    - item.cancelled_qty
                    - allocated_by_order_item.get(item.id, 0),
                    0,
                )
                for item in order_items
            }
        shipment_views = tuple(
            ShipmentView(
                id=shipment.id,
                package_no=shipment.package_no,
                carrier=shipment.carrier,
                tracking_no=shipment.tracking_no,
                status=shipment.status,
                current_node=shipment.current_node,
                shipped_at=shipment.shipped_at,
                delivered_at=shipment.delivered_at,
                items=tuple(package_items_by_shipment.get(shipment.id, [])),
                events=tuple(events_by_shipment.get(shipment.id, [])),
            )
            for shipment in shipments_by_order.get(order.id, [])
        )
        views.append(
            FulfillmentOrderView(
                order_no=order.order_no,
                status=order.status,
                payment_status=order.payment_status,
                fulfillment_status=order.fulfillment_status,
                payable_cents=order.payable_cents,
                placed_at=order.placed_at,
                recipient_name=order.recipient_name,
                recipient_phone=order.recipient_phone,
                address_region=order.address_region,
                address_city=order.address_city,
                address_detail=order.address_detail,
                item_count=len(order_items),
                items=tuple(
                    FulfillmentItemView(
                        order_item_id=item.id,
                        sku=item.sku,
                        title=item.title_snapshot,
                        qty=item.qty,
                        shipped_qty=(
                            item.qty
                            if order.id in legacy_orders
                            else allocated_by_order_item.get(item.id, 0)
                        ),
                        cancelled_qty=item.cancelled_qty,
                        remaining_qty=remaining_by_item[item.id],
                    )
                    for item in order_items
                ),
                shipments=shipment_views,
            )
        )
    return views
