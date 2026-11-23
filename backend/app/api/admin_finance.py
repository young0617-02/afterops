from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.domain.models import ActionProposal, Order, Payment, Refund
from app.domain.money import format_cents
from app.services.after_sales_charges import after_sales_charge_ledger
from app.services.refund_settlements import (
    RefundSettlementView,
    list_refunds_for_admin,
    retry_refund_settlement,
    settle_refund,
)
from app.settings import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


class FinanceAnomalyOut(BaseModel):
    order_no: str
    kind: str
    detail: str


class FinanceLedgerOut(BaseModel):
    at: datetime
    kind: str
    order_no: str
    amount_cents: int
    amount_display: str


class RefundSettlementOut(BaseModel):
    id: int
    order_no: str
    proposal_id: int
    kind: str
    amount_cents: int
    amount_display: str
    reason: str
    status: str
    provider_refund_id: str
    settlement_status: str
    settlement_reference: str
    settlement_failure_reason: str
    settlement_attempts: int
    created_at: datetime
    executed_at: datetime | None
    settled_at: datetime | None


class RefundSettleIn(BaseModel):
    succeeded: bool
    reference: str = Field(default="", max_length=128)
    failure_reason: str = Field(default="", max_length=1000)


class AfterSalesChargeOut(BaseModel):
    id: int
    kind: str
    order_no: str
    customer_phone: str | None
    return_request_id: int | None
    repair_request_id: int | None
    amount_cents: int
    amount_display: str
    reference: str
    created_at: datetime


class AfterSalesChargeLedgerOut(BaseModel):
    items: list[AfterSalesChargeOut]
    count: int
    exchange_supplement_cents: int
    exchange_supplement_display: str
    repair_fee_cents: int
    repair_fee_display: str
    repair_parts_cost_cents: int
    repair_parts_cost_display: str
    repair_margin_cents: int
    repair_margin_display: str
    total_cents: int
    total_display: str
    days: int


def _settlement_out(view: RefundSettlementView) -> RefundSettlementOut:
    return RefundSettlementOut(
        id=view.id,
        order_no=view.order_no,
        proposal_id=view.proposal_id,
        kind=view.kind,
        amount_cents=view.amount_cents,
        amount_display=view.amount_display,
        reason=view.reason,
        status=view.status,
        provider_refund_id=view.provider_refund_id,
        settlement_status=view.settlement_status,
        settlement_reference=view.settlement_reference,
        settlement_failure_reason=view.settlement_failure_reason,
        settlement_attempts=view.settlement_attempts,
        created_at=view.created_at,
        executed_at=view.executed_at,
        settled_at=view.settled_at,
    )


class FinanceSummaryOut(BaseModel):
    window_days: int
    paid_count: int
    paid_amount_cents: int
    paid_amount_display: str
    refund_count: int
    refund_amount_cents: int
    refund_amount_display: str
    compensation_count: int
    compensation_amount_cents: int
    compensation_amount_display: str
    net_amount_cents: int
    net_amount_display: str
    pending_proposal_count: int
    pending_proposal_amount_cents: int
    pending_proposal_amount_display: str
    refund_auto_settle_minutes: int
    refund_auto_retry_minutes: int
    refund_auto_retry_max_attempts: int
    anomalies: list[FinanceAnomalyOut]
    ledger: list[FinanceLedgerOut]


@dataclass(frozen=True)
class _Anomaly:
    order_no: str
    kind: str
    detail: str


def _sum_refunds(session: Session, merchant_id: int, start: datetime) -> tuple[int, int, int, int, int, int]:
    row = session.execute(
        select(
            func.count(Refund.id),
            func.coalesce(func.sum(Refund.amount_cents), 0),
        ).where(
            Refund.merchant_id == merchant_id,
            Refund.status == "executed",
            Refund.kind == "refund",
            Refund.executed_at.is_not(None),
            Refund.executed_at >= start,
        )
    ).one()
    comp = session.execute(
        select(
            func.count(Refund.id),
            func.coalesce(func.sum(Refund.amount_cents), 0),
        ).where(
            Refund.merchant_id == merchant_id,
            Refund.status == "executed",
            Refund.kind == "compensation",
            Refund.executed_at.is_not(None),
            Refund.executed_at >= start,
        )
    ).one()
    return int(row[0]), int(row[1]), int(comp[0]), int(comp[1]), 0, 0


