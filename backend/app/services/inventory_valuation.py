from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Inventory, Product


@dataclass(frozen=True)
class InventoryValueLine:
    sku: str
    title: str
    warehouse_id: str
    warehouse_name: str
    qty_available: int
    qty_reserved: int
    qty_on_hand: int
    cost_cents: int
    value_cents: int


@dataclass(frozen=True)
class InventoryValuation:
    total_qty: int
    total_value_cents: int
    lines: tuple[InventoryValueLine, ...]


def inventory_valuation(
    session: Session,
    merchant_id: int,
    limit: int = 200,
    sku: str | None = None,
) -> InventoryValuation:
    stmt = (
        select(Inventory, Product)
        .join(Product, Product.id == Inventory.product_id)
        .where(Inventory.merchant_id == merchant_id)
    )
    if sku:
        stmt = stmt.where(Product.sku == sku.strip().upper())
    rows = session.execute(stmt).all()
    lines: list[InventoryValueLine] = []
    total_qty = 0
    total_value = 0
    for inventory, product in rows:
        qty_on_hand = inventory.qty_available + inventory.qty_reserved
        if qty_on_hand <= 0:
            continue
        value_cents = qty_on_hand * product.cost_cents
        total_qty += qty_on_hand
        total_value += value_cents
        lines.append(
            InventoryValueLine(
                sku=product.sku,
                title=product.title,
                warehouse_id=inventory.warehouse_id,
                warehouse_name=inventory.warehouse_name,
                qty_available=inventory.qty_available,
                qty_reserved=inventory.qty_reserved,
                qty_on_hand=qty_on_hand,
                cost_cents=product.cost_cents,
                value_cents=value_cents,
            )
        )
    lines.sort(
        key=lambda line: (
            -line.value_cents,
            line.sku,
            line.warehouse_id,
        )
    )
    return InventoryValuation(
        total_qty=total_qty,
        total_value_cents=total_value,
        lines=tuple(lines[:limit]),
    )
