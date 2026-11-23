from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.domain.models import PolicyEntry
from app.services.policies import list_policy_groups, publish_policy_version

router = APIRouter(prefix="/api/admin", tags=["admin"])


class PolicyVersionOut(BaseModel):
    entry_id: str
    topic: str
    title: str
    body: str
    version: str
    source: str
    valid_from: date
    valid_until: date | None
    published_at: date


class PolicyGroupOut(BaseModel):
    entry_id: str
    topic: str
    current: PolicyVersionOut | None
    versions: list[PolicyVersionOut]


class PublishPolicyIn(BaseModel):
    entry_id: str = Field(
        min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{2,63}$"
    )
    topic: str = Field(
        min_length=2, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]{1,63}$"
    )
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=10000)
    source: str = Field(min_length=1, max_length=255)
    valid_from: date | None = None


def _out(entry: PolicyEntry) -> PolicyVersionOut:
    return PolicyVersionOut(
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


@router.get("/policies", response_model=list[PolicyGroupOut])
def admin_policies(
    admin: AdminDep,
    session: SessionDep,
    as_of: Annotated[date | None, Query()] = None,
) -> list[PolicyGroupOut]:
    return [
        PolicyGroupOut(
            entry_id=group.entry_id,
            topic=group.topic,
            current=_out(group.current) if group.current else None,
            versions=[_out(entry) for entry in group.versions],
        )
        for group in list_policy_groups(session, admin.merchant_id, as_of)
    ]


@router.post("/policies", response_model=PolicyVersionOut)
def publish_policy(
    payload: PublishPolicyIn,
    admin: OwnerDep,
    session: SessionDep,
) -> PolicyVersionOut:
    return _out(
        publish_policy_version(
            session,
            admin.merchant_id,
            admin.user_id,
            entry_id=payload.entry_id,
            topic=payload.topic,
            title=payload.title,
            body=payload.body,
            source=payload.source,
            valid_from=payload.valid_from,
        )
    )
