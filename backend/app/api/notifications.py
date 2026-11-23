from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.admin import AdminDep
from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import Notification
from app.services.errors import NotFoundError

router = APIRouter(tags=["notifications"])


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    body: str
    link: str
    read_at: datetime | None
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class NotificationReadOut(BaseModel):
    id: int
    read_at: datetime


class NotificationReadAllOut(BaseModel):
    updated: int


def _to_out(notification: Notification) -> NotificationOut:
    return NotificationOut(
        id=notification.id,
        type=notification.type,
        title=notification.title,
        body=notification.body,
        link=notification.link,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


def _recipient_condition(recipient_type: str, recipient_id: int):
    if recipient_type == "customer":
        return Notification.customer_id == recipient_id
    return Notification.user_id == recipient_id


def _list(
    session: Session,
    *,
    recipient_type: str,
    recipient_id: int,
    unread_only: bool,
    limit: int,
) -> NotificationListOut:
    condition = (
        Notification.recipient_type == recipient_type,
        _recipient_condition(recipient_type, recipient_id),
    )
    stmt = select(Notification).where(*condition)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    items = session.execute(
        stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).scalars().all()
    unread_count = session.execute(
        select(func.count(Notification.id)).where(
            *condition, Notification.read_at.is_(None)
        )
    ).scalar_one()
    return NotificationListOut(
        items=[_to_out(item) for item in items],
        unread_count=int(unread_count),
    )


def _mark_read(
    session: Session,
    *,
    recipient_type: str,
    recipient_id: int,
    notification_id: int,
) -> NotificationReadOut:
    notification = session.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_type == recipient_type,
            _recipient_condition(recipient_type, recipient_id),
        )
    ).scalar_one_or_none()
    if notification is None:
        raise NotFoundError("通知不存在")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        session.commit()
    return NotificationReadOut(id=notification.id, read_at=notification.read_at)


def _mark_all_read(
    session: Session,
    *,
    recipient_type: str,
    recipient_id: int,
) -> NotificationReadAllOut:
    result = session.execute(
        update(Notification)
        .where(
            Notification.recipient_type == recipient_type,
            _recipient_condition(recipient_type, recipient_id),
            Notification.read_at.is_(None),
        )
        .values(read_at=datetime.now(UTC))
    )
    session.commit()
    return NotificationReadAllOut(updated=int(result.rowcount or 0))


async def _notification_event_stream(
    request: Request,
    session: Session,
    *,
    merchant_id: int,
    recipient_type: str,
    recipient_id: int,
    after_id: int,
    wait_seconds: int,
):
    last_id = after_id
    deadline = time.monotonic() + wait_seconds
    heartbeat_at = time.monotonic() + 10
    while time.monotonic() < deadline:
        rows = session.execute(
            select(Notification)
            .where(
                Notification.merchant_id == merchant_id,
                Notification.recipient_type == recipient_type,
                _recipient_condition(recipient_type, recipient_id),
                Notification.id > last_id,
            )
            .order_by(Notification.id)
            .limit(50)
        ).scalars().all()
        for notification in rows:
            last_id = notification.id
            payload = {
                "id": notification.id,
                "type": notification.type,
                "title": notification.title,
                "body": notification.body,
                "link": notification.link,
                "read_at": (
                    notification.read_at.isoformat()
                    if notification.read_at
                    else None
                ),
                "created_at": notification.created_at.isoformat(),
            }
            yield (
                f"id: {notification.id}\n"
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )
        if await request.is_disconnected():
            return
        if time.monotonic() >= heartbeat_at:
            yield ": ping\n\n"
            heartbeat_at = time.monotonic() + 10
        await asyncio.sleep(1.0)
    yield ": stream-timeout\n\n"


@router.get("/api/store/notifications", response_model=NotificationListOut)
def customer_notifications(
    customer: CurrentCustomer,
    session: SessionDep,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> NotificationListOut:
    return _list(
        session,
        recipient_type="customer",
        recipient_id=customer.id,
        unread_only=unread_only,
        limit=limit,
    )


@router.get("/api/store/notifications/stream")
async def customer_notification_stream(
    request: Request,
    customer: CurrentCustomer,
    session: SessionDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    wait_seconds: Annotated[int, Query(ge=1, le=25)] = 25,
    last_event_id: Annotated[
        str | None, Header(alias="Last-Event-ID")
    ] = None,
) -> StreamingResponse:
    start_id = after_id
    if last_event_id and last_event_id.isdigit():
        start_id = max(start_id, int(last_event_id))
    return StreamingResponse(
        _notification_event_stream(
            request,
            session,
            merchant_id=customer.merchant_id,
            recipient_type="customer",
            recipient_id=customer.id,
            after_id=start_id,
            wait_seconds=wait_seconds,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/api/store/notifications/{notification_id}/read",
    response_model=NotificationReadOut,
)
def customer_notification_read(
    notification_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> NotificationReadOut:
    return _mark_read(
        session,
        recipient_type="customer",
        recipient_id=customer.id,
        notification_id=notification_id,
    )


@router.post(
    "/api/store/notifications/read-all",
    response_model=NotificationReadAllOut,
)
def customer_notifications_read_all(
    customer: CurrentCustomer, session: SessionDep
) -> NotificationReadAllOut:
    return _mark_all_read(
        session, recipient_type="customer", recipient_id=customer.id
    )


@router.get("/api/admin/notifications", response_model=NotificationListOut)
def admin_notifications(
    admin: AdminDep,
    session: SessionDep,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> NotificationListOut:
    return _list(
        session,
        recipient_type="user",
        recipient_id=admin.user_id,
        unread_only=unread_only,
        limit=limit,
    )


@router.get("/api/admin/notifications/stream")
async def admin_notification_stream(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    wait_seconds: Annotated[int, Query(ge=1, le=25)] = 25,
    last_event_id: Annotated[
        str | None, Header(alias="Last-Event-ID")
    ] = None,
) -> StreamingResponse:
    start_id = after_id
    if last_event_id and last_event_id.isdigit():
        start_id = max(start_id, int(last_event_id))
    return StreamingResponse(
        _notification_event_stream(
            request,
            session,
            merchant_id=admin.merchant_id,
            recipient_type="user",
            recipient_id=admin.user_id,
            after_id=start_id,
            wait_seconds=wait_seconds,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/api/admin/notifications/{notification_id}/read",
    response_model=NotificationReadOut,
)
def admin_notification_read(
    notification_id: int,
    admin: AdminDep,
    session: SessionDep,
) -> NotificationReadOut:
    return _mark_read(
        session,
        recipient_type="user",
        recipient_id=admin.user_id,
        notification_id=notification_id,
    )


@router.post(
    "/api/admin/notifications/read-all",
    response_model=NotificationReadAllOut,
)
def admin_notifications_read_all(
    admin: AdminDep, session: SessionDep
) -> NotificationReadAllOut:
    return _mark_all_read(
        session, recipient_type="user", recipient_id=admin.user_id
    )
