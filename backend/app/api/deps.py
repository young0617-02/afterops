from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain.models import Customer
from app.services.auth import COOKIE_NAME, verify_session_token
from app.services.errors import UnauthorizedError
from app.settings import settings

SessionDep = Annotated[Session, Depends(get_session)]


def get_current_customer(request: Request, session: SessionDep) -> Customer:
    payload = verify_session_token(request.cookies.get(COOKIE_NAME, ""), settings.session_secret)
    if payload is None:
        raise UnauthorizedError("请先建立会话")
    try:
        customer_id = int(payload["cid"])
        merchant_id = int(payload["mid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("会话无效") from exc
    customer = session.get(Customer, customer_id)
    if customer is None or customer.merchant_id != merchant_id:
        raise UnauthorizedError("会话无效")
    return customer


CurrentCustomer = Annotated[Customer, Depends(get_current_customer)]
