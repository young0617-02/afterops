from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import Cart, CartItem, Inventory, Product
from app.services.errors import ConflictError, NotFoundError


@dataclass
class CartLine:
    sku: str
    title: str
    qty: int
    unit_price_cents: int
    available_qty: int

    @property
    def line_total_cents(self) -> int:
        return self.qty * self.unit_price_cents


@dataclass
class CartView:
    cart_id: int
    status: str
    items: list[CartLine]
    item_total_cents: int
    freight_cents: int = 0
    discount_cents: int = 0

    @property
    def payable_cents(self) -> int:
        return self.item_total_cents + self.freight_cents - self.discount_cents


def get_or_create_cart(session: Session, merchant_id: int, customer_id: int) -> Cart:
    cart = session.execute(
        select(Cart)
        .where(
            Cart.merchant_id == merchant_id,
            Cart.customer_id == customer_id,
            Cart.status == "active",
        )
        .order_by(Cart.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if cart is None:
        cart = Cart(merchant_id=merchant_id, customer_id=customer_id, status="active")
        session.add(cart)
        session.commit()
        session.refresh(cart)
    return cart


def _active_product(session: Session, merchant_id: int, sku: str) -> Product:
    product = session.execute(
        select(Product).where(
            Product.merchant_id == merchant_id,
            Product.sku == sku,
            Product.status == "active",
        )
    ).scalar_one_or_none()
    if product is None:
        raise NotFoundError("商品不存在")
    return product


def _available_qty(session: Session, product_id: int) -> int:
    value = session.execute(
        select(func.coalesce(func.sum(Inventory.qty_available), 0)).where(
            Inventory.product_id == product_id
        )
    ).scalar()
    return int(value or 0)


def _assert_stock(session: Session, product: Product, qty: int) -> int:
    available = _available_qty(session, product.id)
    if qty > available:
        raise ConflictError(f"库存不足：{product.sku} 当前可售 {available} 件")
    return available


def add_item(session: Session, cart: Cart, sku: str, qty: int) -> CartView:
    product = _active_product(session, cart.merchant_id, sku)
    item = session.execute(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product.id)
    ).scalar_one_or_none()
    new_qty = (item.qty if item else 0) + qty
    _assert_stock(session, product, new_qty)
    if item is None:
        item = CartItem(
            cart_id=cart.id,
            product_id=product.id,
            qty=new_qty,
            unit_price_cents=product.price_cents,
        )
        session.add(item)
    else:
        item.qty = new_qty
        item.unit_price_cents = product.price_cents
    session.commit()
    return view(session, cart)


def set_qty(session: Session, cart: Cart, sku: str, qty: int) -> CartView:
    product = _active_product(session, cart.merchant_id, sku)
    item = session.execute(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product.id)
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("购物车中没有该商品")
    if qty == 0:
        session.delete(item)
    else:
        _assert_stock(session, product, qty)
        item.qty = qty
        item.unit_price_cents = product.price_cents
    session.commit()
    return view(session, cart)


def remove_item(session: Session, cart: Cart, sku: str) -> CartView:
    product = _active_product(session, cart.merchant_id, sku)
    item = session.execute(
        select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product.id)
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("购物车中没有该商品")
    session.delete(item)
    session.commit()
    return view(session, cart)


def view(session: Session, cart: Cart) -> CartView:
    available = func.coalesce(func.sum(Inventory.qty_available), 0).label("available_qty")
    rows = session.execute(
        select(CartItem, Product, available)
        .join(Product, Product.id == CartItem.product_id)
        .outerjoin(Inventory, Inventory.product_id == Product.id)
        .where(CartItem.cart_id == cart.id)
        .group_by(CartItem.id, Product.id)
        .order_by(Product.id)
    ).all()
    lines = [
        CartLine(
            sku=product.sku,
            title=product.title,
            qty=item.qty,
            unit_price_cents=item.unit_price_cents,
            available_qty=int(qty or 0),
        )
        for item, product, qty in rows
    ]
    return CartView(
        cart_id=cart.id,
        status=cart.status,
        items=lines,
        item_total_cents=sum(line.line_total_cents for line in lines),
    )
