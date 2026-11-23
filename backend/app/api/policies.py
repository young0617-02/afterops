from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.domain.models import PolicyEntry
from app.services.merchant import demo_merchant_id
from app.services.policies import current_policy, list_current_policies

router = APIRouter(prefix="/api/store", tags=["policies"])


class PolicyOut(BaseModel):
    entry_id: str
    topic: str
    title: str
    body: str
    version: str
    source: str
    valid_from: date
    valid_until: date | None
    published_at: date


class PolicyTopicOut(BaseModel):
    topic: str
    entry_id: str
    title: str
    version: str
    valid_from: date
    valid_until: date | None


def _out(entry: PolicyEntry) -> PolicyOut:
    return PolicyOut(
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


@router.get("/policies", response_model=list[PolicyTopicOut])
def list_policies(
    session: SessionDep,
    as_of: Annotated[date | None, Query()] = None,
) -> list[PolicyTopicOut]:
    merchant_id = demo_merchant_id(session)
    return [
        PolicyTopicOut(
            topic=entry.topic,
            entry_id=entry.entry_id,
            title=entry.title,
            version=entry.version,
            valid_from=entry.valid_from,
            valid_until=entry.valid_until,
        )
        for entry in list_current_policies(session, merchant_id, as_of)
    ]


@router.get("/policies/{topic}", response_model=PolicyOut)
def get_policy(
    topic: str,
    session: SessionDep,
    as_of: Annotated[date | None, Query()] = None,
) -> PolicyOut:
    merchant_id = demo_merchant_id(session)
    return _out(current_policy(session, merchant_id, topic, as_of))
