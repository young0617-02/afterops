from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Product, ReturnItem, ReturnRequest
from app.services.return_reasons import REASON_LABELS


@dataclass(frozen=True)
class ReturnReasonStat:
    category: str
    label: str
    count: int


@dataclass(frozen=True)
class ReturnSkuStat:
    sku: str
    title: str
    requests: int
    qty: int


@dataclass(frozen=True)
class ReturnResponsibilityStat:
    responsibility: str
    label: str
    items: int
    qty: int
    deduction_cents: int


RESPONSIBILITY_LABELS = {
    "merchant": "商家责任",
    "customer": "顾客责任",
    "logistics": "物流责任",
    "other": "其他",
    "undetermined": "未判定",
}


@dataclass(frozen=True)
class ReturnAnalytics:
    days: int
    since: datetime
    total: int
    status_counts: dict[str, int]
    reasons: tuple[ReturnReasonStat, ...]
    top_skus: tuple[ReturnSkuStat, ...]
    responsibilities: tuple[ReturnResponsibilityStat, ...]
    refund_total_cents: int
    approval_rate: float
    avg_approval_hours: float | None
    restocked_units: int
    damaged_units: int
    damaged_cost_cents: int


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def return_analytics(
    session: Session, merchant_id: int, days: int = 30
) -> ReturnAnalytics:
    since = datetime.now(UTC) - timedelta(days=days)
    requests = [
        request
        for request in session.execute(
            select(ReturnRequest).where(
                ReturnRequest.merchant_id == merchant_id,
                ReturnRequest.created_at >= since,
            )
        ).scalars().all()
    ]
    status_counts = {
        "requested": 0,
        "approved": 0,
        "in_transit": 0,
        "received": 0,
        "rejected": 0,
        "withdrawn": 0,
    }
    reason_counts = {category: 0 for category in REASON_LABELS}
    approved_hours: list[float] = []
    refund_total = 0
    for request in requests:
        if request.status in status_counts:
            status_counts[request.status] += 1
        category = (
            request.reason_category
            if request.reason_category in reason_counts
            else "other"
        )
        reason_counts[category] += 1
        refund_total += request.refund_amount_cents or 0
        if request.approved_at is not None:
            created = _as_utc(request.created_at)
            approved = _as_utc(request.approved_at)
            approved_hours.append(
                max(0.0, (approved - created).total_seconds() / 3600)
            )

    items = session.execute(
        select(ReturnItem, ReturnRequest)
        .join(ReturnRequest, ReturnRequest.id == ReturnItem.return_request_id)
        .where(
            ReturnRequest.merchant_id == merchant_id,
            ReturnRequest.created_at >= since,
        )
    ).all()
    costs = dict(
        session.execute(
            select(Product.sku, Product.cost_cents).where(
                Product.merchant_id == merchant_id
            )
        ).all()
    )
    restocked_units = sum(
        item.qty for item, _request in items if item.disposition == "restock"
    )
    damaged_items = [
        item for item, _request in items if item.disposition == "damaged"
    ]
    damaged_units = sum(item.qty for item in damaged_items)
    damaged_cost_cents = sum(
        item.qty * costs.get(item.sku, 0) for item in damaged_items
    )
    responsibility_stats = {
        key: {"items": 0, "qty": 0, "deduction_cents": 0}
        for key in RESPONSIBILITY_LABELS
    }
    for item, _request in items:
        key = item.responsibility or "undetermined"
        row = responsibility_stats[key]
        row["items"] += 1
        row["qty"] += item.qty
        row["deduction_cents"] += item.inspection_deduction_cents
    sku_stats: dict[str, dict] = {}
    for item, request in items:
        row = sku_stats.setdefault(
            item.sku,
            {
                "title": item.title_snapshot,
                "requests": set(),
                "qty": 0,
            },
        )
        row["requests"].add(request.id)
        row["qty"] += item.qty
    top_skus = sorted(
        sku_stats.items(),
        key=lambda pair: (pair[1]["qty"], len(pair[1]["requests"])),
        reverse=True,
    )[:5]

    total = len(requests)
    approved_count = sum(
        1 for request in requests if request.approved_at is not None
    )
    return ReturnAnalytics(
        days=days,
        since=since,
        total=total,
        status_counts=status_counts,
        reasons=tuple(
            ReturnReasonStat(
                category=category,
                label=label,
                count=reason_counts[category],
            )
            for category, label in REASON_LABELS.items()
        ),
        top_skus=tuple(
            ReturnSkuStat(
                sku=sku,
                title=row["title"],
                requests=len(row["requests"]),
                qty=row["qty"],
            )
            for sku, row in top_skus
        ),
        responsibilities=tuple(
            ReturnResponsibilityStat(
                responsibility=key,
                label=label,
                items=responsibility_stats[key]["items"],
                qty=responsibility_stats[key]["qty"],
                deduction_cents=responsibility_stats[key][
                    "deduction_cents"
                ],
            )
            for key, label in RESPONSIBILITY_LABELS.items()
        ),
        refund_total_cents=refund_total,
        approval_rate=(
            round(approved_count / total, 3) if total else 0.0
        ),
        avg_approval_hours=(
            round(sum(approved_hours) / len(approved_hours), 1)
            if approved_hours
            else None
        ),
        restocked_units=restocked_units,
        damaged_units=damaged_units,
        damaged_cost_cents=damaged_cost_cents,
    )