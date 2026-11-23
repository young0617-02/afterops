from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import ShippingMethod
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError

CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9-]{2,31}")


@dataclass(frozen=True)
class ShippingMethodView:
    id: int
    code: str
    name: str
    fee_cents: int
    free_over_cents: int | None
    status: str
    sort_order: int
    created_at: datetime


def _view(method: ShippingMethod) -> ShippingMethodView:
    return ShippingMethodView(
        id=method.id,
        code=method.code,
        name=method.name,
        fee_cents=method.fee_cents,
        free_over_cents=method.free_over_cents,
        status=method.status,
        sort_order=method.sort_order,
        created_at=method.created_at,
    )


def list_methods(
    session: Session, merchant_id: int, *, active_only: bool = False
) -> list[ShippingMethodView]:
    stmt = select(ShippingMethod).where(
        ShippingMethod.merchant_id == merchant_id
    )
    if active_only:
        stmt = stmt.where(ShippingMethod.status == "active")
    methods = session.execute(
        stmt.order_by(ShippingMethod.sort_order, ShippingMethod.id)
    ).scalars().all()
    return [_view(method) for method in methods]


def create_method(
    session: Session,
    merchant_id: int,
    user_id: int,
    *,
    code: str,
    name: str,
    fee_cents: int,
    free_over_cents: int | None,
    sort_order: int,
) -> ShippingMethodView:
    code_value = code.strip().upper()
    name_value = name.strip()
    if CODE_RE.fullmatch(code_value) is None:
        raise ValidationError("配送编码需为 3–32 位大写字母、数字或连字符")
    if not name_value or len(name_value) > 120:
        raise ValidationError("配送名称不能为空且不超过 120 个字符")
    if fee_cents < 0 or fee_cents > 100_000:
        raise ValidationError("运费需在 0–1000 元之间")
    if free_over_cents is not None and free_over_cents < 0:
        raise ValidationError("包邮门槛不能为负")
    try:
        existing = session.execute(
            select(ShippingMethod.id).where(
                ShippingMethod.merchant_id == merchant_id,
                ShippingMethod.code == code_value,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("配送编码已存在")
        method = ShippingMethod(
            merchant_id=merchant_id,
            code=code_value,
            name=name_value,
            fee_cents=fee_cents,
            free_over_cents=free_over_cents,
            status="active",
            sort_order=sort_order,
            created_by=user_id,
        )
        session.add(method)
        session.flush()
        append_event(
            session,
            merchant_id,
            "shipping_method_created",
            {
                "shipping_method_id": method.id,
                "code": method.code,
                "fee_cents": method.fee_cents,
                "free_over_cents": method.free_over_cents,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(method)
    except Exception:
        session.rollback()
        raise


def set_method_status(
    session: Session,
    merchant_id: int,
    user_id: int,
    method_id: int,
    status: str,
) -> ShippingMethodView:
    if status not in ("active", "disabled"):
        raise ValidationError("未知配送方式状态")
    try:
        method = session.execute(
            select(ShippingMethod)
            .where(
                ShippingMethod.id == method_id,
                ShippingMethod.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if method is None:
            raise NotFoundError("配送方式不存在")
        method.status = status
        append_event(
            session,
            merchant_id,
            "shipping_method_status_changed",
            {
                "shipping_method_id": method.id,
                "code": method.code,
                "status": status,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(method)
    except Exception:
        session.rollback()
        raise


def resolve_method(
    session: Session, merchant_id: int, code: str | None
) -> ShippingMethod | None:
    base = select(ShippingMethod).where(
        ShippingMethod.merchant_id == merchant_id,
        ShippingMethod.status == "active",
    )
    if code and code.strip():
        method = session.execute(
            base.where(
                func.upper(ShippingMethod.code) == code.strip().upper()
            )
        ).scalar_one_or_none()
        if method is None:
            raise ConflictError("配送方式不可用")
        return method
    standard = session.execute(
        base.where(func.upper(ShippingMethod.code) == "STANDARD")
    ).scalar_one_or_none()
    if standard is not None:
        return standard
    return session.execute(
        base.order_by(ShippingMethod.sort_order, ShippingMethod.id)
    ).scalars().first()


def shipping_fee(method: ShippingMethod | None, item_total: int) -> int:
    if method is None:
        return 0
    if (
        method.free_over_cents is not None
        and item_total >= method.free_over_cents
    ):
        return 0
    return method.fee_cents
