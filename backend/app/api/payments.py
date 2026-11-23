from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import Order, Payment
from app.services.errors import NotFoundError
from app.services.payments import mark_payment_paid
from app.settings import settings

router = APIRouter(prefix="/api/store", tags=["payments"])


@router.post("/payments/{payment_id}/mock-confirm")
def mock_confirm(payment_id: int, customer: CurrentCustomer, session: SessionDep) -> dict:
    if settings.app_env == "prod":
        raise NotFoundError("mock 支付仅在开发环境可用")

    payment = session.execute(
        select(Payment).where(Payment.id == payment_id)
    ).scalar_one_or_none()
    if payment is None:
        raise NotFoundError("支付单不存在")
    order = session.execute(
        select(Order).where(Order.id == payment.order_id)
    ).scalar_one()
    if order.customer_id != customer.id or order.merchant_id != customer.merchant_id:
        raise NotFoundError("支付单不存在")

    result = mark_payment_paid(session, payment_id, event_id=f"mock-confirm:{payment_id}")
    return {
        "payment_id": result.payment_id,
        "order_no": result.order_no,
        "status": result.status,
        "amount_cents": result.amount_cents,
    }
