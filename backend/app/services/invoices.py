from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import Customer, InvoiceRequest, Order, Refund
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.notifications import notify_customer, notify_staff


@dataclass(frozen=True)
class InvoiceView:
    id: int
    order_no: str
    invoice_type: str
    title: str
    tax_no: str
    email: str
    amount_cents: int
    net_paid_cents: int
    adjustment_required: bool
    status: str
    invoice_no: str
    reject_reason: str
    void_reason: str
    created_at: datetime
    issued_at: datetime | None
    voided_at: datetime | None


def _refunded_total(session: Session, order_id: int) -> int:
    value = session.execute(
        select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(
            Refund.order_id == order_id,
            Refund.kind == "refund",
            Refund.status == "executed",
        )
    ).scalar_one()
    return int(value or 0)


def _view(
    invoice: InvoiceRequest,
    order: Order,
    refunded_cents: int = 0,
) -> InvoiceView:
    net_paid = max(order.payable_cents - refunded_cents, 0)
    return InvoiceView(
        id=invoice.id,
        order_no=order.order_no,
        invoice_type=invoice.invoice_type,
        title=invoice.title,
        tax_no=invoice.tax_no,
        email=invoice.email,
        amount_cents=invoice.amount_cents,
        net_paid_cents=net_paid,
        adjustment_required=(
            invoice.status in ("requested", "issued")
            and invoice.amount_cents != net_paid
        ),
        status=invoice.status,
        invoice_no=invoice.invoice_no,
        reject_reason=invoice.reject_reason,
        void_reason=invoice.void_reason,
        created_at=invoice.created_at,
        issued_at=invoice.issued_at,
        voided_at=invoice.voided_at,
    )


