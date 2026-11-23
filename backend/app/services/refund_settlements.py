from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import ActionProposal, Order, Refund
from app.domain.money import format_cents
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.notifications import notify_customer


@dataclass(frozen=True)
class RefundSettlementView:
    id: int
    order_no: str
    proposal_id: int
    kind: str
    amount_cents: int
    amount_display: str
    reason: str
    status: str
    provider_refund_id: str
    settlement_status: str
    settlement_reference: str
    settlement_failure_reason: str
    settlement_attempts: int
    created_at: datetime
    executed_at: datetime | None
    settled_at: datetime | None


def _reason(session: Session, proposal_id: int) -> str:
    proposal = session.get(ActionProposal, proposal_id)
    if proposal is None:
        return ""
    return str((proposal.payload_json or {}).get("reason") or "")


def _view(session: Session, refund: Refund, order: Order) -> RefundSettlementView:
    return RefundSettlementView(
        id=refund.id,
        order_no=order.order_no,
        proposal_id=refund.proposal_id,
        kind=refund.kind,
        amount_cents=refund.amount_cents,
        amount_display=format_cents(refund.amount_cents),
        reason=_reason(session, refund.proposal_id),
        status=refund.status,
        provider_refund_id=refund.provider_refund_id,
        settlement_status=refund.settlement_status,
        settlement_reference=refund.settlement_reference,
        settlement_failure_reason=refund.settlement_failure_reason,
        settlement_attempts=refund.settlement_attempts,
        created_at=refund.created_at,
        executed_at=refund.executed_at,
        settled_at=refund.settled_at,
    )


def list_refunds_for_admin(
    session: Session,
    merchant_id: int,
    *,
    status: str = "all",
    limit: int = 100,
) -> list[RefundSettlementView]:
    stmt = (
        select(Refund, Order)
        .join(Order, Order.id == Refund.order_id)
        .where(Refund.merchant_id == merchant_id)
    )
    if status != "all":
        stmt = stmt.where(Refund.settlement_status == status)
    rows = session.execute(
        stmt.order_by(Refund.id.desc()).limit(limit)
    ).all()
    return [_view(session, refund, order) for refund, order in rows]


