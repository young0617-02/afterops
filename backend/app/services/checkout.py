from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.domain.models import (
    Cart,
    CartItem,
    Customer,
    IdempotencyKey,
    Inventory,
    InventoryReservation,
    Order,
    OrderItem,
    Payment,
    Product,
)
from app.services.audit import append_event
from app.services.coupons import apply_coupon, redeem_coupon
from app.services.errors import ConflictError, ValidationError
from app.services.inventory_alerts import maybe_notify_low_stock
from app.services.inventory_ledger import record_movement
from app.services.shipping import resolve_method, shipping_fee

RESERVATION_MINUTES = 15


@dataclass(frozen=True)
class Address:
    recipient_name: str
    recipient_phone: str
    region: str
    city: str
    detail: str


@dataclass(frozen=True)
class CheckoutResult:
    order_no: str
    payment_id: int
    amount_cents: int
    status: str
    expires_at: str | None
    discount_cents: int = 0
    coupon_code: str | None = None
    freight_cents: int = 0
    shipping_method_code: str | None = None
    shipping_method_name: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


def _find_idempotent(session: Session, key: str) -> CheckoutResult | None:
    row = session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == "checkout",
            IdempotencyKey.key == key,
        )
    ).scalar_one_or_none()
    if row is None or not row.response_json:
        return None
    return CheckoutResult(**row.response_json)


