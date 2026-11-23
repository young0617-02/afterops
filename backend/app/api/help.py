from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.services.merchant import demo_merchant_id
from app.services.policies import list_current_policies
from app.services.return_policy import get_return_policy

router = APIRouter(prefix="/api/store", tags=["help"])


class HelpPolicyOut(BaseModel):
    entry_id: str
    topic: str
    title: str
    body: str
    version: str
    source: str
    valid_from: date
    valid_until: date | None
    published_at: date


class HelpCategoryWindowOut(BaseModel):
    category: str
    window_days: int


class HelpReturnPolicyOut(BaseModel):
    window_days: int
    return_shipping_fee_cents: int
    allow_exchange: bool
    final_sale_categories: list[str]
    category_windows: list[HelpCategoryWindowOut]


class HelpCenterOut(BaseModel):
    policies: list[HelpPolicyOut]
    return_policy: HelpReturnPolicyOut


@router.get("/help", response_model=HelpCenterOut)
def help_center(session: SessionDep) -> HelpCenterOut:
    merchant_id = demo_merchant_id(session)
    policy = get_return_policy(session, merchant_id)
    return HelpCenterOut(
        policies=[
            HelpPolicyOut(
                entry_id=entry.entry_id,
                topic=entry.topic,
                title=entry.title,
                body=entry.body,
                version=entry.version,
                source=entry.source,
                valid_from=entry.valid_from,
                valid_until=entry.valid_until,
                published_at=entry.published_at,
            )
            for entry in list_current_policies(session, merchant_id)
        ],
        return_policy=HelpReturnPolicyOut(
            window_days=policy.window_days,
            return_shipping_fee_cents=policy.return_shipping_fee_cents,
            allow_exchange=policy.allow_exchange,
            final_sale_categories=list(policy.final_sale_categories),
            category_windows=[
                HelpCategoryWindowOut(
                    category=item.category,
                    window_days=item.window_days,
                )
                for item in policy.category_windows
            ],
        ),
    )
