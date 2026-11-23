from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Customer, Order, ReturnItem, ReturnRequest, User
from app.services.return_analytics import RESPONSIBILITY_LABELS

DISPOSITION_LABELS = {
    "restock": "回补库存",
    "damaged": "报损不入库",
}


def _csv_safe(value: str) -> str:
    text = value or ""
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def return_inspection_report_csv(
    session: Session, merchant_id: int, days: int = 30
) -> str:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(ReturnItem, ReturnRequest, Order, Customer, User)
        .join(ReturnRequest, ReturnRequest.id == ReturnItem.return_request_id)
        .join(Order, Order.id == ReturnRequest.order_id)
        .outerjoin(Customer, Customer.id == ReturnRequest.customer_id)
        .outerjoin(User, User.id == ReturnItem.inspected_by)
        .where(
            ReturnRequest.merchant_id == merchant_id,
            ReturnItem.inspected_at.is_not(None),
            ReturnItem.inspected_at >= since,
        )
        .order_by(ReturnItem.inspected_at.desc(), ReturnItem.id.desc())
    ).all()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "退货单号",
            "订单号",
            "顾客手机号",
            "SKU",
            "商品",
            "数量",
            "处置",
            "责任判定",
            "扣减金额(元)",
            "质检说明",
            "质检人",
            "质检时间",
        ]
    )
    for item, request, order, customer, inspector in rows:
        inspected_at = item.inspected_at
        if inspected_at is not None and inspected_at.tzinfo is None:
            inspected_at = inspected_at.replace(tzinfo=UTC)
        writer.writerow(
            [
                request.id,
                _csv_safe(order.order_no),
                _csv_safe(customer.phone if customer else ""),
                _csv_safe(item.sku),
                _csv_safe(item.title_snapshot),
                item.qty,
                DISPOSITION_LABELS.get(
                    item.disposition or "", "未处置"
                ),
                RESPONSIBILITY_LABELS.get(
                    item.responsibility or "undetermined", "未判定"
                ),
                f"{item.inspection_deduction_cents / 100:.2f}",
                _csv_safe(request.inspection_note),
                _csv_safe(inspector.email if inspector else ""),
                (
                    inspected_at.strftime("%Y-%m-%d %H:%M:%S")
                    if inspected_at is not None
                    else ""
                ),
            ]
        )
    return buffer.getvalue()
