from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.domain.models import User
from app.services.staff import (
    create_staff,
    is_online,
    list_staff,
    record_heartbeat,
    reset_staff_password,
    update_staff,
    update_staff_skills,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class StaffOut(BaseModel):
    id: int
    email: str
    role: str
    status: str
    handoff_available: bool
    last_seen_at: datetime | None
    online: bool
    handoff_skills: list[str]
    created_at: datetime


class CreateStaffIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["owner", "agent"] = "agent"


class UpdateStaffIn(BaseModel):
    role: Literal["owner", "agent"] | None = None
    status: Literal["active", "disabled"] | None = None


class ResetPasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class UpdateSkillsIn(BaseModel):
    skills: list[Literal["after_sales", "complaint"]] = Field(
        default_factory=list, max_length=2
    )


def _out(user: User) -> StaffOut:
    return StaffOut(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        handoff_available=user.handoff_available,
        last_seen_at=user.last_seen_at,
        online=is_online(user),
        handoff_skills=list(user.handoff_skills or []),
        created_at=user.created_at,
    )


@router.get("/staff", response_model=list[StaffOut])
def admin_staff(admin: OwnerDep, session: SessionDep) -> list[StaffOut]:
    return [_out(user) for user in list_staff(session, admin.merchant_id)]


@router.post("/heartbeat", response_model=StaffOut)
def admin_heartbeat(admin: AdminDep, session: SessionDep) -> StaffOut:
    return _out(record_heartbeat(session, admin.merchant_id, admin.user_id))


@router.patch("/staff/{user_id}/skills", response_model=StaffOut)
def admin_update_staff_skills(
    user_id: int,
    payload: UpdateSkillsIn,
    admin: OwnerDep,
    session: SessionDep,
) -> StaffOut:
    return _out(
        update_staff_skills(
            session,
            admin.merchant_id,
            admin.user_id,
            user_id,
            payload.skills,
        )
    )


@router.post("/staff", response_model=StaffOut)
def admin_staff_create(
    payload: CreateStaffIn,
    admin: OwnerDep,
    session: SessionDep,
) -> StaffOut:
    return _out(
        create_staff(
            session,
            admin.merchant_id,
            admin.user_id,
            email=payload.email,
            password=payload.password,
            role=payload.role,
        )
    )


@router.patch("/staff/{user_id}", response_model=StaffOut)
def admin_staff_update(
    user_id: int,
    payload: UpdateStaffIn,
    admin: OwnerDep,
    session: SessionDep,
) -> StaffOut:
    return _out(
        update_staff(
            session,
            admin.merchant_id,
            admin.user_id,
            user_id,
            role=payload.role,
            status=payload.status,
        )
    )


@router.post("/staff/{user_id}/password", response_model=StaffOut)
def admin_staff_password(
    user_id: int,
    payload: ResetPasswordIn,
    admin: OwnerDep,
    session: SessionDep,
) -> StaffOut:
    return _out(
        reset_staff_password(
            session,
            admin.merchant_id,
            admin.user_id,
            user_id,
            payload.password,
        )
    )
