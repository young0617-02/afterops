from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentCustomer, SessionDep
from app.services.coupons import (
    ClaimableCouponView,
    WalletCouponView,
    claim_coupon,
    claimable_coupons,
    customer_coupon_wallet,
)

router = APIRouter(prefix="/api/store", tags=["coupons"])


class ClaimableCouponOut(BaseModel):
    id: int
    discount_type: str
    value: int
    min_spend_cents: int
    starts_on: date
    ends_on: date | None
    scope_type: str
    scope_values: list[str]
    claimed: bool
    sold_out: bool


class WalletCouponOut(ClaimableCouponOut):
    code: str
    status: str
    claimed_at: datetime
    times_used: int
    remaining_uses: int
    state: str


def _claimable(view: ClaimableCouponView) -> ClaimableCouponOut:
    return ClaimableCouponOut(
        id=view.id,
        discount_type=view.discount_type,
        value=view.value,
        min_spend_cents=view.min_spend_cents,
        starts_on=view.starts_on,
        ends_on=view.ends_on,
        scope_type=view.scope_type,
        scope_values=list(view.scope_values),
        claimed=view.claimed,
        sold_out=view.sold_out,
    )


def _wallet(view: WalletCouponView) -> WalletCouponOut:
    return WalletCouponOut(
        id=view.id,
        code=view.code,
        discount_type=view.discount_type,
        value=view.value,
        min_spend_cents=view.min_spend_cents,
        starts_on=view.starts_on,
        ends_on=view.ends_on,
        scope_type=view.scope_type,
        scope_values=list(view.scope_values),
        claimed=True,
        sold_out=False,
        status=view.status,
        claimed_at=view.claimed_at,
        times_used=view.times_used,
        remaining_uses=view.remaining_uses,
        state=view.state,
    )


@router.get("/coupons/claimable", response_model=list[ClaimableCouponOut])
def customer_claimable_coupons(
    customer: CurrentCustomer, session: SessionDep
) -> list[ClaimableCouponOut]:
    return [_claimable(view) for view in claimable_coupons(session, customer)]


@router.post(
    "/coupons/{coupon_id}/claim", response_model=WalletCouponOut
)
def customer_claim_coupon(
    coupon_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> WalletCouponOut:
    return _wallet(claim_coupon(session, customer, coupon_id))


@router.get("/coupons/wallet", response_model=list[WalletCouponOut])
def customer_coupon_wallet_endpoint(
    customer: CurrentCustomer, session: SessionDep
) -> list[WalletCouponOut]:
    return [
        _wallet(view)
        for view in customer_coupon_wallet(session, customer)
    ]
