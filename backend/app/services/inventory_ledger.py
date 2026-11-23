from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    Inventory,
    InventoryMovement,
    Order,
    Product,
    User,
)
from app.services.errors import ConflictError


@dataclass(frozen=True)
class InventoryMovementView:
    id: int
    sku: str
    product_title: str
    warehouse_id: str
    warehouse_name: str
    qty_delta: int
    qty_after: int
    reason: str
    order_no: str | None
    return_id: int | None
    operator_email: str | None
    note: str
    created_at: datetime


def record_movement(
    session: Session,
    inventory: Inventory,
    qty_delta: int,
    reason: str,
    *,
    order_id: int | None = None,
    return_id: int | None = None,
    operator_user_id: int | None = None,
    note: str = "",
) -> InventoryMovement | None:
    if qty_delta == 0:
        return None
    qty_after = inventory.qty_available + qty_delta
    if qty_after < 0:
        raise ConflictError("库存不足")
    inventory.qty_available = qty_after
    movement = InventoryMovement(
        merchant_id=inventory.merchant_id,
        product_id=inventory.product_id,
        warehouse_id=inventory.warehouse_id,
        warehouse_name=inventory.warehouse_name,
        qty_delta=qty_delta,
        qty_after=qty_after,
        reason=reason,
        order_id=order_id,
        return_id=return_id,
        operator_user_id=operator_user_id,
        note=note,
    )
    session.add(movement)
    session.flush()
    return movement


def set_inventory_qty(
    session: Session,
    inventory: Inventory,
    new_qty: int,
    reason: str,
    *,
    operator_user_id: int | None = None,
    note: str = "",
) -> InventoryMovement | None:
    if new_qty < 0:
        raise ConflictError("库存不能为负数")
    return record_movement(
        session,
        inventory,
        new_qty - inventory.qty_available,
        reason,
        operator_user_id=operator_user_id,
        note=note,
    )


@dataclass(frozen=True)
class InventoryDiscrepancy:
    product_id: int
    sku: str
    product_title: str
    warehouse_id: str
    warehouse_name: str
    qty_available: int
    movement_total: int
    delta: int


@dataclass(frozen=True)
class InventoryReconciliation:
    tracked: int
    matched: int
    untracked: int
    discrepancies: tuple[InventoryDiscrepancy, ...]


def reconcile_inventory(
    session: Session, merchant_id: int
) -> InventoryReconciliation:
    rows = session.execute(
        select(
            Inventory,
            Product,
            func.count(InventoryMovement.id),
            func.coalesce(func.sum(InventoryMovement.qty_delta), 0),
        )
        .join(Product, Product.id == Inventory.product_id)
        .outerjoin(
            InventoryMovement,
            and_(
                InventoryMovement.product_id == Inventory.product_id,
                InventoryMovement.warehouse_id == Inventory.warehouse_id,
            ),
        )
        .where(Inventory.merchant_id == merchant_id)
        .group_by(Inventory.id, Product.id)
        .order_by(Product.sku, Inventory.warehouse_id)
    ).all()
    tracked = 0
    matched = 0
    untracked = 0
    discrepancies: list[InventoryDiscrepancy] = []
    for inventory, product, movement_count, movement_total in rows:
        if movement_count == 0:
            untracked += 1
            continue
        tracked += 1
        total = int(movement_total or 0)
        if total == inventory.qty_available:
            matched += 1
            continue
        discrepancies.append(
            InventoryDiscrepancy(
                product_id=product.id,
                sku=product.sku,
                product_title=product.title,
                warehouse_id=inventory.warehouse_id,
                warehouse_name=inventory.warehouse_name,
                qty_available=inventory.qty_available,
                movement_total=total,
                delta=inventory.qty_available - total,
            )
        )
    return InventoryReconciliation(
        tracked=tracked,
        matched=matched,
        untracked=untracked,
        discrepancies=tuple(discrepancies),
    )


def list_movements(
    session: Session,
    merchant_id: int,
    *,
    sku: str | None = None,
    warehouse_id: str | None = None,
    limit: int = 50,
) -> list[InventoryMovementView]:
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
    rows = session.execute(
        stmt.order_by(
            InventoryMovement.created_at.desc(),
            InventoryMovement.id.desc(),
        ).limit(limit)
    ).all()
    return [
        InventoryMovementView(
            id=movement.id,
            sku=product.sku,
            product_title=product.title,
            warehouse_id=movement.warehouse_id,
            warehouse_name=movement.warehouse_name,
            qty_delta=movement.qty_delta,
            qty_after=movement.qty_after,
            reason=movement.reason,
            order_no=order.order_no if order else None,
            return_id=movement.return_id,
            operator_email=user.email if user else None,
            note=movement.note,
            created_at=movement.created_at,
        )
        for movement, product, order, user in rows
    ]
