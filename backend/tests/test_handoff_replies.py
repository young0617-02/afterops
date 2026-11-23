from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    AuditEvent,
    Conversation,
    HandoffTicket,
    Notification,
)
from app.services.audit import verify_chain
from app.settings import settings


def _human_request(client: TestClient, text: str = "转人工") -> dict:
    response = client.post("/api/chat", json={"text": text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handoff"]["ticket_ref"]
    return body


def _ticket_id(client: TestClient, ticket_ref: str) -> int:
    items = client.get("/api/admin/handoffs?status=all").json()["items"]
    return next(item["id"] for item in items if item["ticket_ref"] == ticket_ref)


def test_owner_reply_auto_claims_and_reaches_customer(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    chat = _human_request(api_client)
    ticket_ref = chat["handoff"]["ticket_ref"]

    admin_login()
    ticket_id = _ticket_id(api_client, ticket_ref)
    reply = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "您好，正在为你核实物流。"},
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["ticket_ref"] == ticket_ref
    assert body["status"] == "claimed"
    assert body["auto_claimed"] is True
    assert body["content"] == "您好，正在为你核实物流。"

    detail = api_client.get(f"/api/admin/handoffs/{ticket_id}").json()
    assert detail["claimed_by_email"] == "owner@test.local"
    assert detail["last_reply"] == "您好，正在为你核实物流。"
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "agent",
    ]
    assert detail["messages"][-1]["content"] == "您好，正在为你核实物流。"

    customer_view = api_client.get(
        f"/api/chat/conversations/{chat['conversation_id']}"
    )
    assert customer_view.status_code == 200, customer_view.text
    history = customer_view.json()["messages"]
    assert history[-1]["role"] == "agent"
    delta = api_client.get(
        f"/api/chat/conversations/{chat['conversation_id']}",
        params={"after_id": history[1]["id"]},
    )
    assert delta.status_code == 200, delta.text
    assert [message["role"] for message in delta.json()["messages"]] == [
        "agent"
    ]

    second = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "已联系承运商，请稍等。"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["auto_claimed"] is False

    notification = db_session.execute(
        select(Notification)
        .where(Notification.type == "agent_reply")
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert notification is not None
    conversation = db_session.get(Conversation, chat["conversation_id"])
    assert conversation is not None
    assert notification.customer_id == conversation.customer_id
    assert notification.title.startswith(f"客服回复了工单 {ticket_ref}")

    db_session.expire_all()
    conversation = db_session.get(Conversation, chat["conversation_id"])
    assert conversation is not None
    assert conversation.status == "handoff"
    events = db_session.execute(
        select(AuditEvent.event_type)
        .where(AuditEvent.event_type.like("handoff_%"))
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert events == [
        "handoff_created",
        "handoff_claimed",
        "handoff_agent_replied",
        "handoff_agent_replied",
    ]
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")

    empty = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "   "},
    )
    assert empty.status_code == 422

    resolved = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/resolve", json={"note": "已处理"}
    )
    assert resolved.status_code == 200, resolved.text
    after_resolve = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "补充说明"},
    )
    assert after_resolve.status_code == 409


def test_customer_messages_wait_for_human_while_ticket_active(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    first = _human_request(api_client)

    waiting = api_client.post(
        "/api/chat",
        json={
            "text": "订单 ORD-2026-000101 到哪了",
            "conversation_id": first["conversation_id"],
        },
    )
    assert waiting.status_code == 200, waiting.text
    body = waiting.json()
    assert body["intent"] == "HANDOFF_WAIT"
    assert body["provider"] == "system"
    assert "人工" in body["reply"]
    assert body["handoff"]["ticket_ref"] == first["handoff"]["ticket_ref"]
    assert body["proposals"] == []
    assert body["citations"] == []

    admin_login("agent@test.local", "agent123456")
    ticket_id = _ticket_id(api_client, first["handoff"]["ticket_ref"])
    claimed = api_client.post(f"/api/admin/handoffs/{ticket_id}/claim")
    assert claimed.status_code == 200, claimed.text
    reply = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "我是人工客服，正在核实。"},
    )
    assert reply.status_code == 200, reply.text

    after_reply = api_client.post(
        "/api/chat",
        json={"text": "好的，谢谢", "conversation_id": first["conversation_id"]},
    )
    assert after_reply.status_code == 200, after_reply.text
    assert after_reply.json()["intent"] == "HANDOFF_WAIT"
    assert "客服已接管" in after_reply.json()["reply"]

    db_session.expire_all()
    ticket = db_session.get(HandoffTicket, ticket_id)
    assert ticket is not None
    assert ticket.last_user_message == "好的，谢谢"
    events = db_session.execute(
        select(AuditEvent.event_type)
        .where(AuditEvent.event_type.like("chat_%"))
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert events == [
        "chat_answered",
        "chat_handoff_message",
        "chat_handoff_message",
    ]

    resolved = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/resolve", json={"note": ""}
    )
    assert resolved.status_code == 200, resolved.text
    resumed = api_client.post(
        "/api/chat",
        json={"text": "已解决，谢谢", "conversation_id": first["conversation_id"]},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["intent"] != "HANDOFF_WAIT"


def test_other_staff_cannot_reply_to_claimed_ticket(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    chat = _human_request(api_client)
    admin_login("agent@test.local", "agent123456")
    ticket_id = _ticket_id(api_client, chat["handoff"]["ticket_ref"])
    reply = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "客服已收到"},
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "claimed"

    api_client.delete("/api/admin/session")
    owner = api_client.post(
        "/api/admin/session",
        json={
            "email": "owner@test.local",
            "password": settings.demo_owner_password,
        },
    )
    assert owner.status_code == 200, owner.text
    conflict = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "店主补充"},
    )
    assert conflict.status_code == 409