from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    Inventory,
    InvoiceRequest,
    Order,
    OrderItem,
    Product,
    Refund,
    RepairRequest,
    ReturnDeductionAppeal,
    ReturnRequest,
    Shipment,
    ShipmentEvent,
)
from app.domain.money import format_cents
from app.services.errors import NotFoundError
from app.services.policies import current_policy


@dataclass
class ToolFact:
    tool: str
    ok: bool
    status: str
    data: dict = field(default_factory=dict)
    citation: str | None = None
    numbers: list[str] = field(default_factory=list)


def get_order(
    session: Session,
    merchant_id: int,
    customer_id: int,
    order_no: str,
) -> ToolFact:
    order = session.execute(
        select(Order).where(
            Order.order_no == order_no,
            Order.merchant_id == merchant_id,
            Order.customer_id == customer_id,
        )
    ).scalar_one_or_none()
    if order is None:
        return ToolFact("get_order", False, "not_found", {"order_no": order_no})

    item_count = session.execute(
        select(func.count()).select_from(OrderItem).where(OrderItem.order_id == order.id)
    ).scalar_one()
    return ToolFact(
        "get_order",
        True,
        "ok",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "status": order.status,
            "payment_status": order.payment_status,
            "fulfillment_status": order.fulfillment_status,
            "payable_cents": order.payable_cents,
            "payable_display": format_cents(order.payable_cents),
            "item_count": int(item_count),
            "placed_at": order.placed_at.isoformat(),
        },
        citation=f"order:{order.order_no}",
        numbers=[
            order.order_no,
            str(order.payable_cents),
            format_cents(order.payable_cents),
        ],
    )


def get_shipment(session: Session, merchant_id: int, order_id: int) -> ToolFact:
    shipments = session.execute(
        select(Shipment)
        .where(
            Shipment.order_id == order_id,
            Shipment.merchant_id == merchant_id,
        )
        .order_by(Shipment.package_no, Shipment.id)
    ).scalars().all()
    if not shipments:
        return ToolFact("get_shipment", False, "not_found", {"order_id": order_id})

    events_by_shipment: dict[int, list] = {}
    events = session.execute(
        select(ShipmentEvent)
        .where(ShipmentEvent.shipment_id.in_([s.id for s in shipments]))
        .order_by(ShipmentEvent.happened_at)
    ).scalars().all()
    for event in events:
        events_by_shipment.setdefault(event.shipment_id, []).append(event)
    packages = [
        {
            "package_no": shipment.package_no,
            "carrier": shipment.carrier,
            "tracking_no": shipment.tracking_no,
            "status": shipment.status,
            "current_node": shipment.current_node,
            "shipped_at": (
                shipment.shipped_at.isoformat() if shipment.shipped_at else None
            ),
            "delivered_at": (
                shipment.delivered_at.isoformat()
                if shipment.delivered_at
                else None
            ),
            "events": [
                {
                    "happened_at": event.happened_at.isoformat(),
                    "node": event.node,
                    "description": event.description,
                }
                for event in events_by_shipment.get(shipment.id, [])
            ],
        }
        for shipment in shipments
    ]
    tracking_numbers = [shipment.tracking_no for shipment in shipments]
    return ToolFact(
        "get_shipment",
        True,
        "ok",
        {"packages": packages},
        citation=(
            f"shipment:{tracking_numbers[0]}"
            if len(tracking_numbers) == 1
            else "shipment:multiple"
        ),
        numbers=tracking_numbers,
    )


