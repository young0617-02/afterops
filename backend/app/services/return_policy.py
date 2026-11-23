from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import ReturnPolicy
from app.services.audit import append_event
from app.services.errors import ValidationError

DEFAULT_WINDOW_DAYS = 7
DEFAULT_ALLOW_EXCHANGE = True
DEFAULT_RETURN_SHIPPING_FEE_CENTS = 0
MAX_WINDOW_DAYS = 90
MAX_RETURN_SHIPPING_FEE_CENTS = 20000
MAX_FINAL_SALE_CATEGORIES = 20
MAX_CATEGORY_WINDOWS = 20
MAX_CATEGORY_LENGTH = 40


@dataclass(frozen=True)
class ReturnCategoryWindow:
    category: str
    window_days: int


@dataclass(frozen=True)
class ReturnPolicyView:
    window_days: int
    return_shipping_fee_cents: int
    allow_exchange: bool
    final_sale_categories: tuple[str, ...]
    category_windows: tuple[ReturnCategoryWindow, ...]
    updated_at: datetime | None


def _view(policy: ReturnPolicy | None) -> ReturnPolicyView:
    if policy is None:
        return ReturnPolicyView(
            window_days=DEFAULT_WINDOW_DAYS,
            return_shipping_fee_cents=DEFAULT_RETURN_SHIPPING_FEE_CENTS,
            allow_exchange=DEFAULT_ALLOW_EXCHANGE,
            final_sale_categories=(),
            category_windows=(),
            updated_at=None,
        )
    return ReturnPolicyView(
        window_days=policy.window_days,
        return_shipping_fee_cents=policy.return_shipping_fee_cents,
        allow_exchange=policy.allow_exchange,
        final_sale_categories=tuple(policy.final_sale_categories or ()),
        category_windows=tuple(
            ReturnCategoryWindow(
                category=str(item.get("category", "")),
                window_days=int(item.get("window_days", 0)),
            )
            for item in (policy.category_windows or [])
            if item.get("category")
        ),
        updated_at=policy.updated_at,
    )


def _payload(view: ReturnPolicyView) -> dict:
    return {
        "window_days": view.window_days,
        "return_shipping_fee_cents": view.return_shipping_fee_cents,
        "allow_exchange": view.allow_exchange,
        "final_sale_categories": list(view.final_sale_categories),
        "category_windows": [
            {
                "category": item.category,
                "window_days": item.window_days,
            }
            for item in view.category_windows
        ],
    }


def normalize_categories(values: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        if len(value) > MAX_CATEGORY_LENGTH:
            raise ValidationError(
                f"禁退类目不能超过 {MAX_CATEGORY_LENGTH} 个字符"
            )
        if value not in normalized:
            normalized.append(value)
    if len(normalized) > MAX_FINAL_SALE_CATEGORIES:
        raise ValidationError(
            f"禁退类目不能超过 {MAX_FINAL_SALE_CATEGORIES} 个"
        )
    return tuple(normalized)


def normalize_category_windows(
    values: list[dict],
) -> tuple[ReturnCategoryWindow, ...]:
    normalized: list[ReturnCategoryWindow] = []
    seen: set[str] = set()
    for raw in values:
        category = str(raw.get("category", "")).strip()
        if not category:
            continue
        if len(category) > MAX_CATEGORY_LENGTH:
            raise ValidationError(
                f"类目名称不能超过 {MAX_CATEGORY_LENGTH} 个字符"
            )
        if category in seen:
            raise ValidationError(f"类目 {category} 配置重复")
        try:
            window_days = int(raw.get("window_days", -1))
        except (TypeError, ValueError) as exc:
            raise ValidationError("类目退货天数必须是整数") from exc
        if window_days < 0 or window_days > MAX_WINDOW_DAYS:
            raise ValidationError(
                f"类目退货天数需在 0–{MAX_WINDOW_DAYS} 天之间"
            )
        seen.add(category)
        normalized.append(
            ReturnCategoryWindow(
                category=category,
                window_days=window_days,
            )
        )
    if len(normalized) > MAX_CATEGORY_WINDOWS:
        raise ValidationError(
            f"类目退货窗口不能超过 {MAX_CATEGORY_WINDOWS} 个"
        )
    return tuple(normalized)


def get_return_policy(session: Session, merchant_id: int) -> ReturnPolicyView:
    policy = session.execute(
        select(ReturnPolicy).where(ReturnPolicy.merchant_id == merchant_id)
    ).scalar_one_or_none()
    return _view(policy)


def update_return_policy(
    session: Session,
    merchant_id: int,
    user_id: int,
    *,
    window_days: int,
    return_shipping_fee_cents: int,
    allow_exchange: bool,
    final_sale_categories: list[str],
    category_windows: list[dict] | None = None,
) -> ReturnPolicyView:
    if window_days < 0 or window_days > MAX_WINDOW_DAYS:
        raise ValidationError(
            f"退货窗口需在 0–{MAX_WINDOW_DAYS} 天之间"
        )
    if (
        return_shipping_fee_cents < 0
        or return_shipping_fee_cents > MAX_RETURN_SHIPPING_FEE_CENTS
    ):
        raise ValidationError(
            f"退货运费需在 0–{MAX_RETURN_SHIPPING_FEE_CENTS // 100} 元之间"
        )
    categories = normalize_categories(final_sale_categories)
    windows = normalize_category_windows(category_windows or [])
    try:
        policy = session.execute(
            select(ReturnPolicy)
            .where(ReturnPolicy.merchant_id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        before = _view(policy)
        if policy is None:
            policy = ReturnPolicy(merchant_id=merchant_id)
            session.add(policy)
        policy.window_days = window_days
        policy.return_shipping_fee_cents = return_shipping_fee_cents
        policy.allow_exchange = allow_exchange
        policy.final_sale_categories = list(categories) or None
        policy.category_windows = [
            {"category": item.category, "window_days": item.window_days}
            for item in windows
        ] or None
        policy.updated_by = user_id
        session.flush()
        after = _view(policy)
        append_event(
            session,
            merchant_id,
            "return_policy_updated",
            {
                "before": _payload(before),
                "after": _payload(after),
                "operator_user_id": user_id,
            },
        )
        session.commit()
        session.refresh(policy)
        return _view(policy)
    except Exception:
        session.rollback()
        raise