def checkout(
    session: Session,
    customer: Customer,
    address: Address,
    idempotency_key: str,
    coupon_code: str | None = None,
    shipping_method_code: str | None = None,
) -> CheckoutResult:
    for attempt in range(4):
        try:
            return _checkout_once(
                session,
                customer,
                address,
                idempotency_key,
                coupon_code,
                shipping_method_code,
            )
        except OperationalError as exc:
            session.rollback()
            if "deadlock detected" not in str(exc).lower() or attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def _checkout_once(
    session: Session,
    customer: Customer,
    address: Address,
    idempotency_key: str,
    coupon_code: str | None = None,
    shipping_method_code: str | None = None,
) -> CheckoutResult:
    key = idempotency_key.strip()
    if not key:
        raise ValidationError("Idempotency-Key 必填")
    if not address.recipient_name.strip() or not address.city.strip():
        raise ValidationError("收货人姓名与城市必填")

    existing = _find_idempotent(session, key)
    if existing is not None:
        session.rollback()
        return existing

    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": customer.merchant_id},
        )

    try:
        cart = session.execute(
            select(Cart)
            .where(
                Cart.merchant_id == customer.merchant_id,
                Cart.customer_id == customer.id,
                Cart.status == "active",
            )
            .order_by(Cart.id.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if cart is None:
            existing = _find_idempotent(session, key)
            if existing is not None:
                session.rollback()
                return existing
            raise ConflictError("购物车为空")

        existing = _find_idempotent(session, key)
        if existing is not None:
            session.rollback()
            return existing

        rows = session.execute(
            select(CartItem, Product)
            .join(Product, Product.id == CartItem.product_id)
            .where(CartItem.cart_id == cart.id)
            .order_by(Product.id)
        ).all()
        if not rows:
            raise ConflictError("购物车为空")

        now = datetime.now(UTC)
        item_total = sum(item.qty * item.unit_price_cents for item, _ in rows)
        coupon = None
        discount_cents = 0
        if coupon_code and coupon_code.strip():
            coupon, discount_cents = apply_coupon(
                session, customer, coupon_code, item_total, rows
            )
        shipping_method = resolve_method(
            session, customer.merchant_id, shipping_method_code
        )
        freight_cents = shipping_fee(shipping_method, item_total)
        payable_cents = item_total + freight_cents - discount_cents
        order_no = f"ORD-{now:%Y%m%d}-{uuid4().hex[:8].upper()}"
        order = Order(
            order_no=order_no,
            merchant_id=customer.merchant_id,
            customer_id=customer.id,
            recipient_name=address.recipient_name,
            recipient_phone=address.recipient_phone,
            address_region=address.region,
            address_city=address.city,
            address_detail=address.detail,
            status="pending_payment",
            payment_status="unpaid",
            fulfillment_status="unfulfilled",
            item_total_cents=item_total,
            freight_cents=freight_cents,
            discount_cents=discount_cents,
            payable_cents=payable_cents,
            currency="CNY",
        )
        session.add(order)
        session.flush()
        if coupon is not None:
            redeem_coupon(
                session, coupon, order, customer, discount_cents
            )

        expires_at: datetime | None = None
        for item, product in rows:
            # Serialize stock allocation per product before locking inventory;
            # concurrent checkouts that pick different warehouse rows otherwise
            # can deadlock on inventory locks.
            session.execute(
                select(Product.id)
                .where(Product.id == product.id)
                .with_for_update()
            ).scalar_one()
            inventory = session.execute(
                select(Inventory)
                .where(
                    Inventory.product_id == product.id,
                    Inventory.qty_available >= item.qty,
                )
                .order_by(Inventory.qty_available.desc(), Inventory.warehouse_id)
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()
            if inventory is None:
                raise ConflictError(f"库存不足：{product.sku}")

            previous_available = inventory.qty_available
            record_movement(
                session,
                inventory,
                -item.qty,
                "checkout_reserved",
                order_id=order.id,
                note=f"结算预留 {item.qty} 件",
            )
            inventory.qty_reserved += item.qty
            maybe_notify_low_stock(
                session,
                product,
                inventory.warehouse_id,
                previous_qty=previous_available,
                current_qty=inventory.qty_available,
            )
            expires_at = now + timedelta(minutes=RESERVATION_MINUTES)
            session.add(
                InventoryReservation(
                    merchant_id=customer.merchant_id,
                    order_id=order.id,
                    product_id=product.id,
                    warehouse_id=inventory.warehouse_id,
                    qty=item.qty,
                    status="reserved",
                    expires_at=expires_at,
                )
            )
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    sku=product.sku,
                    title_snapshot=product.title,
                    qty=item.qty,
                    unit_price_cents=item.unit_price_cents,
                    warranty_months=product.warranty_months,
                )
            )

        payment = Payment(
            order_id=order.id,
            merchant_id=customer.merchant_id,
            provider="mock",
            provider_payment_id=f"MOCK-{uuid4().hex.upper()}",
            amount_cents=payable_cents,
            status="created",
            idempotency_key=f"payment:{order_no}",
        )
        session.add(payment)
        session.flush()

        cart.status = "ordered"
        result = CheckoutResult(
            order_no=order_no,
            payment_id=payment.id,
            amount_cents=payable_cents,
            status="pending_payment",
            expires_at=expires_at.isoformat() if expires_at else None,
            discount_cents=discount_cents,
            coupon_code=coupon.code if coupon else None,
            freight_cents=freight_cents,
            shipping_method_code=(
                shipping_method.code if shipping_method else None
            ),
            shipping_method_name=(
                shipping_method.name if shipping_method else None
            ),
        )
        session.add(
            IdempotencyKey(
                key=key,
                scope="checkout",
                response_json=result.to_json(),
                expires_at=now + timedelta(days=1),
            )
        )
        append_event(
            session,
            customer.merchant_id,
            "order_created",
            {
                "order_no": order_no,
                "payment_id": payment.id,
                "customer_id": customer.id,
                "amount_cents": payable_cents,
                "discount_cents": discount_cents,
                "coupon_code": coupon.code if coupon else None,
                "freight_cents": freight_cents,
                "shipping_method_code": (
                    shipping_method.code if shipping_method else None
                ),
                "item_count": len(rows),
            },
        )
        session.commit()
        return result
    except IntegrityError as exc:
        session.rollback()
        existing = _find_idempotent(session, key)
        if existing is not None:
            return existing
        raise ConflictError("结算冲突，请重试") from exc
    except Exception:
        session.rollback()
        raise
