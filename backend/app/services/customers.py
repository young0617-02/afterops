from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Customer
from app.services.errors import ConflictError, ValidationError

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{1,63}$")


def normalize_email(raw: str | None) -> str | None:
    if raw is None:
        return None
    email = raw.strip().lower()
    if not email:
        return None
    if len(email) > 255 or not EMAIL_RE.match(email):
        raise ValidationError("邮箱格式不正确")
    return email


def set_customer_email(
    session: Session, customer: Customer, raw: str | None
) -> str | None:
    email = normalize_email(raw)
    if email and email != customer.email:
        existing = session.execute(
            select(Customer.id).where(
                Customer.merchant_id == customer.merchant_id,
                Customer.email == email,
                Customer.id != customer.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("该邮箱已被其他账号使用")
    customer.email = email
    return email
