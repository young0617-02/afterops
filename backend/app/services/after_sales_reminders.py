from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Order,
    Refund,
    RepairRequest,
    ReturnDeductionAppeal,
    ReturnPickup,
    ReturnRequest,
)
from app.domain.money import format_cents
from app.services.notifications import notify_customer, notify_staff


def send_after_sales_reminders(
    session: Session,
    *,
    now: datetime | None = None,
    quote_hours: int = 24,
    settlement_hours: int = 24,
    appeal_hours: int = 24,
    supplement_hours: int = 24,
    supplement_timeout_hours: int = 72,
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    stats = {
        "pickup_overdue": 0,
        "quote_pending": 0,
        "refund_processing": 0,
        "appeal_pending": 0,
        "supplement_pending": 0,
    }
    pickup_rows = session.execute(
        select(ReturnPickup, ReturnRequest, Order)
        .join(
            ReturnRequest,
            ReturnRequest.id == ReturnPickup.return_request_id,
        )
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(
            ReturnPickup.status == "scheduled",
            ReturnPickup.scheduled_date < now.date(),
            ReturnPickup.reminded_at.is_(None),
        )
        .order_by(ReturnPickup.id)
        .limit(50)
        .with_for_update()
    ).all()
    for pickup, request, order in pickup_rows:
        notify_staff(
            session,
            request.merchant_id,
            notification_type="pickup_overdue",
            title=f"上门取件已超预约日期 #{request.id}",
            body=(
                f"订单 {order.order_no} · {pickup.scheduled_date} "
                f"{pickup.time_window} · {pickup.pickup_no}"
            ),
            link="/admin",
        )
        pickup.reminded_at = now
        stats["pickup_overdue"] += 1

    quote_cutoff = now - timedelta(hours=quote_hours)
    quote_since = func.coalesce(
        RepairRequest.quote_revised_at, RepairRequest.quote_created_at
    )
    quote_rows = session.execute(
        select(RepairRequest, Order)
        .join(Order, Order.id == RepairRequest.order_id)
        .where(
            RepairRequest.quote_status == "pending",
            quote_since.is_not(None),
            quote_since <= quote_cutoff,
            RepairRequest.quote_reminded_at.is_(None),
        )
        .order_by(RepairRequest.id)
        .limit(50)
        .with_for_update()
    ).all()
    for repair, order in quote_rows:
        notify_customer(
            session,
            order.merchant_id,
            repair.customer_id,
            notification_type="repair_quote_reminder",
            title="维修报价待确认",
            body=(
                f"订单 {order.order_no} · 维修费 "
                f"{format_cents(repair.quote_cents or 0)} 元 · "
                f"{repair.quote_note}"
            ),
            link="/after-sales",
        )
        repair.quote_reminded_at = now
        stats["quote_pending"] += 1

    settlement_cutoff = now - timedelta(hours=settlement_hours)
    refund_rows = session.execute(
        select(Refund, Order)
        .join(Order, Order.id == Refund.order_id)
        .where(
            Refund.settlement_status == "processing",
            Refund.executed_at.is_not(None),
            Refund.executed_at <= settlement_cutoff,
            Refund.settlement_reminded_at.is_(None),
        )
        .order_by(Refund.id)
        .limit(50)
        .with_for_update()
    ).all()
    for refund, order in refund_rows:
        notify_staff(
            session,
            refund.merchant_id,
            notification_type="refund_settlement_overdue",
            title="退款渠道处理超时",
            body=(
                f"订单 {order.order_no} · "
                f"{format_cents(refund.amount_cents)} 元 · "
                f"{refund.provider_refund_id}"
            ),
            link="/admin",
        )
        refund.settlement_reminded_at = now
        stats["refund_processing"] += 1

    supplement_cutoff = now - timedelta(hours=supplement_hours)
    supplement_rows = session.execute(
        select(ReturnRequest, Order)
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(
            ReturnRequest.status == "received",
            ReturnRequest.resolution == "exchange",
            ReturnRequest.exchange_payment_status == "pending",
            ReturnRequest.exchange_supplement_cents > 0,
            ReturnRequest.received_at.is_not(None),
            ReturnRequest.received_at <= supplement_cutoff,
            ReturnRequest.exchange_supplement_reminded_at.is_(None),
        )
        .order_by(ReturnRequest.id)
        .limit(50)
        .with_for_update()
    ).all()
    for request, order in supplement_rows:
        deadline_text = ""
        deadline = request.exchange_supplement_deadline
        if deadline is None and supplement_timeout_hours > 0 and request.received_at is not None:
            deadline = request.received_at + timedelta(
                hours=supplement_timeout_hours
            )
        if deadline is not None:
            deadline_text = (
                f" · 请在 {deadline:%Y-%m-%d %H:%M} 前支付，"
                "逾期将自动取消换货并生成退款提案"
            )
        notify_customer(
            session,
            request.merchant_id,
            request.customer_id,
            notification_type="return_exchange_supplement_reminder",
            title="换货补差价待支付",
            body=(
                f"订单 {order.order_no} · 补差 "
                f"{format_cents(request.exchange_supplement_cents)} 元"
                f"{deadline_text}"
            ),
            link="/after-sales",
        )
        request.exchange_supplement_reminded_at = now
        stats["supplement_pending"] += 1

    appeal_cutoff = now - timedelta(hours=appeal_hours)
    appeal_rows = session.execute(
        select(ReturnDeductionAppeal, ReturnRequest, Order)
        .join(
            ReturnRequest,
            ReturnRequest.id == ReturnDeductionAppeal.return_request_id,
        )
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(
            ReturnDeductionAppeal.status == "pending",
            ReturnDeductionAppeal.created_at <= appeal_cutoff,
            ReturnDeductionAppeal.reminded_at.is_(None),
        )
        .order_by(ReturnDeductionAppeal.id)
        .limit(50)
        .with_for_update()
    ).all()
    for appeal, request, order in appeal_rows:
        notify_staff(
            session,
            request.merchant_id,
            notification_type="return_appeal_overdue",
            title="扣减申诉待处理超时",
            body=(
                f"订单 {order.order_no} · 申请 "
                f"{format_cents(appeal.requested_cents)} 元"
            ),
            link="/admin",
        )
        appeal.reminded_at = now
        stats["appeal_pending"] += 1

    session.commit()
    return stats
