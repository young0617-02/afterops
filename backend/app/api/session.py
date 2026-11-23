from __future__ import annotations

import re

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import Customer
from app.services import otp, rate_limit
from app.services.auth import COOKIE_NAME, SESSION_TTL_SECONDS, sign_session_token
from app.services.csrf import CUSTOMER_CSRF_COOKIE, set_csrf_cookie
from app.services.customers import set_customer_email
from app.services.errors import UnauthorizedError, ValidationError
from app.services.merchant import demo_merchant_id
from app.settings import settings

router = APIRouter(prefix="/api/store", tags=["session"])
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class OtpRequestIn(BaseModel):
    phone: str = Field(min_length=11, max_length=11)


class OtpRequestOut(BaseModel):
    status: str
    expires_in: int
    debug_code: str | None = None


class SessionIn(BaseModel):
    phone: str = Field(min_length=11, max_length=11)
    code: str = Field(min_length=6, max_length=6)
    name: str | None = Field(default=None, max_length=120)


class CustomerOut(BaseModel):
    id: int
    phone: str | None
    name: str | None
    email: str | None
    notify_email_shipment: bool
    notify_email_after_sales: bool
    notify_email_return: bool


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=255)
    notify_email_shipment: bool | None = None
    notify_email_after_sales: bool | None = None
    notify_email_return: bool | None = None


def _out(customer: Customer) -> CustomerOut:
    return CustomerOut(
        id=customer.id,
        phone=customer.phone,
        name=customer.name,
        email=customer.email,
        notify_email_shipment=customer.notify_email_shipment,
        notify_email_after_sales=customer.notify_email_after_sales,
        notify_email_return=customer.notify_email_return,
    )


@router.post("/otp/request", response_model=OtpRequestOut, status_code=202)
def request_otp(
    payload: OtpRequestIn,
    request: Request,
    session: SessionDep,
) -> OtpRequestOut:
    if not PHONE_RE.match(payload.phone):
        raise ValidationError("手机号格式不正确")
    merchant_id = demo_merchant_id(session)
    rate_limit.check(
        session,
        f"otp:{_client_ip(request)}:{payload.phone}",
        settings.otp_request_limit,
        settings.otp_request_window_seconds,
    )
    code, ttl = otp.issue_code(session, merchant_id, payload.phone)
    return OtpRequestOut(
        status="sent",
        expires_in=ttl,
        debug_code=code if settings.app_env != "prod" else None,
    )


@router.post("/session", response_model=CustomerOut)
def create_session(
    payload: SessionIn,
    response: Response,
    request: Request,
    session: SessionDep,
) -> CustomerOut:
    if not PHONE_RE.match(payload.phone):
        raise ValidationError("手机号格式不正确")
    merchant_id = demo_merchant_id(session)
    rate_limit.check(
        session,
        f"login:{_client_ip(request)}:{payload.phone}",
        settings.login_limit,
        settings.login_window_seconds,
    )
    if not otp.verify_code(session, merchant_id, payload.phone, payload.code):
        raise UnauthorizedError("验证码无效或已过期")

    customer = session.execute(
        select(Customer).where(
            Customer.merchant_id == merchant_id,
            Customer.phone == payload.phone,
        )
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(
            merchant_id=merchant_id,
            phone=payload.phone,
            name=payload.name or f"顾客{payload.phone[-4:]}",
        )
        session.add(customer)
        session.commit()
        session.refresh(customer)
    token = sign_session_token(customer.id, merchant_id, settings.session_secret)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "prod",
        path="/",
    )
    set_csrf_cookie(response, CUSTOMER_CSRF_COOKIE, token)
    return _out(customer)


@router.get("/me", response_model=CustomerOut)
def me(customer: CurrentCustomer) -> CustomerOut:
    return _out(customer)


@router.patch("/me", response_model=CustomerOut)
def update_profile(
    payload: ProfileIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> CustomerOut:
    if "name" in payload.model_fields_set:
        customer.name = (payload.name or "").strip() or None
    if "email" in payload.model_fields_set:
        set_customer_email(session, customer, payload.email)
    for field in (
        "notify_email_shipment",
        "notify_email_after_sales",
        "notify_email_return",
    ):
        if field in payload.model_fields_set:
            setattr(customer, field, bool(getattr(payload, field)))
    session.commit()
    session.refresh(customer)
    return _out(customer)


@router.delete("/session")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CUSTOMER_CSRF_COOKIE, path="/")
    return {"status": "signed_out"}
