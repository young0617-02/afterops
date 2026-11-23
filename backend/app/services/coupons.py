from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import (
    CartItem,
    Coupon,
    CouponClaim,
    CouponRedemption,
    Customer,
    Order,
    Product,
)
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError

CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9-]{2,31}")
MAX_FIXED_CENTS = 100_000_000


@dataclass(frozen=True)
class CouponView:
    id: int
    code: str
    discount_type: str
    value: int
    min_spend_cents: int
    starts_on: date
    ends_on: date | None
    max_uses: int | None
    used_count: int
    per_customer_limit: int
    scope_type: str
    scope_values: tuple[str, ...]
    claimable: bool
    claim_count: int
    status: str
    created_at: datetime


@dataclass(frozen=True)
class ClaimableCouponView:
    id: int
    discount_type: str
    value: int
    min_spend_cents: int
    starts_on: date
    ends_on: date | None
    scope_type: str
    scope_values: tuple[str, ...]
    claimed: bool
    sold_out: bool


@dataclass(frozen=True)
class WalletCouponView:
    id: int
    code: str
    discount_type: str
    value: int
    min_spend_cents: int
    starts_on: date
    ends_on: date | None
    scope_type: str
    scope_values: tuple[str, ...]
    status: str
    claimed_at: datetime
    times_used: int
    remaining_uses: int
    state: str


def _view(coupon: Coupon, claim_count: int = 0) -> CouponView:
    return CouponView(
        id=coupon.id,
        code=coupon.code,
        discount_type=coupon.discount_type,
        value=coupon.value,
        min_spend_cents=coupon.min_spend_cents,
        starts_on=coupon.starts_on,
        ends_on=coupon.ends_on,
        max_uses=coupon.max_uses,
        used_count=coupon.used_count,
        per_customer_limit=coupon.per_customer_limit,
        scope_type=coupon.scope_type,
        scope_values=tuple(coupon.scope_values or ()),
        claimable=coupon.claimable,
        claim_count=claim_count,
        status=coupon.status,
        created_at=coupon.created_at,
    )


def normalize_scope(
    scope_type: str, scope_values: list[str] | None
) -> tuple[str, ...]:
    values = [value.strip() for value in (scope_values or []) if value.strip()]
    if scope_type == "all":
        return ()
    if scope_type == "category":
        unique = list(dict.fromkeys(values))
        if not unique:
            raise ValidationError("指定类目优惠券至少需要一个类目")
        if len(unique) > 20:
            raise ValidationError("类目范围不能超过 20 个")
        if any(len(value) > 80 for value in unique):
            raise ValidationError("类目名称不能超过 80 个字符")
        return tuple(unique)
    if scope_type == "sku":
        unique = list(dict.fromkeys(value.upper() for value in values))
        if not unique:
            raise ValidationError("指定 SKU 优惠券至少需要一个 SKU")
        if len(unique) > 50:
            raise ValidationError("SKU 范围不能超过 50 个")
        if any(len(value) > 64 for value in unique):
            raise ValidationError("SKU 不能超过 64 个字符")
        return tuple(unique)
    raise ValidationError("未知优惠券适用范围")


