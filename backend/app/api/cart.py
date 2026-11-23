from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentCustomer, SessionDep
from app.domain.money import format_cents
from app.services import cart as cart_service

router = APIRouter(prefix="/api/store", tags=["cart"])


class CartItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    qty: int = Field(gt=0, le=999)


class CartItemSet(BaseModel):
    qty: int = Field(ge=0, le=999)


class CartItemOut(BaseModel):
    sku: str
    title: str
    qty: int
    unit_price_cents: int
    unit_price_display: str
    line_total_cents: int
    line_total_display: str
    available_qty: int


class CartOut(BaseModel):
    cart_id: int
    status: str
    items: list[CartItemOut]
    item_total_cents: int
    item_total_display: str
    freight_cents: int
    freight_display: str
    discount_cents: int
    discount_display: str
    payable_cents: int
    payable_display: str


def _out(view: cart_service.CartView) -> CartOut:
    return CartOut(
        cart_id=view.cart_id,
        status=view.status,
        items=[
            CartItemOut(
                sku=line.sku,
                title=line.title,
                qty=line.qty,
                unit_price_cents=line.unit_price_cents,
                unit_price_display=format_cents(line.unit_price_cents),
                line_total_cents=line.line_total_cents,
                line_total_display=format_cents(line.line_total_cents),
                available_qty=line.available_qty,
            )
            for line in view.items
        ],
        item_total_cents=view.item_total_cents,
        item_total_display=format_cents(view.item_total_cents),
        freight_cents=view.freight_cents,
        freight_display=format_cents(view.freight_cents),
        discount_cents=view.discount_cents,
        discount_display=format_cents(view.discount_cents),
        payable_cents=view.payable_cents,
        payable_display=format_cents(view.payable_cents),
    )


@router.get("/cart", response_model=CartOut)
def get_cart(customer: CurrentCustomer, session: SessionDep) -> CartOut:
    cart = cart_service.get_or_create_cart(session, customer.merchant_id, customer.id)
    return _out(cart_service.view(session, cart))


@router.post("/cart/items", response_model=CartOut)
def add_cart_item(payload: CartItemIn, customer: CurrentCustomer, session: SessionDep) -> CartOut:
    cart = cart_service.get_or_create_cart(session, customer.merchant_id, customer.id)
    return _out(cart_service.add_item(session, cart, payload.sku, payload.qty))


@router.put("/cart/items/{sku}", response_model=CartOut)
def set_cart_item(
    sku: str, payload: CartItemSet, customer: CurrentCustomer, session: SessionDep
) -> CartOut:
    cart = cart_service.get_or_create_cart(session, customer.merchant_id, customer.id)
    return _out(cart_service.set_qty(session, cart, sku, payload.qty))


@router.delete("/cart/items/{sku}", response_model=CartOut)
def remove_cart_item(sku: str, customer: CurrentCustomer, session: SessionDep) -> CartOut:
    cart = cart_service.get_or_create_cart(session, customer.merchant_id, customer.id)
    return _out(cart_service.remove_item(session, cart, sku))
