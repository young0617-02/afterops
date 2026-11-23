from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    Customer,
    Inventory,
    InventoryReservation,
    Order,
    Payment,
    ReturnRequest,
    Shipment,
)
from app.services.audit import append_event
from app.services.coupons import release_coupon
from app.services.errors import ConflictError, NotFoundError
from app.services.inventory_ledger import record_movement
from app.services.notifications import notify_customer


@dataclass(frozen=True)
class CancelResult:
    order_no: str
    status: str


@dataclass(frozen=True)
class ConfirmReceiptResult:
    order_no: str
    status: str


def _release_reservations(session: Session, order: Order, new_status: str) -> None:
    release_coupon(session, order)
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
        if inventory is not None:
            reason = (
                "order_expired"
                if new_status == "expired"
                else "order_cancelled"
            )
            record_movement(
                session,
                inventory,
                reservation.qty,
                reason,
                order_id=order.id,
                note=f"释放预留（{new_status}）",
            )
            inventory.qty_reserved = max(0, inventory.qty_reserved - reservation.qty)
        reservation.status = new_status
        reservation.expires_at = None


def _fail_payment(session: Session, order: Order) -> None:
    payment = session.execute(
        select(Payment).where(Payment.order_id == order.id).order_by(Payment.id.desc()).limit(1).with_for_update()
    ).scalar_one_or_none()
    if payment is not None and payment.status in ("created", "pending"):
        payment.status = "failed"


def cancel_unpaid_order(session: Session, customer: Customer, order_no: str) -> CancelResult:
    try:
        order = session.execute(
            select(Order)
            .where(
                Order.order_no == order_no,
                Order.customer_id == customer.id,
                Order.merchant_id == customer.merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("订单不存在")
        if order.status != "pending_payment" or order.payment_status == "paid":
            raise ConflictError("只有待支付订单可以取消")

        _release_reservations(session, order, "released")
        _fail_payment(session, order)
        order.status = "cancelled"
        append_event(
            session,
            order.merchant_id,
            "order_cancelled",
            {"order_no": order.order_no, "reason": "customer_cancelled"},
        )
        session.commit()
        return CancelResult(order_no=order.order_no, status=order.status)
    except Exception:
        session.rollback()
        raise


def expire_order(session: Session, order_id: int) -> bool:
    try:
        order = session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        ).scalar_one_or_none()
        if order is None or order.status != "pending_payment" or order.payment_status == "paid":
            session.rollback()
            return False
        _release_reservations(session, order, "expired")
        _fail_payment(session, order)
        order.status = "cancelled"
        append_event(
            session,
            order.merchant_id,
            "order_expired",
            {"order_no": order.order_no, "reason": "reservation_expired"},
        )
        notify_customer(
            session,
            order.merchant_id,
            order.customer_id,
            notification_type="order_expired",
            title="订单已超时关闭",
            body=f"订单 {order.order_no} 未在有效期内完成支付，库存已释放。",
            link="/orders",
        )
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise


def expire_overdue_reservations(
    session: Session, now: datetime | None = None
) -> int:
    now = now or datetime.now(UTC)
    order_ids = session.execute(
        select(InventoryReservation.order_id)
        .where(
            InventoryReservation.status == "reserved",
            InventoryReservation.expires_at.is_not(None),
            InventoryReservation.expires_at < now,
        )
        .distinct()
        .order_by(InventoryReservation.order_id)
    ).scalars().all()
    expired = 0
    for order_id in order_ids:
        if expire_order(session, int(order_id)):
            expired += 1
    return expired


def confirm_receipt(
    session: Session, customer: Customer, order_no: str
) -> ConfirmReceiptResult:
    try:
        order = session.execute(
            select(Order)
            .where(
                Order.order_no == order_no,
                Order.customer_id == customer.id,
                Order.merchant_id == customer.merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("订单不存在")
        if order.status != "delivered":
            raise ConflictError("只有已签收订单可以确认收货")
        order.status = "completed"
        append_event(
            session,
            order.merchant_id,
            "order_completed",
            {"order_no": order.order_no},
        )
        session.commit()
        return ConfirmReceiptResult(order_no=order.order_no, status=order.status)
    except Exception:
        session.rollback()
        raise


def auto_complete_delivered_orders(
    session: Session, *, days: int, limit: int = 100
) -> int:
    """Complete old delivered orders unless a return or approval is still active."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    last_delivery = (
        select(
            Shipment.order_id.label("order_id"),
            func.max(Shipment.delivered_at).label("last_delivered_at"),
        )
        .group_by(Shipment.order_id)
        .subquery()
    )
    active_return = exists().where(
        ReturnRequest.order_id == Order.id,
        ReturnRequest.status.in_(("requested", "approved", "in_transit")),
    )
    pending_proposal = exists().where(
        ActionProposal.after_sales_case_id == AfterSalesCase.id,
        AfterSalesCase.order_id == Order.id,
        ActionProposal.status == "pending",
    )
    rows = session.execute(
        select(Order)
        .join(
            last_delivery,
            last_delivery.c.order_id == Order.id,
        )
        .where(
            Order.status == "delivered",
            last_delivery.c.last_delivered_at.is_not(None),
            last_delivery.c.last_delivered_at <= cutoff,
            ~active_return,
            ~pending_proposal,
        )
        .order_by(last_delivery.c.last_delivered_at)
        .limit(limit)
        .with_for_update(of=Order)
    ).scalars().all()
    for order in rows:
        order.status = "completed"
        append_event(
            session,
            order.merchant_id,
            "order_auto_completed",
            {"order_no": order.order_no, "days": days},
        )
        notify_customer(
            session,
            order.merchant_id,
            order.customer_id,
            notification_type="order_auto_completed",
            title="订单已自动完成",
            body=f"订单 {order.order_no} 已超过 {days} 天，自动完成。",
            link="/orders",
        )
    if rows:
        session.commit()
    return len(rows)