def _anomalies(session: Session, merchant_id: int) -> list[_Anomaly]:
    rows: list[_Anomaly] = []
    over = session.execute(
        select(
            Order.order_no,
            Order.payable_cents,
            func.coalesce(func.sum(Refund.amount_cents), 0),
        )
        .join(Refund, Refund.order_id == Order.id)
        .where(
            Order.merchant_id == merchant_id,
            Refund.status == "executed",
            Refund.kind == "refund",
        )
        .group_by(Order.id)
        .having(func.coalesce(func.sum(Refund.amount_cents), 0) > Order.payable_cents)
        .limit(50)
    ).all()
    for order_no, payable, refunded in over:
        rows.append(
            _Anomaly(
                order_no=order_no,
                kind="refund_over_payable",
                detail=f"累计退款 {format_cents(int(refunded))} 超过订单应付 {format_cents(int(payable))}",
            )
        )

    paid_payment_exists = (
        select(Payment.id)
        .where(
            Payment.order_id == Order.id,
            Payment.status.in_(("paid", "partially_refunded", "refunded")),
        )
        .exists()
    )
    missing_payment = session.execute(
        select(Order.order_no)
        .where(
            Order.merchant_id == merchant_id,
            Order.payment_status.in_(("paid", "partially_refunded", "refunded")),
            ~paid_payment_exists,
        )
        .limit(50)
    ).scalars().all()
    for order_no in missing_payment:
        rows.append(
            _Anomaly(
                order_no=order_no,
                kind="paid_without_payment",
                detail="订单标记已支付，但没有已支付流水",
            )
        )

    refund_exists = (
        select(Refund.id)
        .where(
            Refund.order_id == Order.id,
            Refund.status == "executed",
            Refund.kind == "refund",
        )
        .exists()
    )
    refunded_without_ledger = session.execute(
        select(Order.order_no)
        .where(
            Order.merchant_id == merchant_id,
            Order.status.in_(("refunded", "partially_refunded")),
            ~refund_exists,
        )
        .limit(50)
    ).scalars().all()
    for order_no in refunded_without_ledger:
        rows.append(
            _Anomaly(
                order_no=order_no,
                kind="refund_status_without_ledger",
                detail="订单显示已退款/部分退款，但没有退款流水",
            )
        )
    return rows[:50]


@router.get("/finance", response_model=FinanceSummaryOut)
def admin_finance(
    admin: AdminDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=720)] = 30,
) -> FinanceSummaryOut:
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    merchant_id = admin.merchant_id

    paid_row = session.execute(
        select(
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount_cents), 0),
        ).where(
            Payment.merchant_id == merchant_id,
            Payment.paid_at.is_not(None),
            Payment.paid_at >= start,
            Payment.status.in_(("paid", "partially_refunded", "refunded")),
        )
    ).one()
    refund_count, refund_amount, comp_count, comp_amount, _, _ = _sum_refunds(
        session, merchant_id, start
    )

    pending = session.execute(
        select(
            func.count(ActionProposal.id),
            func.coalesce(func.sum(ActionProposal.payload_json["amount_cents"].as_integer()), 0),
        ).where(
            ActionProposal.merchant_id == merchant_id,
            ActionProposal.status == "pending",
            ActionProposal.action_type.in_(("refund", "compensation")),
        )
    ).one()

    ledger_rows: list[FinanceLedgerOut] = []
    payments = session.execute(
        select(Payment.paid_at, Payment.amount_cents, Order.order_no)
        .join(Order, Order.id == Payment.order_id)
        .where(
            Payment.merchant_id == merchant_id,
            Payment.paid_at.is_not(None),
            Payment.status.in_(("paid", "partially_refunded", "refunded")),
        )
        .order_by(Payment.paid_at.desc())
        .limit(30)
    ).all()
    for at, amount, order_no in payments:
        ledger_rows.append(
            FinanceLedgerOut(
                at=at,
                kind="payment",
                order_no=order_no,
                amount_cents=int(amount),
                amount_display=format_cents(int(amount)),
            )
        )
    refunds = session.execute(
        select(Refund.executed_at, Refund.amount_cents, Refund.kind, Order.order_no)
        .join(Order, Order.id == Refund.order_id)
        .where(
            Refund.merchant_id == merchant_id,
            Refund.status == "executed",
            Refund.executed_at.is_not(None),
        )
        .order_by(Refund.executed_at.desc())
        .limit(30)
    ).all()
    for at, amount, kind, order_no in refunds:
        ledger_rows.append(
            FinanceLedgerOut(
                at=at,
                kind=kind,
                order_no=order_no,
                amount_cents=int(amount),
                amount_display=format_cents(int(amount)),
            )
        )
    ledger_rows.sort(key=lambda row: row.at, reverse=True)

    paid_amount = int(paid_row[1])
    net = paid_amount - refund_amount
    return FinanceSummaryOut(
        window_days=days,
        paid_count=int(paid_row[0]),
        paid_amount_cents=paid_amount,
        paid_amount_display=format_cents(paid_amount),
        refund_count=refund_count,
        refund_amount_cents=refund_amount,
        refund_amount_display=format_cents(refund_amount),
        compensation_count=comp_count,
        compensation_amount_cents=comp_amount,
        compensation_amount_display=format_cents(comp_amount),
        net_amount_cents=net,
        net_amount_display=format_cents(net),
        pending_proposal_count=int(pending[0]),
        pending_proposal_amount_cents=int(pending[1]),
        pending_proposal_amount_display=format_cents(int(pending[1])),
        refund_auto_settle_minutes=settings.refund_auto_settle_minutes,
        refund_auto_retry_minutes=settings.refund_auto_retry_minutes,
        refund_auto_retry_max_attempts=(
            settings.refund_auto_retry_max_attempts
        ),
        anomalies=[
            FinanceAnomalyOut(order_no=row.order_no, kind=row.kind, detail=row.detail)
            for row in _anomalies(session, merchant_id)
        ],
        ledger=ledger_rows[:30],
    )


