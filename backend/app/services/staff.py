from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import User
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.passwords import hash_password
from app.settings import settings

HANDOFF_SKILL_QUEUES = ("after_sales", "complaint")


def is_online(user: User, now: datetime | None = None) -> bool:
    if user.status != "active" or not user.handoff_available:
        return False
    if user.last_seen_at is None:
        return False
    seen = user.last_seen_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return (current - seen).total_seconds() <= settings.agent_online_window_seconds


def record_heartbeat(session: Session, merchant_id: int, user_id: int) -> User:
    try:
        user = session.execute(
            select(User)
            .where(User.id == user_id, User.merchant_id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("员工不存在")
        user.last_seen_at = datetime.now(UTC)
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def update_staff_skills(
    session: Session,
    merchant_id: int,
    operator_id: int,
    user_id: int,
    skills: list[str],
) -> User:
    normalized = sorted(set(skills))
    unknown = set(normalized) - set(HANDOFF_SKILL_QUEUES)
    if unknown:
        raise ValidationError("未知工单队列技能")
    try:
        user = session.execute(
            select(User)
            .where(User.id == user_id, User.merchant_id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("员工不存在")
        if user.role != "agent":
            raise ValidationError("只有客服可以配置工单技能")
        before = user.handoff_skills or []
        if before != normalized:
            user.handoff_skills = normalized
            append_event(
                session,
                merchant_id,
                "staff_skills_updated",
                {
                    "user_id": user.id,
                    "email": user.email,
                    "skills": normalized,
                    "operator_user_id": operator_id,
                },
            )
            session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def list_staff(session: Session, merchant_id: int) -> list[User]:
    return session.execute(
        select(User)
        .where(User.merchant_id == merchant_id)
        .order_by(User.role, User.created_at, User.id)
    ).scalars().all()


def _active_owner_count(session: Session, merchant_id: int) -> int:
    return int(
        session.execute(
            select(func.count(User.id)).where(
                User.merchant_id == merchant_id,
                User.role == "owner",
                User.status == "active",
            )
        ).scalar_one()
    )


def _validate_password(password: str) -> str:
    if len(password) < 8:
        raise ValidationError("密码至少 8 位")
    if len(password) > 128:
        raise ValidationError("密码过长")
    return password


def create_staff(
    session: Session,
    merchant_id: int,
    operator_id: int,
    *,
    email: str,
    password: str,
    role: str,
) -> User:
    email_value = email.strip().lower()
    if not email_value or "@" not in email_value:
        raise ValidationError("邮箱格式不正确")
    if role not in ("owner", "agent"):
        raise ValidationError("未知角色")
    _validate_password(password)
    try:
        user = User(
            merchant_id=merchant_id,
            email=email_value,
            password_hash=hash_password(password),
            role=role,
            status="active",
        )
        session.add(user)
        session.flush()
        append_event(
            session,
            merchant_id,
            "staff_created",
            {
                "user_id": user.id,
                "email": user.email,
                "role": role,
                "operator_user_id": operator_id,
            },
        )
        session.commit()
        return user
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("该邮箱已存在") from exc
    except Exception:
        session.rollback()
        raise


def update_staff(
    session: Session,
    merchant_id: int,
    operator_id: int,
    user_id: int,
    *,
    role: str | None = None,
    status: str | None = None,
) -> User:
    if role is not None and role not in ("owner", "agent"):
        raise ValidationError("未知角色")
    if status is not None and status not in ("active", "disabled"):
        raise ValidationError("未知账号状态")
    try:
        user = session.execute(
            select(User)
            .where(User.id == user_id, User.merchant_id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("员工不存在")
        if user.id == operator_id and (
            status == "disabled" or (role is not None and role != "owner")
        ):
            raise ConflictError("不能停用或降级当前登录账号")
        losing_owner = (
            user.role == "owner"
            and user.status == "active"
            and ((status is not None and status != "active") or (role is not None and role != "owner"))
        )
        if losing_owner and _active_owner_count(session, merchant_id) <= 1:
            raise ConflictError("至少保留一个启用状态的店主")
        changed = []
        if role is not None and role != user.role:
            user.role = role
            changed.append("role")
        if status is not None and status != user.status:
            user.status = status
            changed.append("status")
        if changed:
            append_event(
                session,
                merchant_id,
                "staff_updated",
                {
                    "user_id": user.id,
                    "email": user.email,
                    "fields": changed,
                    "role": user.role,
                    "status": user.status,
                    "operator_user_id": operator_id,
                },
            )
            session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def set_handoff_availability(
    session: Session,
    merchant_id: int,
    user_id: int,
    available: bool,
) -> User:
    try:
        user = session.execute(
            select(User)
            .where(User.id == user_id, User.merchant_id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("员工不存在")
        user.last_seen_at = datetime.now(UTC)
        if user.handoff_available != available:
            user.handoff_available = available
            append_event(
                session,
                merchant_id,
                "staff_availability_changed",
                {
                    "user_id": user.id,
                    "email": user.email,
                    "available": available,
                    "operator_user_id": user_id,
                },
            )
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def reset_staff_password(
    session: Session,
    merchant_id: int,
    operator_id: int,
    user_id: int,
    password: str,
) -> User:
    _validate_password(password)
    try:
        user = session.execute(
            select(User)
            .where(User.id == user_id, User.merchant_id == merchant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("员工不存在")
        user.password_hash = hash_password(password)
        append_event(
            session,
            merchant_id,
            "staff_password_reset",
            {
                "user_id": user.id,
                "email": user.email,
                "operator_user_id": operator_id,
            },
        )
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise
