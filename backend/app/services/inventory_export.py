from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Inventory,
    InventoryMovement,
    Order,
    Product,
    User,
)

REASON_LABELS = {
    "opening_balance": "期初余额",
    "product_created": "商品创建",
    "admin_adjustment": "手工调整",
    "stocktake": "库存盘点",
    "transfer_out": "调拨调出",
    "transfer_in": "调拨调入",
    "transfer_cancel_in": "调拨取消回补",
    "checkout_reserved": "结算预留",
    "order_cancelled": "订单取消",
    "order_expired": "订单超时",
    "return_restocked": "退货回补",
    "exchange_allocated": "换货占用",
}
MAX_EXPORT_ROWS = 10000


def _cell(value: object) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@") or text[:1] in ("\t", "\r"):
        return f"'{text}"
    return text


def _csv(headers: list[str], rows: list[list[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return "\ufeff" + buffer.getvalue()


def inventory_csv(session: Session, merchant_id: int) -> str:
    rows = session.execute(
        select(Product, Inventory)
        .join(Inventory, Inventory.product_id == Product.id)
        .where(Product.merchant_id == merchant_id)
        .order_by(Product.sku, Inventory.warehouse_id)
    ).all()
    return _csv(
        [
            "SKU",
            "商品",
            "类目",
            "仓库编号",
            "仓库名称",
            "可售库存",
            "预留库存",
            "更新时间",
        ],
        [
            [
                product.sku,
                product.title,
                product.category,
                inventory.warehouse_id,
                inventory.warehouse_name,
                inventory.qty_available,
                inventory.qty_reserved,
                inventory.updated_at.isoformat() if inventory.updated_at else "",
            ]
            for product, inventory in rows
        ],
    )


def movements_csv(
    session: Session,
    merchant_id: int,
    *,
    sku: str | None = None,
    warehouse_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> str:
    stmt = (
        select(InventoryMovement, Product, Order, User)
        .join(Product, Product.id == InventoryMovement.product_id)
        .outerjoin(Order, Order.id == InventoryMovement.order_id)
        .outerjoin(User, User.id == InventoryMovement.operator_user_id)
        .where(InventoryMovement.merchant_id == merchant_id)
    )
    if sku and sku.strip():
        stmt = stmt.where(Product.sku == sku.strip().upper())
    if warehouse_id and warehouse_id.strip():
        stmt = stmt.where(
            InventoryMovement.warehouse_id == warehouse_id.strip()
        )
    if from_date is not None:
        stmt = stmt.where(
            InventoryMovement.created_at
            >= datetime.combine(from_date, time.min, tzinfo=UTC)
        )
    if to_date is not None:
        stmt = stmt.where(
            InventoryMovement.created_at
            < datetime.combine(
                to_date + timedelta(days=1), time.min, tzinfo=UTC
            )
        )
    rows = session.execute(
        stmt.order_by(
            InventoryMovement.created_at.desc(),
            InventoryMovement.id.desc(),
        ).limit(MAX_EXPORT_ROWS)
    ).all()
    return _csv(
        [
            "时间",
            "SKU",
            "商品",
            "仓库编号",
            "仓库名称",
            "变动",
            "结存",
            "原因",
            "订单号",
            "退货ID",
            "操作人",
            "备注",
        ],
        [
            [
                movement.created_at.isoformat() if movement.created_at else "",
                product.sku,
                product.title,
                movement.warehouse_id,
                movement.warehouse_name,
                movement.qty_delta,
                movement.qty_after,
                REASON_LABELS.get(movement.reason, movement.reason),
                order.order_no if order else "",
                movement.return_id or "",
                user.email if user else "",
                movement.note,
            ]
            for movement, product, order, user in rows
        ],
    )