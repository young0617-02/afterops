from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.admin import OwnerDep
from app.api.deps import SessionDep
from app.services.inventory_transfers import (
    cancel_stock_transfer,
    create_stock_transfer,
    list_stock_transfers,
    receive_stock_transfer,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class StockTransferOut(BaseModel):
    id: int
    transfer_no: str
    sku: str
    title: str
    from_warehouse_id: str
    to_warehouse_id: str
    qty: int
    status: str
    note: str
    created_by: int | None
    created_at: datetime
    received_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str


class CreateStockTransferIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    from_warehouse_id: str = Field(min_length=1, max_length=64)
    to_warehouse_id: str = Field(min_length=1, max_length=64)
    qty: int = Field(ge=1)
    note: str = Field(default="", max_length=500)


class CancelStockTransferIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.get("/stock-transfers", response_model=list[StockTransferOut])
def admin_stock_transfers(
    admin: OwnerDep,
    session: SessionDep,
    status: Annotated[
        Literal["all", "in_transit", "received", "cancelled"], Query()
    ] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[StockTransferOut]:
    return [
        StockTransferOut(**asdict(view))
        for view in list_stock_transfers(
            session, admin.merchant_id, status, limit
        )
    ]


@router.post("/stock-transfers", response_model=StockTransferOut)
def admin_create_stock_transfer(
    payload: CreateStockTransferIn,
    admin: OwnerDep,
    session: SessionDep,
) -> StockTransferOut:
    view = create_stock_transfer(
        session,
        admin.merchant_id,
        admin.user_id,
        payload.sku,
        payload.from_warehouse_id,
        payload.to_warehouse_id,
        payload.qty,
        payload.note,
    )
    return StockTransferOut(**asdict(view))


@router.post(
    "/stock-transfers/{transfer_id}/receive",
    response_model=StockTransferOut,
)
def admin_receive_stock_transfer(
    transfer_id: int,
    admin: OwnerDep,
    session: SessionDep,
) -> StockTransferOut:
    view = receive_stock_transfer(
        session, admin.merchant_id, admin.user_id, transfer_id
    )
    return StockTransferOut(**asdict(view))


@router.post(
    "/stock-transfers/{transfer_id}/cancel",
    response_model=StockTransferOut,
)
def admin_cancel_stock_transfer(
    transfer_id: int,
    payload: CancelStockTransferIn,
    admin: OwnerDep,
    session: SessionDep,
) -> StockTransferOut:
    view = cancel_stock_transfer(
        session,
        admin.merchant_id,
        admin.user_id,
        transfer_id,
        payload.reason,
    )
    return StockTransferOut(**asdict(view))