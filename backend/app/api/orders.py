from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import (
    ActionProposal,
    Order,
    OrderItem,
    Payment,
    Refund,
    Shipment,
    ShipmentEvent,
    ShipmentItem,
)
from app.domain.money import format_cents
from app.services.errors import NotFoundError
from app.services.fulfillment import allocated_quantities
from app.services.orders import cancel_unpaid_order, confirm_receipt
from app.services.proposals import request_order_cancellation

router = APIRouter(prefix="/api/store", tags=["orders"])


class OrderItemOut(BaseModel):
    order_item_id: int
    sku: str
    title: str
    qty: int
    cancelled_qty: int
    shipped_qty: int
    cancelable_qty: int
    unit_price_cents: int
    unit_price_display: str
    line_total_cents: int
    line_total_display: str


class PaymentOut(BaseModel):
    id: int
    provider: str
    status: str
    amount_cents: int
    amount_display: str


class ShipmentEventOut(BaseModel):
    happened_at: datetime
    node: str
    description: str


class ShipmentItemOut(BaseModel):
    sku: str
    title: str
    qty: int


class ShipmentOut(BaseModel):
    id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    items: list[ShipmentItemOut]
    events: list[ShipmentEventOut]


class RefundOut(BaseModel):
    id: int
    kind: str
    amount_cents: int
    amount_display: str
    status: str
    reason: str
    provider_refund_id: str
    settlement_status: str
    settlement_reference: str
    settlement_failure_reason: str
    settled_at: datetime | None
    created_at: datetime
    executed_at: datetime | None


class AddressOut(BaseModel):
    recipient_name: str
    recipient_phone: str
    region: str
    city: str
    detail: str


class OrderSummaryOut(BaseModel):
    id: int
    order_no: str
    status: str
    payment_status: str
    fulfillment_status: str
    payable_cents: int
    payable_display: str
    discount_cents: int
    discount_display: str
    freight_cents: int
    freight_display: str
    currency: str
    placed_at: datetime
    item_count: int
    cancel_requested_at: datetime | None


class OrderDetailOut(OrderSummaryOut):
    items: list[OrderItemOut]
    payment: PaymentOut | None
    refunds: list[RefundOut]
    refunded_total_cents: int
    refunded_total_display: str
    net_paid_cents: int
    net_paid_display: str
    shipments: list[ShipmentOut]
    address: AddressOut


class CancelOut(BaseModel):
    order_no: str
    status: str


class CancelItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    qty: int = Field(ge=1)


class CancelRequestIn(BaseModel):
    items: list[CancelItemIn] | None = None


class CancelRequestOut(BaseModel):
    order_no: str
    proposal_id: int
    amount_cents: int
    amount_display: str
    status: str
    scope: str
    items: list[CancelItemIn]


class ConfirmReceiptOut(BaseModel):
    order_no: str
    status: str


def _summary(order: Order, item_count: int) -> OrderSummaryOut:
    return OrderSummaryOut(
        id=order.id,
        order_no=order.order_no,
        status=order.status,
        payment_status=order.payment_status,
        fulfillment_status=order.fulfillment_status,
        payable_cents=order.payable_cents,
        payable_display=format_cents(order.payable_cents),
        discount_cents=order.discount_cents,
        discount_display=format_cents(order.discount_cents),
        freight_cents=order.freight_cents,
        freight_display=format_cents(order.freight_cents),
        currency=order.currency,
        placed_at=order.placed_at,
        item_count=item_count,
        cancel_requested_at=order.cancel_requested_at,
    )


