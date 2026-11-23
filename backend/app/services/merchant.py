from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Merchant
from app.services.audit import append_event
from app.services.errors import NotFoundError, ServiceUnavailableError
from app.settings import settings


@dataclass(frozen=True)
class HandoffAssignmentSettings:
    auto_assign_handoffs: bool


def get_handoff_assignment_settings(
    session: Session, merchant_id: int
) -> HandoffAssignmentSettings:
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise NotFoundError("商家不存在")
    return HandoffAssignmentSettings(
        auto_assign_handoffs=merchant.auto_assign_handoffs
    )


def update_handoff_assignment_settings(
    session: Session,
    merchant_id: int,
    user_id: int,
    *,
    auto_assign_handoffs: bool,
) -> HandoffAssignmentSettings:
    try:
        merchant = session.execute(
            select(Merchant)
            .where(Merchant.id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if merchant is None:
            raise NotFoundError("商家不存在")
        previous = merchant.auto_assign_handoffs
        merchant.auto_assign_handoffs = auto_assign_handoffs
        if previous != auto_assign_handoffs:
            append_event(
                session,
                merchant_id,
                "handoff_assignment_updated",
                {
                    "from": previous,
                    "to": auto_assign_handoffs,
                    "operator_user_id": user_id,
                },
            )
            session.commit()
        return HandoffAssignmentSettings(
            auto_assign_handoffs=merchant.auto_assign_handoffs
        )
    except Exception:
        session.rollback()
        raise


def demo_merchant_id(session: Session) -> int:
    merchant_id = session.execute(
        select(Merchant.id).where(
            Merchant.name == settings.demo_merchant_name,
            Merchant.status == "active",
        )
    ).scalar_one_or_none()
    if merchant_id is None:
        raise ServiceUnavailableError("演示商家未初始化，请先运行 seed")
    return merchant_id
