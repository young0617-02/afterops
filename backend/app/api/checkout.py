from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.api.deps import CurrentCustomer, SessionDep
from app.domain.money import format_cents
from app.services import checkout as checkout_service
from app.services import rate_limit
from app.services.customers import normalize_email, set_customer_email
from app.services.errors import ValidationError
from app.settings import settings

router = APIRouter(prefix="/api/store", tags=["checkout"])
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class AddressIn(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=120)
    recipient_phone: str = Field(min_length=11, max_length=11)
    region: str = Field(default="", max_length=80)
    city: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=255)


class CheckoutIn(BaseModel):
    address: AddressIn
    email: str | None = Field(default=None, max_length=255)
    coupon_code: str | None = Field(default=None, max_length=32)
    shipping_method_code: str | None = Field(default=None, max_length=32)


class CheckoutOut(BaseModel):
    order_no: str
    payment_id: int
    amount_cents: int
    amount_display: str
    status: str
    expires_at: str | None
    discount_cents: int
    discount_display: str
    coupon_code: str | None
    freight_cents: int
    freight_display: str
    shipping_method_code: str | None
    shipping_method_name: str | None


@router.post("/checkout", response_model=CheckoutOut, status_code=201)
def checkout(
    payload: CheckoutIn,
    customer: CurrentCustomer,
    session: SessionDep,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> CheckoutOut:
    if not PHONE_RE.match(payload.address.recipient_phone):
        raise ValidationError("收货手机号格式不正确")
    email = normalize_email(payload.email) if payload.email is not None else None
    rate_limit.check(
        session,
        f"checkout:{customer.merchant_id}:{customer.id}",
        settings.checkout_limit,
        settings.checkout_window_seconds,
    )
    if payload.email is not None:
        set_customer_email(session, customer, email)
        session.commit()
    address = checkout_service.Address(
        recipient_name=payload.address.recipient_name,
        recipient_phone=payload.address.recipient_phone,
        region=payload.address.region,
        city=payload.address.city,
        detail=payload.address.detail,
    )
    result = checkout_service.checkout(
        session,
        customer,
        address,
        idempotency_key,
        payload.coupon_code,
        payload.shipping_method_code,
    )
    return CheckoutOut(
        order_no=result.order_no,
        payment_id=result.payment_id,
        amount_cents=result.amount_cents,
        amount_display=format_cents(result.amount_cents),
        status=result.status,
        expires_at=result.expires_at,
        discount_cents=result.discount_cents,
        discount_display=format_cents(result.discount_cents),
        coupon_code=result.coupon_code,
        freight_cents=result.freight_cents,
        freight_display=format_cents(result.freight_cents),
        shipping_method_code=result.shipping_method_code,
        shipping_method_name=result.shipping_method_name,
    )