def create_invoice(
    session: Session,
    customer: Customer,
    order_no: str,
    invoice_type: str,
    title: str,
    tax_no: str,
    email: str,
) -> InvoiceView:
    if invoice_type not in ("personal", "company"):
        raise ValidationError("未知发票类型")
    title_value = title.strip()
    tax_value = tax_no.strip().upper()
    email_value = email.strip()
    if not title_value or len(title_value) > 120:
        raise ValidationError("发票抬头不能为空且不超过 120 个字符")
    if invoice_type == "company" and not tax_value:
        raise ValidationError("企业发票必须填写税号")
    if len(tax_value) > 32:
        raise ValidationError("税号不能超过 32 个字符")
    if len(email_value) > 255:
        raise ValidationError("接收邮箱不能超过 255 个字符")
    try:
        order = session.execute(
            select(Order)
            .where(
                Order.order_no == order_no,
                Order.merchant_id == customer.merchant_id,
                Order.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("订单不存在")
        if order.payment_status not in ("paid", "partially_refunded"):
            raise ConflictError("只有已支付订单可以申请发票")
        existing = session.execute(
            select(InvoiceRequest.id).where(
                InvoiceRequest.order_id == order.id,
                InvoiceRequest.status.in_(("requested", "issued")),
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("该订单已有进行中的发票申请")
        refunded = _refunded_total(session, order.id)
        net_paid = max(order.payable_cents - refunded, 0)
        if net_paid <= 0:
            raise ConflictError("订单已无实际支付，无需开票")
        invoice = InvoiceRequest(
            merchant_id=customer.merchant_id,
            order_id=order.id,
            customer_id=customer.id,
            invoice_type=invoice_type,
            title=title_value,
            tax_no=tax_value,
            email=email_value,
            amount_cents=net_paid,
            status="requested",
        )
        session.add(invoice)
        session.flush()
        append_event(
            session,
            customer.merchant_id,
            "invoice_requested",
            {
                "invoice_id": invoice.id,
                "order_no": order.order_no,
                "invoice_type": invoice_type,
                "amount_cents": invoice.amount_cents,
            },
        )
        notify_staff(
            session,
            customer.merchant_id,
            notification_type="invoice_requested",
            title=f"新的发票申请 #{invoice.id}",
            body=(
                f"订单 {order.order_no} · {title_value} · "
                f"{invoice.amount_cents / 100:.2f} 元"
            ),
            link="/admin",
        )
        session.commit()
        return _view(invoice, order, refunded)
    except Exception:
        session.rollback()
        raise


def _refunded_map(session: Session, order_ids: list[int]) -> dict[int, int]:
    if not order_ids:
        return {}
    rows = session.execute(
        select(Refund.order_id, func.sum(Refund.amount_cents))
        .where(
            Refund.order_id.in_(order_ids),
            Refund.kind == "refund",
            Refund.status == "executed",
        )
        .group_by(Refund.order_id)
    ).all()
    return {int(order_id): int(amount or 0) for order_id, amount in rows}


def list_invoices_for_customer(
    session: Session, customer: Customer
) -> list[InvoiceView]:
    rows = session.execute(
        select(InvoiceRequest, Order)
        .join(Order, Order.id == InvoiceRequest.order_id)
        .where(
            InvoiceRequest.merchant_id == customer.merchant_id,
            InvoiceRequest.customer_id == customer.id,
        )
        .order_by(InvoiceRequest.id.desc())
    ).all()
    refunded = _refunded_map(session, [order.id for _invoice, order in rows])
    return [
        _view(invoice, order, refunded.get(order.id, 0))
        for invoice, order in rows
    ]


def list_invoices_for_admin(
    session: Session, merchant_id: int, status: str = "all"
) -> list[InvoiceView]:
    stmt = (
        select(InvoiceRequest, Order)
        .join(Order, Order.id == InvoiceRequest.order_id)
        .where(InvoiceRequest.merchant_id == merchant_id)
    )
    if status == "needs_adjustment":
        stmt = stmt.where(
            InvoiceRequest.status.in_(("requested", "issued"))
        )
    elif status != "all":
        stmt = stmt.where(InvoiceRequest.status == status)
    rows = session.execute(
        stmt.order_by(InvoiceRequest.id.desc()).limit(200)
    ).all()
    refunded = _refunded_map(session, [order.id for _invoice, order in rows])
    views = [
        _view(invoice, order, refunded.get(order.id, 0))
        for invoice, order in rows
    ]
    if status == "needs_adjustment":
        return [view for view in views if view.adjustment_required]
    return views


def _invoice_for_update(
    session: Session, merchant_id: int, invoice_id: int
) -> tuple[InvoiceRequest, Order]:
    row = session.execute(
        select(InvoiceRequest, Order)
        .join(Order, Order.id == InvoiceRequest.order_id)
        .where(
            InvoiceRequest.id == invoice_id,
            InvoiceRequest.merchant_id == merchant_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("发票申请不存在")
    return row


def issue_invoice(
    session: Session,
    merchant_id: int,
    user_id: int,
    invoice_id: int,
    invoice_no: str,
) -> InvoiceView:
    number = invoice_no.strip()
    if not number or len(number) > 64:
        raise ValidationError("发票号码不能为空且不超过 64 个字符")
    try:
        invoice, order = _invoice_for_update(session, merchant_id, invoice_id)
        if invoice.status != "requested":
            raise ConflictError("只有待处理发票可以开票")
        invoice.status = "issued"
        invoice.invoice_no = number
        invoice.issued_by = user_id
        invoice.issued_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "invoice_issued",
            {
                "invoice_id": invoice.id,
                "order_no": order.order_no,
                "invoice_no": number,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            invoice.customer_id,
            notification_type="invoice_issued",
            title="发票已开具",
            body=f"订单 {order.order_no} · 发票号 {number}",
            link="/orders",
        )
        session.commit()
        return _view(
            invoice, order, _refunded_total(session, order.id)
        )
    except Exception:
        session.rollback()
        raise


def void_invoice(
    session: Session,
    merchant_id: int,
    user_id: int,
    invoice_id: int,
    reason: str,
) -> InvoiceView:
    reason_text = reason.strip()
    if not reason_text:
        raise ValidationError("作废原因不能为空")
    if len(reason_text) > 500:
        raise ValidationError("作废原因不能超过 500 个字符")
    try:
        invoice, order = _invoice_for_update(session, merchant_id, invoice_id)
        if invoice.status != "issued":
            raise ConflictError("只有已开具发票可以作废")
        invoice.status = "voided"
        invoice.void_reason = reason_text
        invoice.voided_by = user_id
        invoice.voided_at = datetime.now(UTC)
        append_event(
            session,
            merchant_id,
            "invoice_voided",
            {
                "invoice_id": invoice.id,
                "order_no": order.order_no,
                "invoice_no": invoice.invoice_no,
                "reason": reason_text,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            invoice.customer_id,
            notification_type="invoice_voided",
            title="发票已作废",
            body=(
                f"订单 {order.order_no} · 发票号 {invoice.invoice_no} · "
                f"{reason_text}"
            ),
            link="/orders",
        )
        session.commit()
        return _view(
            invoice, order, _refunded_total(session, order.id)
        )
    except Exception:
        session.rollback()
        raise


def reject_invoice(
    session: Session,
    merchant_id: int,
    user_id: int,
    invoice_id: int,
    reason: str,
) -> InvoiceView:
    reason_text = reason.strip()
    try:
        invoice, order = _invoice_for_update(session, merchant_id, invoice_id)
        if invoice.status != "requested":
            raise ConflictError("只有待处理发票可以驳回")
        invoice.status = "rejected"
        invoice.reject_reason = reason_text
        append_event(
            session,
            merchant_id,
            "invoice_rejected",
            {
                "invoice_id": invoice.id,
                "order_no": order.order_no,
                "reason": reason_text,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            invoice.customer_id,
            notification_type="invoice_rejected",
            title="发票申请未通过",
            body=f"订单 {order.order_no} · {reason_text or '未说明原因'}",
            link="/orders",
        )
        session.commit()
        return _view(
            invoice, order, _refunded_total(session, order.id)
        )
    except Exception:
        session.rollback()
        raise