def _refund_for_update(
    session: Session, merchant_id: int, refund_id: int
) -> tuple[Refund, Order]:
    row = session.execute(
        select(Refund, Order)
        .join(Order, Order.id == Refund.order_id)
        .where(
            Refund.id == refund_id,
            Refund.merchant_id == merchant_id,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("退款记录不存在")
    return row


def _mark_settlement_succeeded(
    session: Session,
    merchant_id: int,
    refund: Refund,
    order: Order,
    *,
    reference: str,
    event_type: str,
    operator_user_id: int | None = None,
    auto_minutes: int | None = None,
) -> None:
    now = datetime.now(UTC)
    refund.settlement_status = "succeeded"
    refund.settlement_reference = reference or f"PSP-{uuid4().hex[:12].upper()}"
    refund.settlement_failure_reason = ""
    refund.settled_at = now
    refund.settlement_failed_at = None
    payload: dict[str, object] = {
        "refund_id": refund.id,
        "order_no": order.order_no,
        "amount_cents": refund.amount_cents,
        "reference": refund.settlement_reference,
        "operator_user_id": operator_user_id,
    }
    if auto_minutes is not None:
        payload["auto_minutes"] = auto_minutes
    append_event(session, merchant_id, event_type, payload)
    notify_customer(
        session,
        merchant_id,
        order.customer_id,
        notification_type="refund_settled",
        title="退款已到账",
        body=(
            f"订单 {order.order_no} · "
            f"{format_cents(refund.amount_cents)} 元 · "
            f"渠道引用 {refund.settlement_reference}"
        ),
        link="/orders",
    )


def settle_refund(
    session: Session,
    merchant_id: int,
    user_id: int,
    refund_id: int,
    *,
    succeeded: bool,
    reference: str = "",
    failure_reason: str = "",
) -> RefundSettlementView:
    reference_value = reference.strip()
    failure_value = failure_reason.strip()
    if len(reference_value) > 128:
        raise ValidationError("渠道引用不能超过 128 个字符")
    if len(failure_value) > 1000:
        raise ValidationError("失败原因不能超过 1000 个字符")
    if not succeeded and not failure_value:
        raise ValidationError("渠道失败需填写原因")
    try:
        refund, order = _refund_for_update(session, merchant_id, refund_id)
        if refund.status != "executed":
            raise ConflictError("当前退款状态不能结算")
        if refund.settlement_status != "processing":
            raise ConflictError("当前退款结算状态不能处理")
        if succeeded:
            _mark_settlement_succeeded(
                session,
                merchant_id,
                refund,
                order,
                reference=reference_value,
                event_type="refund_settlement_succeeded",
                operator_user_id=user_id,
            )
        else:
            refund.settlement_status = "failed"
            refund.settlement_reference = ""
            refund.settlement_failure_reason = failure_value
            refund.settled_at = None
            refund.settlement_failed_at = datetime.now(UTC)
            append_event(
                session,
                merchant_id,
                "refund_settlement_failed",
                {
                    "refund_id": refund.id,
                    "order_no": order.order_no,
                    "amount_cents": refund.amount_cents,
                    "failure_reason": failure_value,
                    "operator_user_id": user_id,
                },
            )
            notify_customer(
                session,
                merchant_id,
                order.customer_id,
                notification_type="refund_settlement_failed",
                title="退款渠道处理失败",
                body=(
                    f"订单 {order.order_no} · "
                    f"{format_cents(refund.amount_cents)} 元 · "
                    f"{failure_value} · 商家将重新提交"
                ),
                link="/orders",
            )
        session.commit()
        return _view(session, refund, order)
    except Exception:
        session.rollback()
        raise


def retry_refund_settlement(
    session: Session, merchant_id: int, user_id: int, refund_id: int
) -> RefundSettlementView:
    try:
        refund, order = _refund_for_update(session, merchant_id, refund_id)
        if refund.settlement_status != "failed":
            raise ConflictError("只有渠道失败的退款可以重试")
        refund.settlement_status = "processing"
        refund.settlement_attempts += 1
        refund.settlement_failure_reason = ""
        refund.settlement_reference = ""
        refund.settled_at = None
        refund.settlement_failed_at = None
        append_event(
            session,
            merchant_id,
            "refund_settlement_retried",
            {
                "refund_id": refund.id,
                "order_no": order.order_no,
                "amount_cents": refund.amount_cents,
                "attempt": refund.settlement_attempts,
                "operator_user_id": user_id,
            },
        )
        notify_customer(
            session,
            merchant_id,
            order.customer_id,
            notification_type="refund_settlement_retried",
            title="退款已重新提交渠道",
            body=(
                f"订单 {order.order_no} · "
                f"{format_cents(refund.amount_cents)} 元 · "
                "等待渠道处理"
            ),
            link="/orders",
        )
        session.commit()
        return _view(session, refund, order)
    except Exception:
        session.rollback()
        raise


def auto_settle_processing_refunds(
    session: Session,
    *,
    now: datetime | None = None,
    minutes: int = 5,
    limit: int = 50,
) -> int:
    if minutes <= 0:
        return 0
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(minutes=minutes)
    rows = session.execute(
        select(Refund.id, Refund.merchant_id)
        .where(
            Refund.status == "executed",
            Refund.settlement_status == "processing",
            Refund.executed_at.is_not(None),
            Refund.executed_at <= cutoff,
        )
        .order_by(Refund.id)
        .limit(limit)
    ).all()
    settled = 0
    for refund_id, merchant_id in rows:
        try:
            refund, order = _refund_for_update(
                session, merchant_id, refund_id
            )
            if refund.settlement_status != "processing":
                raise ConflictError("当前退款结算状态不能处理")
            _mark_settlement_succeeded(
                session,
                merchant_id,
                refund,
                order,
                reference=f"PSP-AUTO-{uuid4().hex[:12].upper()}",
                event_type="refund_settlement_auto",
                auto_minutes=minutes,
            )
            session.commit()
            settled += 1
        except (ConflictError, NotFoundError):
            session.rollback()
    return settled


def auto_retry_failed_refunds(
    session: Session,
    *,
    now: datetime | None = None,
    minutes: int = 15,
    max_attempts: int = 3,
    limit: int = 50,
) -> int:
    if minutes <= 0 or max_attempts <= 0:
        return 0
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(minutes=minutes)
    rows = session.execute(
        select(Refund.id, Refund.merchant_id)
        .where(
            Refund.status == "executed",
            Refund.settlement_status == "failed",
            Refund.settlement_attempts < max_attempts,
            Refund.settlement_failed_at.is_not(None),
            Refund.settlement_failed_at <= cutoff,
        )
        .order_by(Refund.id)
        .limit(limit)
    ).all()
    retried = 0
    for refund_id, merchant_id in rows:
        try:
            refund, order = _refund_for_update(
                session, merchant_id, refund_id
            )
            if refund.settlement_status != "failed":
                raise ConflictError("当前退款结算状态不能重试")
            if refund.settlement_attempts >= max_attempts:
                raise ConflictError("退款自动重试次数已达上限")
            refund.settlement_status = "processing"
            refund.settlement_attempts += 1
            refund.settlement_failure_reason = ""
            refund.settlement_reference = ""
            refund.settled_at = None
            refund.settlement_failed_at = None
            append_event(
                session,
                merchant_id,
                "refund_settlement_auto_retried",
                {
                    "refund_id": refund.id,
                    "order_no": order.order_no,
                    "amount_cents": refund.amount_cents,
                    "attempt": refund.settlement_attempts,
                    "max_attempts": max_attempts,
                    "auto_minutes": minutes,
                    "operator_user_id": None,
                },
            )
            notify_customer(
                session,
                merchant_id,
                order.customer_id,
                notification_type="refund_settlement_retried",
                title="退款已重新提交渠道",
                body=(
                    f"订单 {order.order_no} · "
                    f"{format_cents(refund.amount_cents)} 元 · "
                    "系统自动重试，等待渠道处理"
                ),
                link="/orders",
            )
            session.commit()
            retried += 1
        except (ConflictError, NotFoundError):
            session.rollback()
    return retried