@router.get("/orders", response_model=list[OrderSummaryOut])
def list_orders(
    customer: CurrentCustomer,
    session: SessionDep,
    status: str | None = None,
    q: str | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[OrderSummaryOut]:
    limit = max(1, min(limit, 100))
    stmt = (
        select(Order, func.count(OrderItem.id))
        .outerjoin(OrderItem, OrderItem.order_id == Order.id)
        .where(
            Order.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
        .group_by(Order.id)
        .order_by(Order.placed_at.desc(), Order.id.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Order.status == status)
    if q and q.strip():
        stmt = stmt.where(Order.order_no.ilike(f"%{q.strip()}%"))
    if before_id is not None:
        stmt = stmt.where(Order.id < before_id)
    rows = session.execute(stmt).all()
    return [_summary(order, int(count)) for order, count in rows]


@router.post("/orders/{order_no}/cancel", response_model=CancelOut)
def cancel_order(order_no: str, customer: CurrentCustomer, session: SessionDep) -> CancelOut:
    result = cancel_unpaid_order(session, customer, order_no)
    return CancelOut(order_no=result.order_no, status=result.status)


@router.post(
    "/orders/{order_no}/cancel-request", response_model=CancelRequestOut
)
def request_cancel_order(
    order_no: str,
    customer: CurrentCustomer,
    session: SessionDep,
    payload: CancelRequestIn | None = None,
) -> CancelRequestOut:
    result = request_order_cancellation(
        session,
        customer,
        order_no,
        items=(
            [(item.sku, item.qty) for item in payload.items]
            if payload is not None and payload.items
            else None
        ),
    )
    return CancelRequestOut(
        order_no=result.order_no,
        proposal_id=result.proposal_id,
        amount_cents=result.amount_cents,
        amount_display=format_cents(result.amount_cents),
        status=result.status,
        scope=result.scope,
        items=[CancelItemIn(sku=item["sku"], qty=item["qty"]) for item in result.items],
    )


@router.post(
    "/orders/{order_no}/confirm-receipt", response_model=ConfirmReceiptOut
)
def confirm_order_receipt(
    order_no: str, customer: CurrentCustomer, session: SessionDep
) -> ConfirmReceiptOut:
    result = confirm_receipt(session, customer, order_no)
    return ConfirmReceiptOut(order_no=result.order_no, status=result.status)


@router.get("/orders/{order_no}", response_model=OrderDetailOut)
def get_order(order_no: str, customer: CurrentCustomer, session: SessionDep) -> OrderDetailOut:
    order = session.execute(
        select(Order).where(
            Order.order_no == order_no,
            Order.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")

    items = session.execute(
        select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
    ).scalars().all()
    payment = session.execute(
        select(Payment).where(Payment.order_id == order.id).order_by(Payment.id.desc()).limit(1)
    ).scalar_one_or_none()
    refunds = session.execute(
        select(Refund)
        .where(Refund.order_id == order.id)
        .order_by(Refund.id)
    ).scalars().all()
    proposal_ids = [
        refund.proposal_id for refund in refunds if refund.proposal_id
    ]
    proposal_reasons = (
        {
            proposal.id: str((proposal.payload_json or {}).get("reason") or "")
            for proposal in session.execute(
                select(ActionProposal).where(
                    ActionProposal.id.in_(proposal_ids)
                )
            ).scalars().all()
        }
        if proposal_ids
        else {}
    )
    refunded_total = sum(
        refund.amount_cents
        for refund in refunds
        if refund.kind == "refund" and refund.status == "executed"
    )
    net_paid = max(order.payable_cents - refunded_total, 0)
    shipments = session.execute(
        select(Shipment)
        .where(Shipment.order_id == order.id)
        .order_by(Shipment.package_no, Shipment.id)
    ).scalars().all()
    shipment_ids = [shipment.id for shipment in shipments]
    shipment_events_by_id: dict[int, list[ShipmentEvent]] = {}
    shipment_items_by_id: dict[int, list[tuple[ShipmentItem, OrderItem]]] = {}
    if shipment_ids:
        events = session.execute(
            select(ShipmentEvent)
            .where(ShipmentEvent.shipment_id.in_(shipment_ids))
            .order_by(ShipmentEvent.happened_at, ShipmentEvent.id)
        ).scalars().all()
        for event in events:
            shipment_events_by_id.setdefault(event.shipment_id, []).append(event)
        rows = session.execute(
            select(ShipmentItem, OrderItem)
            .join(OrderItem, OrderItem.id == ShipmentItem.order_item_id)
            .where(ShipmentItem.shipment_id.in_(shipment_ids))
            .order_by(ShipmentItem.shipment_id, ShipmentItem.id)
        ).all()
        for shipment_item, order_item in rows:
            shipment_items_by_id.setdefault(
                shipment_item.shipment_id, []
            ).append((shipment_item, order_item))

    allocated = allocated_quantities(session, list(items), list(shipments))
    summary = _summary(order, len(items))
    return OrderDetailOut(
        **summary.model_dump(),
        items=[
            OrderItemOut(
                order_item_id=item.id,
                sku=item.sku,
                title=item.title_snapshot,
                qty=item.qty,
                cancelled_qty=item.cancelled_qty,
                shipped_qty=allocated.get(item.id, 0),
                cancelable_qty=max(
                    item.qty - item.cancelled_qty - allocated.get(item.id, 0),
                    0,
                ),
                unit_price_cents=item.unit_price_cents,
                unit_price_display=format_cents(item.unit_price_cents),
                line_total_cents=item.qty * item.unit_price_cents,
                line_total_display=format_cents(item.qty * item.unit_price_cents),
            )
            for item in items
        ],
        payment=(
            PaymentOut(
                id=payment.id,
                provider=payment.provider,
                status=payment.status,
                amount_cents=payment.amount_cents,
                amount_display=format_cents(payment.amount_cents),
            )
            if payment
            else None
        ),
        refunds=[
            RefundOut(
                id=refund.id,
                kind=refund.kind,
                amount_cents=refund.amount_cents,
                amount_display=format_cents(refund.amount_cents),
                status=refund.status,
                reason=(
                    proposal_reasons.get(refund.proposal_id, "")
                    if refund.proposal_id
                    else ""
                ),
                provider_refund_id=refund.provider_refund_id,
                settlement_status=refund.settlement_status,
                settlement_reference=refund.settlement_reference,
                settlement_failure_reason=refund.settlement_failure_reason,
                settled_at=refund.settled_at,
                created_at=refund.created_at,
                executed_at=refund.executed_at,
            )
            for refund in refunds
        ],
        refunded_total_cents=refunded_total,
        refunded_total_display=format_cents(refunded_total),
        net_paid_cents=net_paid,
        net_paid_display=format_cents(net_paid),
        shipments=[
            ShipmentOut(
                id=shipment.id,
                package_no=shipment.package_no,
                carrier=shipment.carrier,
                tracking_no=shipment.tracking_no,
                status=shipment.status,
                current_node=shipment.current_node,
                shipped_at=shipment.shipped_at,
                delivered_at=shipment.delivered_at,
                items=[
                    ShipmentItemOut(
                        sku=order_item.sku,
                        title=order_item.title_snapshot,
                        qty=shipment_item.qty,
                    )
                    for shipment_item, order_item in shipment_items_by_id.get(
                        shipment.id, []
                    )
                ],
                events=[
                    ShipmentEventOut(
                        happened_at=event.happened_at,
                        node=event.node,
                        description=event.description,
                    )
                    for event in shipment_events_by_id.get(shipment.id, [])
                ],
            )
            for shipment in shipments
        ],
        address=AddressOut(
            recipient_name=order.recipient_name,
            recipient_phone=order.recipient_phone,
            region=order.address_region,
            city=order.address_city,
            detail=order.address_detail,
        ),
    )
