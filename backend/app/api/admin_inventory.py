from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.services.errors import ValidationError
from app.services.inventory_export import inventory_csv, movements_csv
from app.services.inventory_import import import_inventory_csv
from app.services.inventory_ledger import (
    list_movements,
    reconcile_inventory,
)
from app.services.inventory_valuation import inventory_valuation

router = APIRouter(prefix="/api/admin", tags=["admin"])


class InventoryMovementOut(BaseModel):
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


class InventoryDiscrepancyOut(BaseModel):
    product_id: int
    sku: str
    product_title: str
    warehouse_id: str
    warehouse_name: str
    qty_available: int
    movement_total: int
    delta: int


class InventoryReconciliationOut(BaseModel):
    tracked: int
    matched: int
    untracked: int
    discrepancies: list[InventoryDiscrepancyOut]


class InventoryImportOut(BaseModel):
    updated: int
    changed: int
    skus: list[str]


@router.get(
    "/inventory-reconciliation", response_model=InventoryReconciliationOut
)
def admin_inventory_reconciliation(
    admin: AdminDep, session: SessionDep
) -> InventoryReconciliationOut:
    result = reconcile_inventory(session, admin.merchant_id)
    return InventoryReconciliationOut(
        tracked=result.tracked,
        matched=result.matched,
        untracked=result.untracked,
        discrepancies=[
            InventoryDiscrepancyOut(
                product_id=row.product_id,
                sku=row.sku,
                product_title=row.product_title,
                warehouse_id=row.warehouse_id,
                warehouse_name=row.warehouse_name,
                qty_available=row.qty_available,
                movement_total=row.movement_total,
                delta=row.delta,
            )
            for row in result.discrepancies
        ],
    )


@router.post("/inventory/import", response_model=InventoryImportOut)
async def admin_inventory_import(
    file: Annotated[UploadFile, File()],
    admin: OwnerDep,
    session: SessionDep,
) -> InventoryImportOut:
    data = await file.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValidationError("CSV 不能超过 1MB")
    result = import_inventory_csv(
        session,
        admin.merchant_id,
        admin.user_id,
        data,
    )
    return InventoryImportOut(
        updated=result.updated,
        changed=result.changed,
        skus=list(result.skus),
    )


@router.get("/inventory/export.csv")
def admin_inventory_export(
    admin: AdminDep, session: SessionDep
) -> Response:
    return Response(
        content=inventory_csv(session, admin.merchant_id),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="inventory.csv"'
        },
    )


@router.get("/inventory-movements/export.csv")
def admin_inventory_movements_export(
    admin: AdminDep,
    session: SessionDep,
    sku: Annotated[str | None, Query(max_length=64)] = None,
    warehouse_id: Annotated[str | None, Query(max_length=64)] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> Response:
    return Response(
        content=movements_csv(
            session,
            admin.merchant_id,
            sku=sku,
            warehouse_id=warehouse_id,
            from_date=from_date,
            to_date=to_date,
        ),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                'attachment; filename="inventory-movements.csv"'
            )
        },
    )


class InventoryValueLineOut(BaseModel):
    sku: str
    title: str
    warehouse_id: str
    warehouse_name: str
    qty_available: int
    qty_reserved: int
    qty_on_hand: int
    cost_cents: int
    value_cents: int


class InventoryValuationOut(BaseModel):
    total_qty: int
    total_value_cents: int
    lines: list[InventoryValueLineOut]


@router.get(
    "/inventory-valuation", response_model=InventoryValuationOut
)
def admin_inventory_valuation(
    admin: OwnerDep,
    session: SessionDep,
    sku: Annotated[str | None, Query(max_length=64)] = None,
) -> InventoryValuationOut:
    result = inventory_valuation(session, admin.merchant_id, sku=sku)
    return InventoryValuationOut(
        total_qty=result.total_qty,
        total_value_cents=result.total_value_cents,
        lines=[
            InventoryValueLineOut(
                sku=line.sku,
                title=line.title,
                warehouse_id=line.warehouse_id,
                warehouse_name=line.warehouse_name,
                qty_available=line.qty_available,
                qty_reserved=line.qty_reserved,
                qty_on_hand=line.qty_on_hand,
                cost_cents=line.cost_cents,
                value_cents=line.value_cents,
            )
            for line in result.lines
        ],
    )


@router.get(
    "/inventory-movements", response_model=list[InventoryMovementOut]
)
def admin_inventory_movements(
    admin: AdminDep,
    session: SessionDep,
    sku: Annotated[str | None, Query(max_length=64)] = None,
    warehouse_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[InventoryMovementOut]:
    return [
        InventoryMovementOut(
            id=movement.id,
            sku=movement.sku,
            product_title=movement.product_title,
            warehouse_id=movement.warehouse_id,
            warehouse_name=movement.warehouse_name,
            qty_delta=movement.qty_delta,
            qty_after=movement.qty_after,
            reason=movement.reason,
            order_no=movement.order_no,
            return_id=movement.return_id,
            operator_email=movement.operator_email,
            note=movement.note,
            created_at=movement.created_at,
        )
        for movement in list_movements(
            session,
            admin.merchant_id,
            sku=sku,
            warehouse_id=warehouse_id,
            limit=limit,
        )
    ]
