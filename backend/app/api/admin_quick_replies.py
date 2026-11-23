from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.services.quick_replies import (
    QuickReplyView,
    create_quick_reply,
    list_quick_replies,
    update_quick_reply,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class QuickReplyOut(BaseModel):
    id: int
    title: str
    category: str
    content: str
    status: str
    created_by_email: str | None
    created_at: datetime
    updated_at: datetime


class QuickReplyCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=80)
    content: str = Field(min_length=1, max_length=1000)


class QuickReplyUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    status: Literal["active", "disabled"] | None = None


def _out(view: QuickReplyView) -> QuickReplyOut:
    return QuickReplyOut(
        id=view.id,
        title=view.title,
        category=view.category,
        content=view.content,
        status=view.status,
        created_by_email=view.created_by_email,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get("/quick-replies", response_model=list[QuickReplyOut])
def admin_quick_replies(
    admin: AdminDep,
    session: SessionDep,
    include_disabled: Annotated[bool, Query()] = False,
) -> list[QuickReplyOut]:
    return [
        _out(view)
        for view in list_quick_replies(
            session,
            admin.merchant_id,
            include_disabled=include_disabled and admin.role == "owner",
        )
    ]


@router.post("/quick-replies", response_model=QuickReplyOut)
def admin_quick_reply_create(
    payload: QuickReplyCreateIn,
    admin: OwnerDep,
    session: SessionDep,
) -> QuickReplyOut:
    view = create_quick_reply(
        session,
        admin.merchant_id,
        admin.user_id,
        title=payload.title,
        content=payload.content,
        category=payload.category,
    )
    return _out(view)


@router.patch("/quick-replies/{quick_reply_id}", response_model=QuickReplyOut)
def admin_quick_reply_update(
    quick_reply_id: int,
    payload: QuickReplyUpdateIn,
    admin: OwnerDep,
    session: SessionDep,
) -> QuickReplyOut:
    view = update_quick_reply(
        session,
        admin.merchant_id,
        admin.user_id,
        quick_reply_id,
        payload.model_dump(exclude_unset=True),
    )
    return _out(view)