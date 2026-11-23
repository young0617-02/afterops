from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import QuickReply, User
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError

STATUSES = ("active", "disabled")


@dataclass(frozen=True)
class QuickReplyView:
    id: int
    title: str
    category: str
    content: str
    status: str
    created_by_email: str | None
    created_at: datetime
    updated_at: datetime


def _view(reply: QuickReply, user: User | None) -> QuickReplyView:
    return QuickReplyView(
        id=reply.id,
        title=reply.title,
        category=reply.category,
        content=reply.content,
        status=reply.status,
        created_by_email=user.email if user else None,
        created_at=reply.created_at,
        updated_at=reply.updated_at,
    )


def list_quick_replies(
    session: Session,
    merchant_id: int,
    *,
    include_disabled: bool = False,
) -> list[QuickReplyView]:
    stmt = select(QuickReply).where(QuickReply.merchant_id == merchant_id)
    if not include_disabled:
        stmt = stmt.where(QuickReply.status == "active")
    replies = session.execute(
        stmt.order_by(QuickReply.category, QuickReply.title, QuickReply.id)
    ).scalars().all()
    user_ids = {reply.created_by for reply in replies if reply.created_by}
    users = (
        {
            user.id: user
            for user in session.execute(
                select(User).where(User.id.in_(user_ids))
            ).scalars().all()
        }
        if user_ids
        else {}
    )
    return [
        _view(reply, users.get(reply.created_by) if reply.created_by else None)
        for reply in replies
    ]


def _validate(
    title: str, content: str, category: str
) -> tuple[str, str, str]:
    title_value = title.strip()
    content_value = content.strip()
    category_value = category.strip()
    if not title_value:
        raise ValidationError("话术标题不能为空")
    if len(title_value) > 120:
        raise ValidationError("话术标题不能超过 120 个字符")
    if not content_value:
        raise ValidationError("话术内容不能为空")
    if len(content_value) > 1000:
        raise ValidationError("话术内容不能超过 1000 个字符")
    if len(category_value) > 80:
        raise ValidationError("话术分类不能超过 80 个字符")
    return title_value, content_value, category_value


def create_quick_reply(
    session: Session,
    merchant_id: int,
    user_id: int,
    *,
    title: str,
    content: str,
    category: str = "",
) -> QuickReplyView:
    title_value, content_value, category_value = _validate(
        title, content, category
    )
    try:
        duplicate = session.execute(
            select(QuickReply.id).where(
                QuickReply.merchant_id == merchant_id,
                QuickReply.title == title_value,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ConflictError("话术标题已存在")
        reply = QuickReply(
            merchant_id=merchant_id,
            title=title_value,
            category=category_value,
            content=content_value,
            status="active",
            created_by=user_id,
        )
        session.add(reply)
        session.flush()
        append_event(
            session,
            merchant_id,
            "quick_reply_created",
            {
                "quick_reply_id": reply.id,
                "title": reply.title,
                "category": reply.category,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        user = session.get(User, user_id)
        return _view(reply, user)
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("话术标题已存在") from exc
    except Exception:
        session.rollback()
        raise


def update_quick_reply(
    session: Session,
    merchant_id: int,
    user_id: int,
    quick_reply_id: int,
    changes: dict,
) -> QuickReplyView:
    try:
        reply = session.execute(
            select(QuickReply)
            .where(
                QuickReply.id == quick_reply_id,
                QuickReply.merchant_id == merchant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if reply is None:
            raise NotFoundError("话术不存在")
        title = changes.get("title", reply.title)
        content = changes.get("content", reply.content)
        category = changes.get("category", reply.category)
        title_value, content_value, category_value = _validate(
            title, content, category
        )
        status = changes.get("status", reply.status)
        if status not in STATUSES:
            raise ValidationError("未知话术状态")
        duplicate = session.execute(
            select(QuickReply.id).where(
                QuickReply.merchant_id == merchant_id,
                QuickReply.title == title_value,
                QuickReply.id != reply.id,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ConflictError("话术标题已存在")
        changed: list[str] = []
        for field, value in (
            ("title", title_value),
            ("content", content_value),
            ("category", category_value),
            ("status", status),
        ):
            if getattr(reply, field) != value:
                setattr(reply, field, value)
                changed.append(field)
        if changed:
            append_event(
                session,
                merchant_id,
                "quick_reply_updated",
                {
                    "quick_reply_id": reply.id,
                    "title": reply.title,
                    "status": reply.status,
                    "fields": changed,
                    "operator_user_id": user_id,
                },
            )
            session.commit()
        user = session.get(User, reply.created_by) if reply.created_by else None
        return _view(reply, user)
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("话术标题已存在") from exc
    except Exception:
        session.rollback()
        raise