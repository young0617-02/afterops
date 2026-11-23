from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Customer, CustomerAddress
from app.services.errors import ConflictError, NotFoundError, ValidationError

MAX_ADDRESSES = 10
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


@dataclass(frozen=True)
class AddressInput:
    recipient_name: str
    recipient_phone: str
    region: str
    city: str
    detail: str
    label: str = ""


def _clean(payload: AddressInput) -> AddressInput:
    cleaned = AddressInput(
        recipient_name=payload.recipient_name.strip(),
        recipient_phone=payload.recipient_phone.strip(),
        region=payload.region.strip(),
        city=payload.city.strip(),
        detail=payload.detail.strip(),
        label=payload.label.strip(),
    )
    if not cleaned.recipient_name or not cleaned.city or not cleaned.detail:
        raise ValidationError("收货人、城市与详细地址必填")
    if not PHONE_RE.match(cleaned.recipient_phone):
        raise ValidationError("收货手机号格式不正确")
    return cleaned


def _lock_customer(session: Session, customer: Customer) -> None:
    session.execute(
        select(Customer.id).where(Customer.id == customer.id).with_for_update()
    )


def _clear_default(session: Session, customer: Customer) -> None:
    rows = session.execute(
        select(CustomerAddress).where(
            CustomerAddress.customer_id == customer.id,
            CustomerAddress.is_default.is_(True),
        )
    ).scalars().all()
    for row in rows:
        row.is_default = False


def list_addresses(session: Session, customer: Customer) -> list[CustomerAddress]:
    return list(
        session.execute(
            select(CustomerAddress)
            .where(CustomerAddress.customer_id == customer.id)
            .order_by(
                CustomerAddress.is_default.desc(),
                CustomerAddress.created_at.desc(),
                CustomerAddress.id.desc(),
            )
        ).scalars().all()
    )


def create_address(
    session: Session,
    customer: Customer,
    payload: AddressInput,
    *,
    is_default: bool = False,
) -> CustomerAddress:
    cleaned = _clean(payload)
    try:
        _lock_customer(session, customer)
        existing = session.execute(
            select(CustomerAddress).where(
                CustomerAddress.customer_id == customer.id,
                CustomerAddress.recipient_name == cleaned.recipient_name,
                CustomerAddress.recipient_phone == cleaned.recipient_phone,
                CustomerAddress.region == cleaned.region,
                CustomerAddress.city == cleaned.city,
                CustomerAddress.detail == cleaned.detail,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if is_default and not existing.is_default:
                _clear_default(session, customer)
                existing.is_default = True
                existing.label = cleaned.label or existing.label
            session.commit()
            session.refresh(existing)
            return existing

        count = session.execute(
            select(CustomerAddress.id).where(
                CustomerAddress.customer_id == customer.id
            )
        ).scalars().all()
        if len(count) >= MAX_ADDRESSES:
            raise ConflictError(f"地址簿最多保存 {MAX_ADDRESSES} 个地址")

        has_default = session.execute(
            select(CustomerAddress.id).where(
                CustomerAddress.customer_id == customer.id,
                CustomerAddress.is_default.is_(True),
            )
        ).scalar_one_or_none()
        make_default = is_default or has_default is None
        if make_default:
            _clear_default(session, customer)
        address = CustomerAddress(
            merchant_id=customer.merchant_id,
            customer_id=customer.id,
            label=cleaned.label,
            recipient_name=cleaned.recipient_name,
            recipient_phone=cleaned.recipient_phone,
            region=cleaned.region,
            city=cleaned.city,
            detail=cleaned.detail,
            is_default=make_default,
        )
        session.add(address)
        session.commit()
        session.refresh(address)
        return address
    except Exception:
        session.rollback()
        raise


def update_address(
    session: Session,
    customer: Customer,
    address_id: int,
    payload: AddressInput,
    *,
    is_default: bool = False,
) -> CustomerAddress:
    cleaned = _clean(payload)
    try:
        _lock_customer(session, customer)
        address = session.execute(
            select(CustomerAddress)
            .where(
                CustomerAddress.id == address_id,
                CustomerAddress.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if address is None:
            raise NotFoundError("地址不存在")
        address.label = cleaned.label
        address.recipient_name = cleaned.recipient_name
        address.recipient_phone = cleaned.recipient_phone
        address.region = cleaned.region
        address.city = cleaned.city
        address.detail = cleaned.detail
        if is_default and not address.is_default:
            _clear_default(session, customer)
            address.is_default = True
        session.commit()
        session.refresh(address)
        return address
    except Exception:
        session.rollback()
        raise


def delete_address(session: Session, customer: Customer, address_id: int) -> None:
    try:
        _lock_customer(session, customer)
        address = session.execute(
            select(CustomerAddress)
            .where(
                CustomerAddress.id == address_id,
                CustomerAddress.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if address is None:
            raise NotFoundError("地址不存在")
        was_default = address.is_default
        session.delete(address)
        session.flush()
        if was_default:
            replacement = session.execute(
                select(CustomerAddress)
                .where(CustomerAddress.customer_id == customer.id)
                .order_by(
                    CustomerAddress.updated_at.desc(),
                    CustomerAddress.id.desc(),
                )
                .limit(1)
            ).scalar_one_or_none()
            if replacement is not None:
                replacement.is_default = True
        session.commit()
    except Exception:
        session.rollback()
        raise


def set_default(session: Session, customer: Customer, address_id: int) -> CustomerAddress:
    try:
        _lock_customer(session, customer)
        address = session.execute(
            select(CustomerAddress)
            .where(
                CustomerAddress.id == address_id,
                CustomerAddress.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if address is None:
            raise NotFoundError("地址不存在")
        _clear_default(session, customer)
        address.is_default = True
        session.commit()
        session.refresh(address)
        return address
    except Exception:
        session.rollback()
        raise


def get_default_address(
    session: Session, customer: Customer
) -> CustomerAddress | None:
    return session.execute(
        select(CustomerAddress)
        .where(
            CustomerAddress.customer_id == customer.id,
            CustomerAddress.is_default.is_(True),
        )
        .limit(1)
    ).scalar_one_or_none()
