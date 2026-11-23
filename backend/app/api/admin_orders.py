from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.admin import AdminDep
from app.api.deps import SessionDep
from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    Customer,
    Order,
    OrderItem,
    Payment,
    Refund,
    ReturnRequest,
    Shipment,
    ShipmentEvent,
    ShipmentItem,
)
from app.domain.money import format_cents
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminOrderItemOut(BaseModel):
    sku: str
    title: str
    qty: int
    cancelled_qty: int
    unit_price_cents: int
    unit_price_display: str
    line_total_cents: int
    line_total_display: str


class AdminPaymentOut(BaseModel):
    id: int
    provider: str
    provider_payment_id: str
    amount_cents: int
    amount_display: str
    status: str
    created_at: datetime
    paid_at: datetime | None


class AdminRefundOut(BaseModel):
    id: int
    proposal_id: int
    kind: str
    amount_cents: int
    amount_display: str
    status: str
    provider_refund_id: str
    created_at: datetime
    executed_at: datetime | None


class AdminShipmentEventOut(BaseModel):
    node: str
    description: str
    happened_at: datetime


class AdminShipmentItemOut(BaseModel):
    sku: str
    title: str
    qty: int


class AdminShipmentOut(BaseModel):
    id: int
    package_no: int
    carrier: str
    tracking_no: str
    status: str
    current_node: str | None
    created_at: datetime
    delivered_at: datetime | None
    items: list[AdminShipmentItemOut]
    events: list[AdminShipmentEventOut]


class AdminProposalOut(BaseModel):
    id: int
    action_type: str
    status: str
    risk_level: str
    amount_cents: int | None
    amount_display: str | None
    created_by: str
    created_at: datetime
    commerce_ref: str | None
    error: str | None


class AdminReturnOut(BaseModel):
    id: int
    status: str
    resolution: str
    reason: str
    exchange_sku: str | None
    created_at: datetime


class AdminOrderDetailOut(BaseModel):
    order_no: str
    status: str
    payment_status: str
    fulfillment_status: str
    customer_id: int
    customer_name: str | None
    customer_phone: str | None
    recipient_name: str
    recipient_phone: str
    region: str
    city: str
    detail: str
    item_total_cents: int
    item_total_display: str
    freight_cents: int
    freight_display: str
    discount_cents: int
    discount_display: str
    payable_cents: int
    payable_display: str
    currency: str
    placed_at: datetime
    items: list[AdminOrderItemOut]
    payments: list[AdminPaymentOut]
    refunds: list[AdminRefundOut]
    shipments: list[AdminShipmentOut]
    proposals: list[AdminProposalOut]
    returns: list[AdminReturnOut]


