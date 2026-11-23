from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.services.merchant import demo_merchant_id
from app.services.shipping import (
    ShippingMethodView,
    create_method,
    list_methods,
    set_method_status,
)

router = APIRouter(tags=["shipping"])


class ShippingMethodOut(BaseModel):
    id: int
    code: str
    name: str
    fee_cents: int
    free_over_cents: int | None
    status: str
    sort_order: int
    created_at: datetime


class CreateShippingMethodIn(BaseModel):
    code: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    fee_cents: int = Field(ge=0, le=100_000)
    free_over_cents: int | None = Field(default=None, ge=0)
    sort_order: int = Field(default=0, ge=0, le=1000)


class ShippingMethodStatusIn(BaseModel):
    status: Literal["active", "disabled"]


def _out(method: ShippingMethodView) -> ShippingMethodOut:
    return ShippingMethodOut(
        id=method.id,
        code=method.code,
        name=method.name,
        fee_cents=method.fee_cents,
        free_over_cents=method.free_over_cents,
        status=method.status,
        sort_order=method.sort_order,
        created_at=method.created_at,
    )


@router.get(
    "/api/store/shipping-methods", response_model=list[ShippingMethodOut]
)
def public_shipping_methods(session: SessionDep) -> list[ShippingMethodOut]:
    merchant_id = demo_merchant_id(session)
    return [
        _out(method)
        for method in list_methods(session, merchant_id, active_only=True)
    ]


@router.get(
    "/api/admin/shipping-methods", response_model=list[ShippingMethodOut]
)
def admin_shipping_methods(
    admin: AdminDep, session: SessionDep
) -> list[ShippingMethodOut]:
    return [
        _out(method)
        for method in list_methods(session, admin.merchant_id)
    ]


@router.post(
    "/api/admin/shipping-methods", response_model=ShippingMethodOut
)
def admin_create_shipping_method(
    payload: CreateShippingMethodIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ShippingMethodOut:
    return _out(
        create_method(
            session,
            admin.merchant_id,
            admin.user_id,
            code=payload.code,
            name=payload.name,
            fee_cents=payload.fee_cents,
            free_over_cents=payload.free_over_cents,
            sort_order=payload.sort_order,
        )
    )


@router.patch(
    "/api/admin/shipping-methods/{method_id}",
    response_model=ShippingMethodOut,
)
def admin_shipping_method_status(
    method_id: int,
    payload: ShippingMethodStatusIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ShippingMethodOut:
    return _out(
        set_method_status(
            session,
            admin.merchant_id,
            admin.user_id,
            method_id,
            payload.status,
        )
    )
