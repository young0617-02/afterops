from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Order,
    ReturnDeductionAppeal,
    ReturnItem,
    ReturnRequest,
)

RISK_WINDOW_DAYS = 90
HIGH_RETURN_COUNT = 3
HIGH_CANCELLED_EXCHANGE_COUNT = 2
HIGH_APPEAL_COUNT = 2
HIGH_CUSTOMER_RESPONSIBILITY_COUNT = 2
MEDIUM_RETURN_COUNT = 2


@dataclass(frozen=True)
class CustomerAfterSalesRiskView:
    level: str
    reasons: tuple[str, ...]
    window_days: int
    order_count: int
    return_count: int
    return_rate: float
    cancelled_exchange_count: int
    appeal_count: int
    customer_responsibility_count: int
    last_return_at: datetime | None


def _risk_view(
    *,
    order_count: int,
    return_count: int,
    last_return_at: datetime | None,
    cancelled_exchange_count: int,
    appeal_count: int,
    customer_responsibility_count: int,
) -> CustomerAfterSalesRiskView:
    return_rate = (
        min(return_count / order_count, 1.0) if order_count > 0 else 0.0
    )
    reasons: list[str] = []
    if return_count > 0:
        reasons.append(
            f"{RISK_WINDOW_DAYS} 天内退货 {return_count} 次"
            f"（订单 {order_count} 单）"
        )
    if cancelled_exchange_count > 0:
        reasons.append(f"顾客主动放弃补差换货 {cancelled_exchange_count} 次")
    if appeal_count > 0:
        reasons.append(f"扣减申诉 {appeal_count} 次")
    if customer_responsibility_count > 0:
        reasons.append(
            f"退货判为顾客责任 {customer_responsibility_count} 次"
        )
    high = (
        return_count >= HIGH_RETURN_COUNT
        or cancelled_exchange_count >= HIGH_CANCELLED_EXCHANGE_COUNT
        or appeal_count >= HIGH_APPEAL_COUNT
        or customer_responsibility_count >= HIGH_CUSTOMER_RESPONSIBILITY_COUNT
    )
    medium = (
        return_count >= MEDIUM_RETURN_COUNT
        or cancelled_exchange_count >= 1
        or appeal_count >= 1
        or customer_responsibility_count >= 1
    )
    return CustomerAfterSalesRiskView(
        level="high" if high else "medium" if medium else "low",
        reasons=tuple(reasons),
        window_days=RISK_WINDOW_DAYS,
        order_count=order_count,
        return_count=return_count,
        return_rate=return_rate,
        cancelled_exchange_count=cancelled_exchange_count,
        appeal_count=appeal_count,
        customer_responsibility_count=customer_responsibility_count,
        last_return_at=last_return_at,
    )


def customer_after_sales_risk_map(
    session: Session,
    merchant_id: int,
    customer_ids: list[int],
    *,
    now: datetime | None = None,
) -> dict[int, CustomerAfterSalesRiskView]:
    ids = [customer_id for customer_id in dict.fromkeys(customer_ids) if customer_id]
    if not ids:
        return {}
    current = now or datetime.now(UTC)
    start = current - timedelta(days=RISK_WINDOW_DAYS)
    order_counts = {
        int(customer_id): int(count)
        for customer_id, count in session.execute(
            select(Order.customer_id, func.count(Order.id))
            .where(
                Order.merchant_id == merchant_id,
                Order.customer_id.in_(ids),
                Order.payment_status.in_(
                    ("paid", "partially_refunded", "refunded")
                ),
                Order.placed_at >= start,
            )
            .group_by(Order.customer_id)
        ).all()
    }
    return_stats = {
        int(customer_id): (int(count), last_return)
        for customer_id, count, last_return in session.execute(
            select(
                ReturnRequest.customer_id,
                func.count(ReturnRequest.id),
                func.max(ReturnRequest.created_at),
            )
            .where(
                ReturnRequest.merchant_id == merchant_id,
                ReturnRequest.customer_id.in_(ids),
                ReturnRequest.created_at >= start,
                ReturnRequest.status.not_in(("withdrawn", "rejected")),
            )
            .group_by(ReturnRequest.customer_id)
        ).all()
    }
    cancelled_counts = {
        int(customer_id): int(count)
        for customer_id, count in session.execute(
            select(ReturnRequest.customer_id, func.count(ReturnRequest.id))
            .where(
                ReturnRequest.merchant_id == merchant_id,
                ReturnRequest.customer_id.in_(ids),
                ReturnRequest.created_at >= start,
                ReturnRequest.status == "exchange_cancelled",
                ReturnRequest.exchange_cancelled_by == "customer",
            )
            .group_by(ReturnRequest.customer_id)
        ).all()
    }
    appeal_counts = {
        int(customer_id): int(count)
        for customer_id, count in session.execute(
            select(
                ReturnRequest.customer_id,
                func.count(ReturnDeductionAppeal.id),
            )
            .join(
                ReturnRequest,
                ReturnRequest.id
                == ReturnDeductionAppeal.return_request_id,
            )
            .where(
                ReturnRequest.merchant_id == merchant_id,
                ReturnRequest.customer_id.in_(ids),
                ReturnDeductionAppeal.created_at >= start,
            )
            .group_by(ReturnRequest.customer_id)
        ).all()
    }
    responsibility_counts = {
        int(customer_id): int(count)
        for customer_id, count in session.execute(
            select(
                ReturnRequest.customer_id,
                func.count(func.distinct(ReturnRequest.id)),
            )
            .join(
                ReturnItem,
                ReturnItem.return_request_id == ReturnRequest.id,
            )
            .where(
                ReturnRequest.merchant_id == merchant_id,
                ReturnRequest.customer_id.in_(ids),
                ReturnRequest.created_at >= start,
                ReturnRequest.status.not_in(("withdrawn", "rejected")),
                ReturnItem.responsibility == "customer",
            )
            .group_by(ReturnRequest.customer_id)
        ).all()
    }
    result: dict[int, CustomerAfterSalesRiskView] = {}
    for customer_id in ids:
        return_count, last_return_at = return_stats.get(
            customer_id, (0, None)
        )
        result[customer_id] = _risk_view(
            order_count=order_counts.get(customer_id, 0),
            return_count=return_count,
            last_return_at=last_return_at,
            cancelled_exchange_count=cancelled_counts.get(customer_id, 0),
            appeal_count=appeal_counts.get(customer_id, 0),
            customer_responsibility_count=responsibility_counts.get(
                customer_id, 0
            ),
        )
    return result


def customer_after_sales_risk(
    session: Session,
    merchant_id: int,
    customer_id: int,
    *,
    now: datetime | None = None,
) -> CustomerAfterSalesRiskView:
    return customer_after_sales_risk_map(
        session, merchant_id, [customer_id], now=now
    )[customer_id]