@router.get("/orders/{order_no}", response_model=AdminOrderDetailOut)
def admin_order_detail(
    order_no: str,
    admin: AdminDep,
    session: SessionDep,
) -> AdminOrderDetailOut:
    order = session.execute(
        select(Order).where(
            Order.merchant_id == admin.merchant_id,
            Order.order_no == order_no,
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")
    customer = session.get(Customer, order.customer_id)
    items = session.execute(
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
    ).scalars().all()
    payments = session.execute(
        select(Payment)
        .where(Payment.order_id == order.id)
        .order_by(Payment.id)
    ).scalars().all()
    refunds = session.execute(
        select(Refund)
        .where(Refund.order_id == order.id)
        .order_by(Refund.id)
    ).scalars().all()
    shipments = session.execute(
        select(Shipment)
        .where(Shipment.order_id == order.id)
        .order_by(Shipment.package_no, Shipment.id)
    ).scalars().all()
    shipment_ids = [shipment.id for shipment in shipments]
    events_by_shipment: dict[int, list[ShipmentEvent]] = {}
    items_by_shipment: dict[int, list[tuple[ShipmentItem, OrderItem]]] = {}
    if shipment_ids:
        events = session.execute(
            select(ShipmentEvent)
            .where(ShipmentEvent.shipment_id.in_(shipment_ids))
            .order_by(ShipmentEvent.happened_at, ShipmentEvent.id)
        ).scalars().all()
        for event in events:
            events_by_shipment.setdefault(event.shipment_id, []).append(event)
        shipment_item_rows = session.execute(
            select(ShipmentItem, OrderItem)
            .join(OrderItem, OrderItem.id == ShipmentItem.order_item_id)
            .where(ShipmentItem.shipment_id.in_(shipment_ids))
            .order_by(ShipmentItem.shipment_id, ShipmentItem.id)
        ).all()
        for shipment_item, order_item in shipment_item_rows:
            items_by_shipment.setdefault(
                shipment_item.shipment_id, []
            ).append((shipment_item, order_item))
    proposals = session.execute(
        select(ActionProposal)
        .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
        .where(
            AfterSalesCase.order_id == order.id,
            ActionProposal.merchant_id == admin.merchant_id,
        )
        .order_by(ActionProposal.id.desc())
    ).scalars().all()
    returns = session.execute(
        select(ReturnRequest)
        .where(ReturnRequest.order_id == order.id)
        .order_by(ReturnRequest.id.desc())
    ).scalars().all()

    return AdminOrderDetailOut(
        order_no=order.order_no,
        status=order.status,
        payment_status=order.payment_status,
        fulfillment_status=order.fulfillment_status,
        customer_id=order.customer_id,
        customer_name=customer.name if customer else None,
        customer_phone=customer.phone if customer else None,
        recipient_name=order.recipient_name,
        recipient_phone=order.recipient_phone,
        region=order.address_region,
        city=order.address_city,
        detail=order.address_detail,
        item_total_cents=order.item_total_cents,
        item_total_display=format_cents(order.item_total_cents),
        freight_cents=order.freight_cents,
        freight_display=format_cents(order.freight_cents),
        discount_cents=order.discount_cents,
        discount_display=format_cents(order.discount_cents),
        payable_cents=order.payable_cents,
        payable_display=format_cents(order.payable_cents),
        currency=order.currency,
        placed_at=order.placed_at,
        items=[
            AdminOrderItemOut(
                sku=item.sku,
                title=item.title_snapshot,
                qty=item.qty,
                cancelled_qty=item.cancelled_qty,
                unit_price_cents=item.unit_price_cents,
                unit_price_display=format_cents(item.unit_price_cents),
                line_total_cents=item.qty * item.unit_price_cents,
                line_total_display=format_cents(
                    item.qty * item.unit_price_cents
                ),
            )
            for item in items
        ],
        payments=[
            AdminPaymentOut(
                id=payment.id,
                provider=payment.provider,
                provider_payment_id=payment.provider_payment_id,
                amount_cents=payment.amount_cents,
                amount_display=format_cents(payment.amount_cents),
                status=payment.status,
                created_at=payment.created_at,
                paid_at=payment.paid_at,
            )
            for payment in payments
        ],
        refunds=[
            AdminRefundOut(
                id=refund.id,
                proposal_id=refund.proposal_id,
                kind=refund.kind,
                amount_cents=refund.amount_cents,
                amount_display=format_cents(refund.amount_cents),
                status=refund.status,
                provider_refund_id=refund.provider_refund_id,
                created_at=refund.created_at,
                executed_at=refund.executed_at,
            )
            for refund in refunds
        ],
        shipments=[
            AdminShipmentOut(
                id=shipment.id,
                package_no=shipment.package_no,
                carrier=shipment.carrier,
                tracking_no=shipment.tracking_no,
                status=shipment.status,
                current_node=shipment.current_node,
                created_at=shipment.created_at,
                delivered_at=shipment.delivered_at,
                items=[
                    AdminShipmentItemOut(
                        sku=order_item.sku,
                        title=order_item.title_snapshot,
                        qty=shipment_item.qty,
                    )
                    for shipment_item, order_item in items_by_shipment.get(
                        shipment.id, []
                    )
                ],
                events=[
                    AdminShipmentEventOut(
                        node=event.node,
                        description=event.description,
                        happened_at=event.happened_at,
                    )
                    for event in events_by_shipment.get(shipment.id, [])
                ],
            )
            for shipment in shipments
        ],
        proposals=[
            AdminProposalOut(
                id=proposal.id,
                action_type=proposal.action_type,
                status=proposal.status,
                risk_level=proposal.risk_level,
                amount_cents=(proposal.payload_json or {}).get("amount_cents"),
                amount_display=(
                    format_cents((proposal.payload_json or {})["amount_cents"])
                    if (proposal.payload_json or {}).get("amount_cents") is not None
                    else None
                ),
                created_by=proposal.created_by,
                created_at=proposal.created_at,
                commerce_ref=proposal.commerce_ref,
                error=proposal.error,
            )
            for proposal in proposals
        ],
        returns=[
            AdminReturnOut(
                id=request.id,
                status=request.status,
                resolution=request.resolution,
                reason=request.reason,
                exchange_sku=request.exchange_sku,
                created_at=request.created_at,
            )
            for request in returns
        ],
    )