def get_after_sales(
    session: Session, merchant_id: int, order_id: int
) -> ToolFact:
    proposals = session.execute(
        select(ActionProposal)
        .join(AfterSalesCase, AfterSalesCase.id == ActionProposal.after_sales_case_id)
        .where(
            AfterSalesCase.order_id == order_id,
            ActionProposal.merchant_id == merchant_id,
        )
        .order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc())
        .limit(3)
    ).scalars().all()
    if not proposals:
        return ToolFact("get_after_sales", False, "not_found", {"order_id": order_id})

    latest = proposals[0]
    payload = latest.payload_json or {}
    amount_cents = payload.get("amount_cents")
    if amount_cents is None:
        amount_cents = payload.get("amount")
    numbers = [str(latest.id)]
    if amount_cents is not None:
        numbers.append(format_cents(int(amount_cents)))
    if latest.commerce_ref:
        numbers.append(latest.commerce_ref)
    return ToolFact(
        "get_after_sales",
        True,
        "ok",
        {
            "proposal_id": latest.id,
            "action_type": latest.action_type,
            "status": latest.status,
            "amount_cents": int(amount_cents) if amount_cents is not None else None,
            "reason": payload.get("reason", ""),
            "message": latest.error if latest.status == "rejected" else None,
            "created_at": latest.created_at.isoformat(),
            "approved_at": (
                latest.approved_at.isoformat() if latest.approved_at else None
            ),
            "executed_at": (
                latest.executed_at.isoformat() if latest.executed_at else None
            ),
            "commerce_ref": latest.commerce_ref,
            "history": [
                {
                    "proposal_id": proposal.id,
                    "action_type": proposal.action_type,
                    "status": proposal.status,
                    "created_at": proposal.created_at.isoformat(),
                }
                for proposal in proposals
            ],
        },
        citation=f"proposal:{latest.id}",
        numbers=numbers,
    )


def search_policy(session: Session, merchant_id: int, topic: str) -> ToolFact:
    try:
        entry = current_policy(session, merchant_id, topic)
    except NotFoundError:
        return ToolFact("search_policy", False, "not_found", {"topic": topic})

    return ToolFact(
        "search_policy",
        True,
        "ok",
        {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "topic": entry.topic,
            "title": entry.title,
            "body": entry.body,
            "source": entry.source,
            "valid_from": entry.valid_from.isoformat(),
            "valid_until": entry.valid_until.isoformat() if entry.valid_until else None,
            "published_at": entry.published_at.isoformat(),
        },
        citation=f"{entry.entry_id}@{entry.version}",
        numbers=re.findall(r"\d+(?:\.\d+)?", entry.body),
    )


