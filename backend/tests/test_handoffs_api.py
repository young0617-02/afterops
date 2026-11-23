from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import (
    AuditEvent,
    Conversation,
    HandoffTicket,
    Notification,
    Outbox,
)
from app.main import app
from app.services.audit import verify_chain
from app.settings import settings

ADDRESS = {
    "recipient_name": "工单测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "工单测试路 1 号",
}


def _paid_order(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    confirm = client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")
    assert confirm.status_code == 200, confirm.text
    return body["order_no"]


def _human_request(client: TestClient, conversation_id: int | None = None) -> dict:
    response = client.post(
        "/api/chat",
        json={"text": "转人工", "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handoff"]["ticket_ref"]
    assert body["handoff"]["ticket_ref"].startswith("TK-")
    return body


def test_human_request_creates_ticket_and_owner_resolves(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    chat = _human_request(api_client)
    ticket_ref = chat["handoff"]["ticket_ref"]
    assert api_client.get("/api/admin/handoffs").status_code == 401

    admin_login()
    listed = api_client.get("/api/admin/handoffs")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["counts"]["open"] == 1
    ticket = next(item for item in body["items"] if item["ticket_ref"] == ticket_ref)
    assert ticket["status"] == "open"
    assert ticket["customer_phone"] == "13800000001"
    assert ticket["last_user_message"] == "转人工"

    detail = api_client.get(f"/api/admin/handoffs/{ticket['id']}")
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "转人工"
    assert "转人工" in messages[1]["content"]

    claimed = api_client.post(f"/api/admin/handoffs/{ticket['id']}/claim")
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "claimed"
    claimed_detail = api_client.get(f"/api/admin/handoffs/{ticket['id']}")
    assert claimed_detail.json()["claimed_by_email"] == "owner@test.local"

    resolved = api_client.post(
        f"/api/admin/handoffs/{ticket['id']}/resolve",
        json={"note": "已电话回访并处理"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"

    db_session.expire_all()
    conversation = db_session.execute(
        select(Conversation).where(Conversation.id == chat["conversation_id"])
    ).scalar_one()
    assert conversation.status == "closed"
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")
    events = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type.like("handoff_%"))
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert [event.event_type for event in events] == [
        "handoff_created",
        "handoff_claimed",
        "handoff_resolved",
    ]

    again = api_client.post(
        f"/api/admin/handoffs/{ticket['id']}/resolve", json={"note": ""}
    )
    assert again.status_code == 409
    resolved_list = api_client.get("/api/admin/handoffs?status=resolved")
    assert [item["ticket_ref"] for item in resolved_list.json()["items"]] == [
        ticket_ref
    ]


def test_handoff_summary_contains_context_and_refreshes(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "handoff-summary-0001")
    response = api_client.post(
        "/api/chat",
        json={
            "text": f"订单 {order_no} 退款一直没到账，我要投诉并转人工",
        },
    )
    assert response.status_code == 200, response.text
    chat = response.json()
    ticket_ref_value = chat["handoff"]["ticket_ref"]
    assert ticket_ref_value.startswith("TK-")

    admin_login()
    listed = api_client.get("/api/admin/handoffs")
    assert listed.status_code == 200, listed.text
    ticket = next(
        item
        for item in listed.json()["items"]
        if item["ticket_ref"] == ticket_ref_value
    )
    summary = ticket["summary"]
    assert f"【订单】{order_no}" in summary
    assert "【客户最近】" in summary
    assert "【建议】" in summary
    assert "【会话】2 条消息" in summary
    assert ticket["summary_generated_at"] is not None

    detail = api_client.get(f"/api/admin/handoffs/{ticket['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["summary"] == summary

    follow_up = api_client.post(
        "/api/chat",
        json={
            "conversation_id": chat["conversation_id"],
            "text": "已经等了一周，今天必须给我处理结果",
        },
    )
    assert follow_up.status_code == 200, follow_up.text

    refreshed = api_client.post(
        f"/api/admin/handoffs/{ticket['id']}/summary"
    )
    assert refreshed.status_code == 200, refreshed.text
    refreshed_summary = refreshed.json()["summary"]
    assert "今天必须给我处理结果" in refreshed_summary
    assert "【会话】4 条消息" in refreshed_summary

    db_session.expire_all()
    ticket_row = db_session.execute(
        select(HandoffTicket).where(HandoffTicket.id == ticket["id"])
    ).scalar_one()
    assert "今天必须给我处理结果" in ticket_row.summary
    event_count = db_session.execute(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "handoff_summary_refreshed"
        )
    ).scalar_one()
    assert event_count == 1


def test_active_ticket_is_reused_then_new_ticket_after_resolve(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    first = _human_request(api_client)
    second = _human_request(api_client, first["conversation_id"])
    assert second["handoff"]["ticket_ref"] == first["handoff"]["ticket_ref"]

    db_session.expire_all()
    active_count = db_session.execute(
        select(func.count(HandoffTicket.id)).where(
            HandoffTicket.status.in_(("open", "claimed"))
        )
    ).scalar_one()
    assert active_count == 1
    created_count = db_session.execute(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.event_type == "handoff_created"
        )
    ).scalar_one()
    assert created_count == 1
    ticket = db_session.execute(select(HandoffTicket)).scalar_one()
    assert ticket.last_user_message == "转人工"

    admin_login()
    ticket_id = ticket.id
    assert (
        api_client.post(
            f"/api/admin/handoffs/{ticket_id}/resolve", json={"note": ""}
        ).status_code
        == 200
    )

    third = _human_request(api_client, first["conversation_id"])
    assert third["handoff"]["ticket_ref"] != first["handoff"]["ticket_ref"]


def test_proposal_queue_does_not_duplicate_handoff_ticket(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "handoff-proposal-0001")
    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款"}
    )
    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert body["proposals"][0]["status"] == "pending"
    assert body["handoff"]["ticket_ref"] is None

    admin_login()
    listed = api_client.get("/api/admin/handoffs")
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert db_session.execute(select(HandoffTicket)).scalars().all() == []


def test_another_staff_cannot_steal_claimed_ticket(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    chat = _human_request(api_client)
    admin_login()
    listed = api_client.get("/api/admin/handoffs")
    ticket_id = listed.json()["items"][0]["id"]
    assert api_client.post(f"/api/admin/handoffs/{ticket_id}/claim").status_code == 200

    with TestClient(app) as other:
        login_response = other.post(
            "/api/admin/session",
            json={
                "email": "agent@test.local",
                "password": settings.demo_agent_password,
            },
        )
        assert login_response.status_code == 200, login_response.text
        conflict = other.post(f"/api/admin/handoffs/{ticket_id}/claim")
        assert conflict.status_code == 409
        resolve_conflict = other.post(
            f"/api/admin/handoffs/{ticket_id}/resolve", json={"note": ""}
        )
        assert resolve_conflict.status_code == 409

    assert (
        api_client.post(
            f"/api/admin/handoffs/{ticket_id}/resolve", json={"note": "店主完成"}
        ).status_code
        == 200
    )
    assert chat["handoff"]["ticket_ref"].startswith("TK-")


def test_ticket_priority_escalates_and_sorts(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    first = _human_request(api_client)

    complaint = api_client.post(
        "/api/chat",
        json={"text": "我要投诉，物流太慢", "conversation_id": first["conversation_id"]},
    )
    assert complaint.status_code == 200, complaint.text
    assert complaint.json()["handoff"]["ticket_ref"] == first["handoff"]["ticket_ref"]

    high = api_client.post(
        "/api/chat", json={"text": "你能保证退款吗"}
    )
    assert high.status_code == 200, high.text
    assert high.json()["handoff"]["ticket_ref"] != first["handoff"]["ticket_ref"]

    db_session.expire_all()
    tickets = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id)
    ).scalars().all()
    assert [ticket.priority for ticket in tickets] == ["urgent", "high"]
    escalation = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "handoff_priority_escalated"
        )
    ).scalar_one()
    assert escalation.payload_json["from"] == "normal"
    assert escalation.payload_json["to"] == "urgent"

    admin_login()
    listed = api_client.get("/api/admin/handoffs")
    assert listed.status_code == 200, listed.text
    assert [row["priority"] for row in listed.json()["items"]] == ["urgent", "high"]
    urgent = api_client.get("/api/admin/handoffs?priority=urgent")
    assert urgent.status_code == 200, urgent.text
    assert len(urgent.json()["items"]) == 1
    assert urgent.json()["items"][0]["priority"] == "urgent"
    assert api_client.get("/api/admin/overview").json()["urgent_handoffs"] == 1
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_owner_assigns_ticket_and_notifies_assignee(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    chat = _human_request(api_client)
    ticket_ref = chat["handoff"]["ticket_ref"]
    admin_login()
    ticket = next(
        item
        for item in api_client.get("/api/admin/handoffs").json()["items"]
        if item["ticket_ref"] == ticket_ref
    )
    agent = next(
        item
        for item in api_client.get("/api/admin/staff").json()
        if item["email"] == "agent@test.local"
    )
    assigned = api_client.post(
        f"/api/admin/handoffs/{ticket['id']}/assign",
        json={"user_id": agent["id"]},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "claimed"
    detail = api_client.get(f"/api/admin/handoffs/{ticket['id']}")
    assert detail.json()["claimed_by_email"] == "agent@test.local"

    db_session.expire_all()
    notifications = db_session.execute(
        select(Notification).where(
            Notification.user_id == agent["id"],
            Notification.type == "handoff_assigned",
        )
    ).scalars().all()
    assert len(notifications) == 1
    outbox = db_session.execute(
        select(Outbox).where(Outbox.aggregate_id == f"user:{agent['id']}")
    ).scalars().all()
    assert any("已指派" in row.payload_json.get("subject", "") for row in outbox)
    assigned_event = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "handoff_assigned")
    ).scalar_one()
    assert assigned_event.payload_json["assignee_user_id"] == agent["id"]

    admin_login("agent@test.local", "agent123456")
    assert (
        api_client.post(
            f"/api/admin/handoffs/{ticket['id']}/assign",
            json={"user_id": agent["id"]},
        ).status_code
        == 403
    )
    resolved = api_client.post(
        f"/api/admin/handoffs/{ticket['id']}/resolve",
        json={"note": "被指派客服已处理"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"


def test_admin_searches_handoff_tickets(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    body = _human_request(api_client)
    ticket_ref = body["handoff"]["ticket_ref"]
    admin_login()

    by_ref = api_client.get(
        "/api/admin/handoffs",
        params={"status": "active", "q": ticket_ref},
    )
    assert by_ref.status_code == 200, by_ref.text
    assert any(row["ticket_ref"] == ticket_ref for row in by_ref.json()["items"])

    by_phone = api_client.get(
        "/api/admin/handoffs",
        params={"status": "active", "q": "13800000001"},
    )
    assert by_phone.status_code == 200, by_phone.text
    assert any(row["ticket_ref"] == ticket_ref for row in by_phone.json()["items"])

    missing = api_client.get(
        "/api/admin/handoffs",
        params={"status": "active", "q": "不存在的工单"},
    )
    assert missing.status_code == 200, missing.text
    assert missing.json()["items"] == []
