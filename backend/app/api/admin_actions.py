from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.admin import OwnerDep
from app.api.deps import SessionDep
from app.domain.money import format_cents
from app.services.proposals import create_staff_action_proposal

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ManualActionIn(BaseModel):
    action_type: Literal["refund", "compensation", "address_change"]
    order_no: str = Field(min_length=1, max_length=32)
    amount_cents: int | None = Field(default=None, ge=1)
    reason: str = Field(default="", max_length=1000)
    new_address_text: str | None = Field(default=None, max_length=255)


class ManualActionOut(BaseModel):
    proposal_id: int
    action_type: str
    order_no: str
    status: str
    risk_level: str
    amount_cents: int | None
    amount_display: str | None


@router.post("/action-proposals", response_model=ManualActionOut)
def create_manual_action(
    payload: ManualActionIn,
    admin: OwnerDep,
    session: SessionDep,
) -> ManualActionOut:
    result = create_staff_action_proposal(
        session,
        admin.merchant_id,
        payload.action_type,
        payload.order_no.strip(),
        amount_cents=payload.amount_cents,
        reason=payload.reason,
        new_address_text=payload.new_address_text,
    )
    amount = result.amount_cents
    return ManualActionOut(
        proposal_id=result.proposal_id,
        action_type=result.action_type,
        order_no=result.order_no,
        status=result.status,
        risk_level=result.risk_level,
        amount_cents=amount,
        amount_display=format_cents(amount) if amount is not None else None,
    )
