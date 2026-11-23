from __future__ import annotations

import hmac
from typing import Annotated, Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.services.errors import UnauthorizedError
from app.services.mock_psp import mock_signature
from app.services.payments import mark_payment_paid
from app.settings import settings

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class MockWebhookIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    payment_id: int = Field(gt=0)
    status: Literal["paid"]


@router.post("/psp/mock")
def mock_psp(
    payload: MockWebhookIn,
    session: SessionDep,
    signature: Annotated[str, Header(alias="X-Mock-Signature")],
) -> dict:
    expected = mock_signature(
        payload.event_id, payload.payment_id, payload.status, settings.mock_psp_secret
    )
    if not hmac.compare_digest(signature, expected):
        raise UnauthorizedError("签名无效")

    result = mark_payment_paid(session, payload.payment_id, event_id=payload.event_id)
    return {
        "payment_id": result.payment_id,
        "order_no": result.order_no,
        "status": result.status,
        "amount_cents": result.amount_cents,
    }