def get_refunds(
    session: Session, merchant_id: int, order_id: int
) -> ToolFact:
    refunds = session.execute(
        select(Refund)
        .where(
            Refund.merchant_id == merchant_id,
            Refund.order_id == order_id,
        )
        .order_by(Refund.id)
    ).scalars().all()
    if not refunds:
        return ToolFact("get_refunds", False, "not_found", {"order_id": order_id})
    proposal_ids = [
        refund.proposal_id for refund in refunds if refund.proposal_id
    ]
    reasons = (
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
    total = sum(
        refund.amount_cents
        for refund in refunds
        if refund.kind == "refund" and refund.status == "executed"
    )
    return ToolFact(
        "get_refunds",
        True,
        "ok",
        {
            "refunds": [
                {
                    "kind": refund.kind,
                    "amount_cents": refund.amount_cents,
                    "amount_display": format_cents(refund.amount_cents),
                    "status": refund.status,
                    "provider_refund_id": refund.provider_refund_id,
                    "settlement_status": refund.settlement_status,
                    "settlement_reference": refund.settlement_reference,
                    "settlement_failure_reason": (
                        refund.settlement_failure_reason
                    ),
                    "settled_at": (
                        refund.settled_at.isoformat()
                        if refund.settled_at
                        else None
                    ),
                    "reason": (
                        reasons.get(refund.proposal_id, "")
                        if refund.proposal_id
                        else ""
                    ),
                    "executed_at": (
                        refund.executed_at.isoformat()
                        if refund.executed_at
                        else None
                    ),
                }
                for refund in refunds
            ],
            "refunded_total_cents": total,
            "refunded_total_display": format_cents(total),
        },
        citation=f"refunds:{order_id}",
        numbers=[format_cents(refund.amount_cents) for refund in refunds],
    )


def get_invoice_status(
    session: Session, merchant_id: int, order_id: int
) -> ToolFact:
    invoice = session.execute(
        select(InvoiceRequest)
        .where(
            InvoiceRequest.merchant_id == merchant_id,
            InvoiceRequest.order_id == order_id,
        )
        .order_by(InvoiceRequest.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if invoice is None:
        return ToolFact(
            "get_invoice_status", False, "not_found", {"order_id": order_id}
        )
    order = session.get(Order, order_id)
    if order is None:
        return ToolFact(
            "get_invoice_status", False, "not_found", {"order_id": order_id}
        )
    refunded = int(
        session.execute(
            select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(
                Refund.order_id == order_id,
                Refund.kind == "refund",
                Refund.status == "executed",
            )
        ).scalar_one()
        or 0
    )
    net_paid = max(order.payable_cents - refunded, 0)
    adjustment = (
        invoice.status in ("requested", "issued")
        and invoice.amount_cents != net_paid
    )
    return ToolFact(
        "get_invoice_status",
        True,
        "ok",
        {
            "invoice_id": invoice.id,
            "status": invoice.status,
            "invoice_no": invoice.invoice_no,
            "amount_cents": invoice.amount_cents,
            "amount_display": format_cents(invoice.amount_cents),
            "net_paid_cents": net_paid,
            "net_paid_display": format_cents(net_paid),
            "adjustment_required": adjustment,
            "void_reason": invoice.void_reason,
        },
        citation=f"invoice:{invoice.id}",
        numbers=[format_cents(invoice.amount_cents), format_cents(net_paid)],
    )


def get_repairs(
    session: Session, merchant_id: int, order_id: int
) -> ToolFact:
    rows = session.execute(
        select(RepairRequest, OrderItem)
        .join(OrderItem, OrderItem.id == RepairRequest.order_item_id)
        .where(
            RepairRequest.merchant_id == merchant_id,
            RepairRequest.order_id == order_id,
        )
        .order_by(RepairRequest.id.desc())
        .limit(10)
    ).all()
    if not rows:
        return ToolFact("get_repairs", False, "not_found", {"order_id": order_id})
    return ToolFact(
        "get_repairs",
        True,
        "ok",
        {
            "repairs": [
                {
                    "id": repair.id,
                    "sku": item.sku,
                    "status": repair.status,
                    "carrier": repair.carrier,
                    "tracking_no": repair.tracking_no,
                    "return_carrier": repair.return_carrier,
                    "return_tracking_no": repair.return_tracking_no,
                    "resolution_note": repair.resolution_note,
                    "received_at": (
                        repair.received_at.isoformat()
                        if repair.received_at
                        else None
                    ),
                    "repaired_at": (
                        repair.repaired_at.isoformat()
                        if repair.repaired_at
                        else None
                    ),
                    "completed_at": (
                        repair.completed_at.isoformat()
                        if repair.completed_at
                        else None
                    ),
                }
                for repair, item in rows
            ]
        },
        citation=f"repair:{rows[0][0].id}",
        numbers=[
            tracking
            for repair, _item in rows
            for tracking in (repair.tracking_no, repair.return_tracking_no)
            if tracking
        ],
    )


def _appeal_fact(appeal: ReturnDeductionAppeal | None) -> dict | None:
    if appeal is None:
        return None
    return {
        "id": appeal.id,
        "status": appeal.status,
        "requested_cents": appeal.requested_cents,
        "requested_display": format_cents(appeal.requested_cents),
        "approved_cents": appeal.approved_cents,
        "approved_display": (
            format_cents(appeal.approved_cents)
            if appeal.approved_cents is not None
            else None
        ),
    }


def get_returns(
    session: Session, merchant_id: int, order_id: int
) -> ToolFact:
    returns = session.execute(
        select(ReturnRequest)
        .where(
            ReturnRequest.merchant_id == merchant_id,
            ReturnRequest.order_id == order_id,
        )
        .order_by(ReturnRequest.id.desc())
        .limit(10)
    ).scalars().all()
    if not returns:
        return ToolFact("get_returns", False, "not_found", {"order_id": order_id})
    replacement_ids = [
        request.replacement_order_id
        for request in returns
        if request.replacement_order_id is not None
    ]
    replacement_orders = {
        order.id: order.order_no
        for order in session.execute(
            select(Order).where(Order.id.in_(replacement_ids))
        ).scalars().all()
    } if replacement_ids else {}
    replacement_shipments: dict[int, list[Shipment]] = {}
    if replacement_ids:
        for shipment in session.execute(
            select(Shipment)
            .where(Shipment.order_id.in_(replacement_ids))
            .order_by(Shipment.package_no, Shipment.id)
        ).scalars().all():
            replacement_shipments.setdefault(shipment.order_id, []).append(
                shipment
            )
    appeals = {
        appeal.return_request_id: appeal
        for appeal in session.execute(
            select(ReturnDeductionAppeal).where(
                ReturnDeductionAppeal.return_request_id.in_(
                    [request.id for request in returns]
                )
            )
        ).scalars().all()
    }
    return ToolFact(
        "get_returns",
        True,
        "ok",
        {
            "returns": [
                {
                    "id": request.id,
                    "status": request.status,
                    "resolution": request.resolution,
                    "refund_amount_cents": request.refund_amount_cents,
                    "refund_amount_display": (
                        format_cents(request.refund_amount_cents)
                        if request.refund_amount_cents is not None
                        else None
                    ),
                    "refund_proposal_id": request.refund_proposal_id,
                    "replacement_order_id": request.replacement_order_id,
                    "replacement_order_no": replacement_orders.get(
                        request.replacement_order_id
                    ),
                    "exchange_supplement_cents": (
                        request.exchange_supplement_cents
                    ),
                    "exchange_supplement_display": (
                        format_cents(request.exchange_supplement_cents)
                        if request.exchange_supplement_cents > 0
                        else None
                    ),
                    "exchange_payment_status": (
                        request.exchange_payment_status
                    ),
                    "exchange_payment_ref": request.exchange_payment_ref,
                    "replacement_shipments": [
                        {
                            "package_no": shipment.package_no,
                            "carrier": shipment.carrier,
                            "tracking_no": shipment.tracking_no,
                            "status": shipment.status,
                            "current_node": shipment.current_node,
                        }
                        for shipment in replacement_shipments.get(
                            request.replacement_order_id, []
                        )
                    ],
                    "carrier": request.carrier,
                    "tracking_no": request.tracking_no,
                    "shipment_status": request.shipment_status,
                    "inspection_deduction_cents": (
                        request.inspection_deduction_cents
                    ),
                    "inspection_note": request.inspection_note,
                    "appeal": _appeal_fact(appeals.get(request.id)),
                    "items": [
                        {
                            "sku": item.sku,
                            "qty": item.qty,
                            "disposition": item.disposition,
                        }
                        for item in request.items
                    ],
                }
                for request in returns
            ]
        },
        citation=f"return:{returns[0].id}",
        numbers=[
            format_cents(request.refund_amount_cents)
            for request in returns
            if request.refund_amount_cents is not None
        ]
        + [
            format_cents(appeal.requested_cents)
            for appeal in appeals.values()
        ]
        + [
            format_cents(appeal.approved_cents)
            for appeal in appeals.values()
            if appeal.approved_cents is not None
        ],
    )


def get_product(
    session: Session,
    merchant_id: int,
    sku: str | None = None,
    keyword: str | None = None,
) -> ToolFact:
    stmt = select(Product).where(
        Product.merchant_id == merchant_id,
        Product.status == "active",
    )
    if sku:
        stmt = stmt.where(Product.sku == sku)
    elif keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                Product.title.ilike(pattern),
                Product.category.ilike(pattern),
                Product.brand.ilike(pattern),
                Product.sku.ilike(pattern),
            )
        )
    else:
        return ToolFact("get_product", False, "not_found", {})

    product = session.execute(stmt.order_by(Product.id).limit(1)).scalar_one_or_none()
    if product is None:
        return ToolFact(
            "get_product", False, "not_found", {"sku": sku, "keyword": keyword}
        )

    inventory = session.execute(
        select(Inventory).where(Inventory.product_id == product.id)
    ).scalars().all()
    available = sum(row.qty_available for row in inventory)
    return ToolFact(
        "get_product",
        True,
        "ok",
        {
            "sku": product.sku,
            "title": product.title,
            "category": product.category,
            "brand": product.brand,
            "specs": product.specs,
            "price_cents": product.price_cents,
            "price_display": format_cents(product.price_cents),
            "support_7d_no_reason": product.support_7d_no_reason,
            "available_qty": available,
            "in_stock": available > 0,
        },
        citation=f"product:{product.sku}",
        numbers=[product.sku, str(product.price_cents), format_cents(product.price_cents), str(available)],
    )
