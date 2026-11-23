from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.services.coupons import (
    CouponView,
    create_coupon,
    list_coupons,
    set_coupon_status,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CouponOut(BaseModel):
    id: int
    code: str
    discount_type: str
    value: int
    min_spend_cents: int
    starts_on: date
    ends_on: date | None
    max_uses: int | None
    used_count: int
    per_customer_limit: int
    scope_type: str
    scope_values: list[str]
    claimable: bool
    claim_count: int
    status: str
    created_at: datetime


class CreateCouponIn(BaseModel):
    code: str = Field(min_length=3, max_length=32)
    discount_type: Literal["fixed", "percent"]
    value: int = Field(ge=1, le=100_000_000)
    min_spend_cents: int = Field(default=0, ge=0)
    starts_on: date
    ends_on: date | None = None
    max_uses: int | None = Field(default=None, ge=1)
    per_customer_limit: int = Field(default=1, ge=1, le=100)
    scope_type: Literal["all", "category", "sku"] = "all"
    scope_values: list[str] = Field(default_factory=list, max_length=50)
    claimable: bool = False


class CouponStatusIn(BaseModel):
    status: Literal["active", "disabled"]


def _out(coupon: CouponView) -> CouponOut:
    return CouponOut(
        id=coupon.id,
        code=coupon.code,
        discount_type=coupon.discount_type,
        value=coupon.value,
        min_spend_cents=coupon.min_spend_cents,
        starts_on=coupon.starts_on,
        ends_on=coupon.ends_on,
        max_uses=coupon.max_uses,
        used_count=coupon.used_count,
        per_customer_limit=coupon.per_customer_limit,
        scope_type=coupon.scope_type,
        scope_values=list(coupon.scope_values),
        claimable=coupon.claimable,
        claim_count=coupon.claim_count,
        status=coupon.status,
        created_at=coupon.created_at,
    )


@router.get("/coupons", response_model=list[CouponOut])
def admin_coupons(
    admin: AdminDep, session: SessionDep
) -> list[CouponOut]:
    return [_out(coupon) for coupon in list_coupons(session, admin.merchant_id)]


@router.post("/coupons", response_model=CouponOut)
def admin_create_coupon(
    payload: CreateCouponIn,
    admin: OwnerDep,
    session: SessionDep,
) -> CouponOut:
    return _out(
        create_coupon(
            session,
            admin.merchant_id,
            admin.user_id,
            code=payload.code,
            discount_type=payload.discount_type,
            value=payload.value,
            min_spend_cents=payload.min_spend_cents,
            starts_on=payload.starts_on,
            ends_on=payload.ends_on,
            max_uses=payload.max_uses,
            per_customer_limit=payload.per_customer_limit,
            scope_type=payload.scope_type,
            scope_values=payload.scope_values,
            claimable=payload.claimable,
        )
    )


@router.patch("/coupons/{coupon_id}", response_model=CouponOut)
def admin_coupon_status(
    coupon_id: int,
    payload: CouponStatusIn,
    admin: OwnerDep,
    session: SessionDep,
) -> CouponOut:
    return _out(
        set_coupon_status(
            session,
            admin.merchant_id,
            admin.user_id,
            coupon_id,
            payload.status,
        )
    )
