from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.domain.models import (
    Inventory,
    InventoryReservation,
    Order,
    Payment,
    WebhookEvent,
)
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError


@dataclass(frozen=True)
class PaymentResult:
    payment_id: int
    order_no: str
    status: str
    amount_cents: int


def mark_payment_paid(session: Session, payment_id: int, event_id: str) -> PaymentResult:
    for attempt in range(4):
        try:
            return _mark_payment_paid_once(session, payment_id, event_id)
        except OperationalError as exc:
            session.rollback()
            if "deadlock detected" not in str(exc).lower() or attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def _mark_payment_paid_once(
    session: Session, payment_id: int, event_id: str
) -> PaymentResult:
    try:
        payment_meta = session.execute(
            select(Payment.id, Payment.order_id, Payment.merchant_id).where(
                Payment.id == payment_id
            )
        ).one_or_none()
        if payment_meta is None:
            raise NotFoundError("支付单不存在")

        # Serialize payment with checkout for the same merchant; stock,
        # payment and audit locks otherwise interleave under load.
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": payment_meta.merchant_id},
            )

        # Lock order before payment, matching cancel/expire, so concurrent
        # payment and cancellation cannot deadlock each other.
        order = session.execute(
            select(Order).where(Order.id == payment_meta.order_id).with_for_update()
        ).scalar_one()
        payment = session.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        ).scalar_one()

        if payment.status == "paid":
            result = PaymentResult(
                payment_id=payment.id,
                order_no=order.order_no,
                status="paid",
                amount_cents=payment.amount_cents,
            )
            session.rollback()
            return result
        if payment.status not in ("created", "pending"):
            raise ConflictError(f"支付单状态 {payment.status} 不能确认支付")
        if order.status in ("cancelled", "refunded"):
            raise ConflictError(f"订单状态 {order.status} 不能支付")

        event = session.execute(
            select(WebhookEvent).where(
                WebhookEvent.provider == "mock",
                WebhookEvent.event_id == event_id,
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if event is None:
            session.add(
                WebhookEvent(
                    merchant_id=payment.merchant_id,
                    provider="mock",
                    event_id=event_id,
                    event_type="payment.paid",
                    payload_json={
                        "payment_id": payment.id,
                        "order_no": order.order_no,
                        "status": "paid",
                    },
                    status="processed",
                    processed_at=now,
                )
            )

        payment.status = "paid"
        payment.paid_at = now
        order.status = "paid"
        order.payment_status = "paid"

        reservations = session.execute(
            select(InventoryReservation)
            .where(
                InventoryReservation.order_id == order.id,
                InventoryReservation.status == "reserved",
            )
            .order_by(InventoryReservation.product_id)
            .with_for_update()
        ).scalars().all()
        for reservation in reservations:
            inventory = session.execute(
                select(Inventory)
                .where(
                    Inventory.product_id == reservation.product_id,
                    Inventory.warehouse_id == reservation.warehouse_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if inventory is None:
                raise ConflictError("库存预留对应的库存记录不存在")
            inventory.qty_reserved = max(0, inventory.qty_reserved - reservation.qty)
            reservation.status = "committed"
            reservation.expires_at = None

        append_event(
            session,
            payment.merchant_id,
            "payment_paid",
            {
                "order_no": order.order_no,
                "payment_id": payment.id,
                "amount_cents": payment.amount_cents,
                "webhook_event_id": event_id,
            },
        )
        session.commit()
        return PaymentResult(
            payment_id=payment.id,
            order_no=order.order_no,
            status="paid",
            amount_cents=payment.amount_cents,
        )
    except Exception:
        session.rollback()
        raise
