from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.services.merchant import (
    get_handoff_assignment_settings,
    update_handoff_assignment_settings,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class HandoffSettingsOut(BaseModel):
    auto_assign_handoffs: bool


class HandoffSettingsIn(BaseModel):
    auto_assign_handoffs: bool


@router.get("/handoff-settings", response_model=HandoffSettingsOut)
def admin_handoff_settings(
    admin: AdminDep, session: SessionDep
) -> HandoffSettingsOut:
    result = get_handoff_assignment_settings(
        session, admin.merchant_id
    )
    return HandoffSettingsOut(
        auto_assign_handoffs=result.auto_assign_handoffs
    )


@router.patch("/handoff-settings", response_model=HandoffSettingsOut)
def admin_update_handoff_settings(
    payload: HandoffSettingsIn,
    admin: OwnerDep,
    session: SessionDep,
) -> HandoffSettingsOut:
    result = update_handoff_assignment_settings(
        session,
        admin.merchant_id,
        admin.user_id,
        auto_assign_handoffs=payload.auto_assign_handoffs,
    )
    return HandoffSettingsOut(
        auto_assign_handoffs=result.auto_assign_handoffs
    )