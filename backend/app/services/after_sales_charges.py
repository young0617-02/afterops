from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import AfterSalesCharge, Customer, Order, RepairRequest
from app.services.errors import ValidationError

CHARGE_KINDS = ("exchange_supplement", "repair_fee")


@dataclass(frozen=True)
class AfterSalesChargeView:
    id: int
    kind: str
    order_no: str
    customer_phone: str | None
    return_request_id: int | None
    repair_request_id: int | None
    amount_cents: int
    reference: str
    created_at: datetime


@dataclass(frozen=True)
class AfterSalesChargeLedgerView:
    items: tuple[AfterSalesChargeView, ...]
    count: int
    exchange_supplement_cents: int
    repair_fee_cents: int
    repair_parts_cost_cents: int
    repair_margin_cents: int
    total_cents: int
    days: int


def record_after_sales_charge(
    session: Session,
    *,
    merchant_id: int,
    customer_id: int,
    order_id: int,
    kind: str,
    amount_cents: int,
    reference: str,
    return_request_id: int | None = None,
    repair_request_id: int | None = None,
) -> AfterSalesCharge:
    if kind not in CHARGE_KINDS:
        raise ValidationError("未知售后收款类型")
    if amount_cents <= 0:
        raise ValidationError("售后收款金额必须大于 0")
    reference_text = reference.strip()
    if not reference_text:
        raise ValidationError("售后收款缺少支付引用")
    charge = AfterSalesCharge(
        merchant_id=merchant_id,
        customer_id=customer_id,
        order_id=order_id,
        kind=kind,
        return_request_id=return_request_id,
        repair_request_id=repair_request_id,
        amount_cents=amount_cents,
        reference=reference_text,
        created_at=datetime.now(UTC),
    )
    session.add(charge)
    session.flush()
    return charge


def after_sales_charge_ledger(
    session: Session,
    merchant_id: int,
    *,
    days: int = 30,
    limit: int = 100,
) -> AfterSalesChargeLedgerView:
    start = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(AfterSalesCharge, Order, Customer)
        .join(Order, Order.id == AfterSalesCharge.order_id)
        .outerjoin(Customer, Customer.id == AfterSalesCharge.customer_id)
        .where(
            AfterSalesCharge.merchant_id == merchant_id,
            AfterSalesCharge.created_at >= start,
        )
        .order_by(AfterSalesCharge.created_at.desc(), AfterSalesCharge.id.desc())
        .limit(limit)
    ).all()
    totals = {
        kind: (int(count), int(amount))
        for kind, count, amount in session.execute(
            select(
                AfterSalesCharge.kind,
                func.count(AfterSalesCharge.id),
                func.coalesce(func.sum(AfterSalesCharge.amount_cents), 0),
            )
            .where(
                AfterSalesCharge.merchant_id == merchant_id,
                AfterSalesCharge.created_at >= start,
            )
            .group_by(AfterSalesCharge.kind)
        ).all()
    }
    supplement_cents = totals.get("exchange_supplement", (0, 0))[1]
    repair_cents = totals.get("repair_fee", (0, 0))[1]
    repair_parts_cost = int(
        session.execute(
            select(
                func.coalesce(func.sum(RepairRequest.parts_cost_cents), 0)
            )
            .join(
                AfterSalesCharge,
                AfterSalesCharge.repair_request_id == RepairRequest.id,
            )
            .where(
                AfterSalesCharge.merchant_id == merchant_id,
                AfterSalesCharge.kind == "repair_fee",
                AfterSalesCharge.created_at >= start,
            )
        ).scalar_one()
    )
    count = sum(item[0] for item in totals.values())
    return AfterSalesChargeLedgerView(
        items=tuple(
            AfterSalesChargeView(
                id=charge.id,
                kind=charge.kind,
                order_no=order.order_no,
                customer_phone=customer.phone if customer else None,
                return_request_id=charge.return_request_id,
                repair_request_id=charge.repair_request_id,
                amount_cents=charge.amount_cents,
                reference=charge.reference,
                created_at=charge.created_at,
            )
            for charge, order, customer in rows
        ),
        count=count,
        exchange_supplement_cents=supplement_cents,
        repair_fee_cents=repair_cents,
        repair_parts_cost_cents=repair_parts_cost,
        repair_margin_cents=repair_cents - repair_parts_cost,
        total_cents=supplement_cents + repair_cents,
        days=days,
    )
