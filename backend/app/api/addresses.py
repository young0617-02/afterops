from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import CustomerAddress, Order
from app.services import addresses as address_service
from app.services.addresses import AddressInput

router = APIRouter(prefix="/api/store", tags=["addresses"])


class AddressIn(BaseModel):
    label: str = Field(default="", max_length=40)
    recipient_name: str = Field(min_length=1, max_length=120)
    recipient_phone: str = Field(min_length=11, max_length=11)
    region: str = Field(default="", max_length=80)
    city: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=255)
    is_default: bool = False


class AddressOut(BaseModel):
    id: int
    label: str
    recipient_name: str
    recipient_phone: str
    region: str
    city: str
    detail: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class AddressLastOut(BaseModel):
    recipient_name: str
    recipient_phone: str
    region: str
    city: str
    detail: str


def _out(address: CustomerAddress) -> AddressOut:
    return AddressOut(
        id=address.id,
        label=address.label,
        recipient_name=address.recipient_name,
        recipient_phone=address.recipient_phone,
        region=address.region,
        city=address.city,
        detail=address.detail,
        is_default=address.is_default,
        created_at=address.created_at,
        updated_at=address.updated_at,
    )


def _input(payload: AddressIn) -> AddressInput:
    return AddressInput(
        recipient_name=payload.recipient_name,
        recipient_phone=payload.recipient_phone,
        region=payload.region,
        city=payload.city,
        detail=payload.detail,
        label=payload.label,
    )


@router.get("/addresses", response_model=list[AddressOut])
def list_addresses(
    customer: CurrentCustomer, session: SessionDep
) -> list[AddressOut]:
    return [
        _out(row)
        for row in address_service.list_addresses(session, customer)
    ]


@router.post("/addresses", response_model=AddressOut)
def create_address(
    payload: AddressIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> AddressOut:
    return _out(
        address_service.create_address(
            session, customer, _input(payload), is_default=payload.is_default
        )
    )


@router.patch("/addresses/{address_id}", response_model=AddressOut)
def update_address(
    address_id: int,
    payload: AddressIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> AddressOut:
    return _out(
        address_service.update_address(
            session,
            customer,
            address_id,
            _input(payload),
            is_default=payload.is_default,
        )
    )


@router.delete("/addresses/{address_id}")
def delete_address(
    address_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> dict[str, str]:
    address_service.delete_address(session, customer, address_id)
    return {"status": "deleted"}


@router.post("/addresses/{address_id}/default", response_model=AddressOut)
def set_default_address(
    address_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> AddressOut:
    return _out(address_service.set_default(session, customer, address_id))


@router.get("/addresses/last", response_model=AddressLastOut | None)
def last_address(
    customer: CurrentCustomer, session: SessionDep
) -> AddressLastOut | None:
    default = address_service.get_default_address(session, customer)
    if default is not None:
        return AddressLastOut(
            recipient_name=default.recipient_name,
            recipient_phone=default.recipient_phone,
            region=default.region,
            city=default.city,
            detail=default.detail,
        )
    order = session.execute(
        select(Order)
        .where(
            Order.merchant_id == customer.merchant_id,
            Order.customer_id == customer.id,
        )
        .order_by(Order.placed_at.desc(), Order.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if order is None:
        return None
    return AddressLastOut(
        recipient_name=order.recipient_name,
        recipient_phone=order.recipient_phone,
        region=order.address_region,
        city=order.address_city,
        detail=order.address_detail,
    )
