from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.ai.chat import handle_chat
from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import Conversation, Message
from app.services import rate_limit
from app.services.errors import NotFoundError
from app.settings import settings

router = APIRouter(prefix="/api", tags=["chat"])


class ChatIn(BaseModel):
    text: str = Field(default="", max_length=500)
    conversation_id: int | None = None
    attachment_ids: list[int] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def require_text_or_attachment(self) -> ChatIn:
        if not self.text.strip() and not self.attachment_ids:
            raise ValueError("请输入问题或上传图片")
        return self


class HandoffOut(BaseModel):
    available: bool
    queue: str
    ticket_ref: str | None


class ChatOut(BaseModel):
    conversation_id: int
    message_id: int
    reply: str
    intent: str
    intent_label: str
    confidence: float
    citations: list[str]
    handoff: HandoffOut
    proposals: list[dict]
    returns: list[dict]
    audit_id: int
    provider: str
    degraded: bool
    fallback_reason: str | None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    tool_calls_json: dict | None
    image_urls: list[str]
    created_at: datetime


class ConversationSummaryOut(BaseModel):
    id: int
    status: str
    created_at: datetime
    updated_at: datetime
    last_message: str | None


class ConversationDetailOut(BaseModel):
    id: int
    status: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessageOut]


@router.post("/chat", response_model=ChatOut)
def chat(
    payload: ChatIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> ChatOut:
    rate_limit.check(
        session,
        f"chat:{customer.merchant_id}:{customer.id}",
        settings.chat_limit,
        settings.chat_window_seconds,
    )
    result = handle_chat(
        session,
        customer,
        payload.text,
        conversation_id=payload.conversation_id,
        attachment_ids=payload.attachment_ids,
    )
    return ChatOut(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        reply=result.reply,
        intent=result.intent,
        intent_label=result.intent_label,
        confidence=result.confidence,
        citations=result.citations,
        handoff=HandoffOut(
            available=result.handoff.available,
            queue=result.handoff.queue,
            ticket_ref=result.handoff.ticket_ref,
        ),
        proposals=result.proposals,
        returns=result.returns,
        audit_id=result.audit_id,
        provider=result.provider,
        degraded=result.degraded,
        fallback_reason=result.fallback_reason,
    )


def _message_out(message: Message) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        tool_calls_json=message.tool_calls_json,
        image_urls=[
            f"/api/store/attachments/{attachment_id}"
            for attachment_id in (message.attachment_ids or [])
        ],
        created_at=message.created_at,
    )


async def _event_stream(
    request: Request,
    session,
    conversation_id: int,
    start_id: int,
    wait_seconds: int,
) -> AsyncIterator[str]:
    yield "retry: 3000\n"
    yield ": connected\n\n"
    last_id = start_id
    deadline = time.monotonic() + wait_seconds
    heartbeat_at = time.monotonic() + 10
    while time.monotonic() < deadline:
        def _fetch(after: int):
            session.expire_all()
            return session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.id > after,
                )
                .order_by(Message.id)
            ).scalars().all()

        messages = await run_in_threadpool(_fetch, last_id)
        for message in messages:
            last_id = message.id
            payload = _message_out(message).model_dump(mode="json")
            yield (
                f"id: {message.id}\n"
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )
        if await request.is_disconnected():
            return
        if time.monotonic() >= heartbeat_at:
            yield ": ping\n\n"
            heartbeat_at = time.monotonic() + 10
        await asyncio.sleep(1.0)
    yield ": stream-timeout\n\n"


@router.get("/chat/conversations/{conversation_id}/stream")
async def stream_conversation(
    request: Request,
    conversation_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    wait_seconds: Annotated[int, Query(ge=1, le=30)] = 25,
    last_event_id: Annotated[
        str | None, Header(alias="Last-Event-ID")
    ] = None,
) -> StreamingResponse:
    conversation = session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.merchant_id == customer.merchant_id,
            Conversation.customer_id == customer.id,
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("会话不存在")
    start_id = after_id
    if last_event_id and last_event_id.isdigit():
        start_id = max(start_id, int(last_event_id))
    return StreamingResponse(
        _event_stream(
            request, session, conversation_id, start_id, wait_seconds
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/conversations", response_model=list[ConversationSummaryOut])
def list_conversations(
    customer: CurrentCustomer, session: SessionDep
) -> list[ConversationSummaryOut]:
    conversations = session.execute(
        select(Conversation)
        .where(
            Conversation.merchant_id == customer.merchant_id,
            Conversation.customer_id == customer.id,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(20)
    ).scalars().all()
    out: list[ConversationSummaryOut] = []
    for conversation in conversations:
        last = session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        out.append(
            ConversationSummaryOut(
                id=conversation.id,
                status=conversation.status,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                last_message=(
                    last.content[:80]
                    if last and last.content
                    else ("（图片）" if last else None)
                ),
            )
        )
    return out


@router.get(
    "/chat/conversations/{conversation_id}",
    response_model=ConversationDetailOut,
)
def get_conversation(
    conversation_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
    after_id: int | None = Query(default=None, ge=1),
) -> ConversationDetailOut:
    conversation = session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.merchant_id == customer.merchant_id,
            Conversation.customer_id == customer.id,
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("会话不存在")
    messages_stmt = (
        select(Message)
        .where(Message.conversation_id == conversation.id)
    )
    if after_id is not None:
        messages_stmt = messages_stmt.where(Message.id > after_id)
    messages = session.execute(
        messages_stmt.order_by(Message.id)
    ).scalars().all()
    return ConversationDetailOut(
        id=conversation.id,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            ChatMessageOut(
                id=message.id,
                role=message.role,
                content=message.content,
                tool_calls_json=message.tool_calls_json,
                image_urls=[
                    f"/api/store/attachments/{attachment_id}"
                    for attachment_id in (message.attachment_ids or [])
                ],
                created_at=message.created_at,
            )
            for message in messages
        ],
    )