@router.get(
    "/finance/after-sales-charges",
    response_model=AfterSalesChargeLedgerOut,
)
def admin_after_sales_charges(
    admin: AdminDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=720)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> AfterSalesChargeLedgerOut:
    ledger = after_sales_charge_ledger(
        session, admin.merchant_id, days=days, limit=limit
    )
    return AfterSalesChargeLedgerOut(
        items=[
            AfterSalesChargeOut(
                id=item.id,
                kind=item.kind,
                order_no=item.order_no,
                customer_phone=item.customer_phone,
                return_request_id=item.return_request_id,
                repair_request_id=item.repair_request_id,
                amount_cents=item.amount_cents,
                amount_display=format_cents(item.amount_cents),
                reference=item.reference,
                created_at=item.created_at,
            )
            for item in ledger.items
        ],
        count=ledger.count,
        exchange_supplement_cents=ledger.exchange_supplement_cents,
        exchange_supplement_display=format_cents(
            ledger.exchange_supplement_cents
        ),
        repair_fee_cents=ledger.repair_fee_cents,
        repair_fee_display=format_cents(ledger.repair_fee_cents),
        repair_parts_cost_cents=ledger.repair_parts_cost_cents,
        repair_parts_cost_display=format_cents(
            ledger.repair_parts_cost_cents
        ),
        repair_margin_cents=ledger.repair_margin_cents,
        repair_margin_display=format_cents(ledger.repair_margin_cents),
        total_cents=ledger.total_cents,
        total_display=format_cents(ledger.total_cents),
        days=ledger.days,
    )


@router.get(
    "/finance/refunds", response_model=list[RefundSettlementOut]
)
def admin_finance_refunds(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[
        Literal["all", "processing", "succeeded", "failed"],
        Query(),
    ] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[RefundSettlementOut]:
    return [
        _settlement_out(view)
        for view in list_refunds_for_admin(
            session, admin.merchant_id, status=status, limit=limit
        )
    ]


@router.post(
    "/finance/refunds/{refund_id}/settle",
    response_model=RefundSettlementOut,
)
def admin_finance_settle_refund(
    refund_id: int,
    payload: RefundSettleIn,
    admin: OwnerDep,
    session: SessionDep,
) -> RefundSettlementOut:
    return _settlement_out(
        settle_refund(
            session,
            admin.merchant_id,
            admin.user_id,
            refund_id,
            succeeded=payload.succeeded,
            reference=payload.reference,
            failure_reason=payload.failure_reason,
        )
    )


@router.post(
    "/finance/refunds/{refund_id}/retry",
    response_model=RefundSettlementOut,
)
def admin_finance_retry_refund(
    refund_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> RefundSettlementOut:
    return _settlement_out(
        retry_refund_settlement(
            session, admin.merchant_id, admin.user_id, refund_id
        )
    )