def create_coupon(
    session: Session,
    merchant_id: int,
    user_id: int,
    *,
    code: str,
    discount_type: str,
    value: int,
    min_spend_cents: int,
    starts_on: date,
    ends_on: date | None,
    max_uses: int | None,
    per_customer_limit: int,
    scope_type: str = "all",
    scope_values: list[str] | None = None,
    claimable: bool = False,
) -> CouponView:
    code_value = code.strip().upper()
    if CODE_RE.fullmatch(code_value) is None:
        raise ValidationError("优惠码需为 3–32 位大写字母、数字或连字符")
    if discount_type not in ("fixed", "percent"):
        raise ValidationError("未知优惠类型")
    if discount_type == "percent" and not (1 <= value <= 100):
        raise ValidationError("百分比优惠需在 1–100 之间")
    if discount_type == "fixed" and not (1 <= value <= MAX_FIXED_CENTS):
        raise ValidationError("固定优惠金额不合法")
    if min_spend_cents < 0:
        raise ValidationError("最低消费不能为负")
    if ends_on is not None and ends_on < starts_on:
        raise ValidationError("结束日期不能早于开始日期")
    if max_uses is not None and max_uses < 1:
        raise ValidationError("总使用次数至少为 1")
    if per_customer_limit < 1:
        raise ValidationError("每客限用至少为 1")
    scope = normalize_scope(scope_type, scope_values)
    try:
        existing = session.execute(
            select(Coupon.id).where(
                Coupon.merchant_id == merchant_id,
                Coupon.code == code_value,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("优惠码已存在")
        coupon = Coupon(
            merchant_id=merchant_id,
            code=code_value,
            discount_type=discount_type,
            value=value,
            min_spend_cents=min_spend_cents,
            starts_on=starts_on,
            ends_on=ends_on,
            max_uses=max_uses,
            used_count=0,
            per_customer_limit=per_customer_limit,
            scope_type=scope_type,
            scope_values=list(scope) or None,
            claimable=claimable,
            status="active",
            created_by=user_id,
        )
        session.add(coupon)
        session.flush()
        append_event(
            session,
            merchant_id,
            "coupon_created",
            {
                "coupon_id": coupon.id,
                "code": coupon.code,
                "discount_type": discount_type,
                "value": value,
                "scope_type": scope_type,
                "scope_values": list(scope),
                "claimable": claimable,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(coupon)
    except Exception:
        session.rollback()
        raise


def list_coupons(session: Session, merchant_id: int) -> list[CouponView]:
    coupons = session.execute(
        select(Coupon)
        .where(Coupon.merchant_id == merchant_id)
        .order_by(Coupon.id.desc())
        .limit(100)
    ).scalars().all()
    counts = dict(
        session.execute(
            select(CouponClaim.coupon_id, func.count(CouponClaim.id))
            .where(CouponClaim.merchant_id == merchant_id)
            .group_by(CouponClaim.coupon_id)
        ).all()
    )
    return [
        _view(coupon, int(counts.get(coupon.id, 0)))
        for coupon in coupons
    ]


def claimable_coupons(
    session: Session, customer: Customer
) -> list[ClaimableCouponView]:
    today = date.today()
    coupons = session.execute(
        select(Coupon)
        .where(
            Coupon.merchant_id == customer.merchant_id,
            Coupon.status == "active",
            Coupon.claimable.is_(True),
            Coupon.starts_on <= today,
            (Coupon.ends_on.is_(None)) | (Coupon.ends_on >= today),
        )
        .order_by(Coupon.id.desc())
        .limit(100)
    ).scalars().all()
    claimed_ids = set(
        session.execute(
            select(CouponClaim.coupon_id).where(
                CouponClaim.customer_id == customer.id
            )
        ).scalars().all()
    )
    return [
        ClaimableCouponView(
            id=coupon.id,
            discount_type=coupon.discount_type,
            value=coupon.value,
            min_spend_cents=coupon.min_spend_cents,
            starts_on=coupon.starts_on,
            ends_on=coupon.ends_on,
            scope_type=coupon.scope_type,
            scope_values=tuple(coupon.scope_values or ()),
            claimed=coupon.id in claimed_ids,
            sold_out=(
                coupon.max_uses is not None
                and coupon.used_count >= coupon.max_uses
            ),
        )
        for coupon in coupons
    ]


def claim_coupon(
    session: Session, customer: Customer, coupon_id: int
) -> WalletCouponView:
    today = date.today()
    try:
        coupon = session.execute(
            select(Coupon)
            .where(
                Coupon.id == coupon_id,
                Coupon.merchant_id == customer.merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if coupon is None:
            raise NotFoundError("优惠券不存在")
        if (
            coupon.status != "active"
            or not coupon.claimable
            or today < coupon.starts_on
            or (coupon.ends_on is not None and today > coupon.ends_on)
        ):
            raise ConflictError("该优惠券不可领取")
        if (
            coupon.max_uses is not None
            and coupon.used_count >= coupon.max_uses
        ):
            raise ConflictError("优惠券已被领完")
        existing = session.execute(
            select(CouponClaim.id).where(
                CouponClaim.coupon_id == coupon.id,
                CouponClaim.customer_id == customer.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("已领取该优惠券")
        claim = CouponClaim(
            merchant_id=customer.merchant_id,
            coupon_id=coupon.id,
            customer_id=customer.id,
            claimed_at=datetime.now(UTC),
        )
        session.add(claim)
        session.flush()
        append_event(
            session,
            customer.merchant_id,
            "coupon_claimed",
            {
                "coupon_id": coupon.id,
                "code": coupon.code,
                "customer_id": customer.id,
            },
        )
        session.commit()
        return _wallet_view(coupon, claim, 0)
    except Exception:
        session.rollback()
        raise


def _wallet_view(
    coupon: Coupon, claim: CouponClaim, times_used: int
) -> WalletCouponView:
    today = date.today()
    remaining = max(0, coupon.per_customer_limit - times_used)
    if coupon.status != "active":
        state = "disabled"
    elif coupon.ends_on is not None and today > coupon.ends_on:
        state = "expired"
    elif remaining <= 0:
        state = "used_up"
    else:
        state = "active"
    return WalletCouponView(
        id=coupon.id,
        code=coupon.code,
        discount_type=coupon.discount_type,
        value=coupon.value,
        min_spend_cents=coupon.min_spend_cents,
        starts_on=coupon.starts_on,
        ends_on=coupon.ends_on,
        scope_type=coupon.scope_type,
        scope_values=tuple(coupon.scope_values or ()),
        status=coupon.status,
        claimed_at=claim.claimed_at,
        times_used=times_used,
        remaining_uses=remaining,
        state=state,
    )


def customer_coupon_wallet(
    session: Session, customer: Customer
) -> list[WalletCouponView]:
    rows = session.execute(
        select(CouponClaim, Coupon)
        .join(Coupon, Coupon.id == CouponClaim.coupon_id)
        .where(CouponClaim.customer_id == customer.id)
        .order_by(CouponClaim.claimed_at.desc(), CouponClaim.id.desc())
    ).all()
    usage = dict(
        session.execute(
            select(
                CouponRedemption.coupon_id,
                func.count(CouponRedemption.id),
            )
            .where(CouponRedemption.customer_id == customer.id)
            .group_by(CouponRedemption.coupon_id)
        ).all()
    )
    return [
        _wallet_view(coupon, claim, int(usage.get(coupon.id, 0)))
        for claim, coupon in rows
    ]


def set_coupon_status(
    session: Session,
    merchant_id: int,
    user_id: int,
    coupon_id: int,
    status: str,
) -> CouponView:
    if status not in ("active", "disabled"):
        raise ValidationError("未知优惠券状态")
    try:
        coupon = session.execute(
            select(Coupon)
            .where(
                Coupon.id == coupon_id,
                Coupon.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if coupon is None:
            raise NotFoundError("优惠券不存在")
        coupon.status = status
        append_event(
            session,
            merchant_id,
            "coupon_status_changed",
            {
                "coupon_id": coupon.id,
                "code": coupon.code,
                "status": status,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(coupon)
    except Exception:
        session.rollback()
        raise


def apply_coupon(
    session: Session,
    customer: Customer,
    code: str,
    item_total: int,
    items: list[tuple[CartItem, Product]] | None = None,
) -> tuple[Coupon, int]:
    code_value = code.strip().upper()
    coupon = session.execute(
        select(Coupon)
        .where(
            Coupon.merchant_id == customer.merchant_id,
            Coupon.code == code_value,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if coupon is None:
        raise NotFoundError("优惠券不存在")
    today = date.today()
    if coupon.status != "active":
        raise ConflictError("优惠券已停用")
    if today < coupon.starts_on or (
        coupon.ends_on is not None and today > coupon.ends_on
    ):
        raise ConflictError("优惠券不在有效期内")
    scope_values = set(coupon.scope_values or ())
    if coupon.scope_type == "category":
        eligible_total = (
            sum(
                item.qty * item.unit_price_cents
                for item, product in (items or [])
                if product.category in scope_values
            )
            if items is not None
            else 0
        )
        if eligible_total <= 0:
            raise ConflictError("订单中没有适用该优惠券的商品")
    elif coupon.scope_type == "sku":
        eligible_total = (
            sum(
                item.qty * item.unit_price_cents
                for item, product in (items or [])
                if product.sku in scope_values
            )
            if items is not None
            else 0
        )
        if eligible_total <= 0:
            raise ConflictError("订单中没有适用该优惠券的商品")
    else:
        eligible_total = item_total
    if eligible_total < coupon.min_spend_cents:
        raise ConflictError("适用商品金额未达到优惠券使用门槛")
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        raise ConflictError("优惠券已被领完")
    used_by_customer = session.execute(
        select(func.count(CouponRedemption.id)).where(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.customer_id == customer.id,
        )
    ).scalar_one()
    if used_by_customer >= coupon.per_customer_limit:
        raise ConflictError("该优惠券已达到每客使用上限")
    if coupon.discount_type == "fixed":
        discount = min(coupon.value, eligible_total)
    else:
        discount = eligible_total * coupon.value // 100
    if discount <= 0:
        raise ConflictError("优惠券不产生可抵扣金额")
    return coupon, discount


def redeem_coupon(
    session: Session,
    coupon: Coupon,
    order: Order,
    customer: Customer,
    discount_cents: int,
) -> None:
    coupon.used_count += 1
    session.add(
        CouponRedemption(
            merchant_id=coupon.merchant_id,
            coupon_id=coupon.id,
            order_id=order.id,
            customer_id=customer.id,
            discount_cents=discount_cents,
        )
    )
    append_event(
        session,
        coupon.merchant_id,
        "coupon_redeemed",
        {
            "coupon_id": coupon.id,
            "code": coupon.code,
            "order_no": order.order_no,
            "discount_cents": discount_cents,
        },
    )


def release_coupon(session: Session, order: Order) -> None:
    redemption = session.execute(
        select(CouponRedemption)
        .where(CouponRedemption.order_id == order.id)
        .with_for_update()
    ).scalar_one_or_none()
    if redemption is None:
        return
    coupon = session.execute(
        select(Coupon)
        .where(Coupon.id == redemption.coupon_id)
        .with_for_update()
    ).scalar_one_or_none()
    if coupon is not None:
        coupon.used_count = max(0, coupon.used_count - 1)
    session.delete(redemption)
    append_event(
        session,
        order.merchant_id,
        "coupon_released",
        {
            "coupon_id": redemption.coupon_id,
            "order_no": order.order_no,
            "discount_cents": redemption.discount_cents,
        },
    )
