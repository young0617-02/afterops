from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    AuditEvent,
    Conversation,
    Customer,
    HandoffTicket,
    User,
)


def _seed_ticket(
    db_session,
    shop_data,
    phone: str,
    *,
    resolved: bool = True,
) -> HandoffTicket:
    merchant = shop_data["merchant"]
    customer = db_session.execute(
        select(Customer).where(Customer.phone == phone)
    ).scalar_one()
    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    conversation = Conversation(
        merchant_id=merchant.id,
        customer_id=customer.id,
        channel="web",
        status="handoff",
    )
    db_session.add(conversation)
    db_session.flush()
    now = datetime.now(UTC)
    ticket = HandoffTicket(
        merchant_id=merchant.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        queue="after_sales",
        status="resolved" if resolved else "claimed",
        priority="normal",
        intent="HUMAN_REQUEST",
        last_user_message="请人工处理",
        last_reply="已回访",
        claimed_by=agent.id,
        claimed_at=now - timedelta(minutes=10),
        resolved_by=agent.id if resolved else None,
        resolved_at=now - timedelta(minutes=1) if resolved else None,
        created_at=now - timedelta(minutes=20),
    )
    db_session.add(ticket)
    db_session.commit()
    return ticket


def test_customer_rates_resolved_ticket_and_admin_sees_csat(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    phone = "13800000011"
    login(phone)
    ticket = _seed_ticket(db_session, shop_data, phone)

    rating = api_client.post(
        f"/api/store/handoffs/{ticket.id}/rating",
        json={"score": 5, "comment": "解决很快"},
    )
    assert rating.status_code == 200, rating.text
    assert rating.json()["ticket_ref"] == f"TK-{ticket.id:06d}"
    assert rating.json()["score"] == 5
    assert rating.json()["follow_up_ticket_ref"] is None

    handoffs = api_client.get("/api/store/handoffs").json()
    row = next(item for item in handoffs if item["id"] == ticket.id)
    assert row["rating_score"] == 5
    assert row["rating_comment"] == "解决很快"

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "handoff_rated")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["ticket_id"] == ticket.id
    assert audit.payload_json["score"] == 5

    duplicate = api_client.post(
        f"/api/store/handoffs/{ticket.id}/rating",
        json={"score": 4},
    )
    assert duplicate.status_code == 409
    assert "已评价" in duplicate.json()["detail"]

    admin_login()
    performance = api_client.get(
        "/api/admin/agent-performance", params={"hours": 24}
    )
    assert performance.status_code == 200, performance.text
    body = performance.json()
    assert body["csat_count"] == 1
    assert body["csat_average"] == 5.0
    agent_row = next(
        row
        for row in body["agents"]
        if row["email"] == "agent@test.local"
    )
    assert agent_row["csat_count"] == 1
    assert agent_row["csat_average"] == 5.0
    recent = body["recent_ratings"][0]
    assert recent["ticket_ref"] == f"TK-{ticket.id:06d}"
    assert recent["agent_email"] == "agent@test.local"
    assert recent["comment"] == "解决很快"


def test_rating_requires_resolved_owned_ticket(
    api_client: TestClient,
    shop_data,
    login,
    db_session,
) -> None:
    phone = "13800000012"
    login(phone)
    unresolved = _seed_ticket(
        db_session, shop_data, phone, resolved=False
    )
    response = api_client.post(
        f"/api/store/handoffs/{unresolved.id}/rating",
        json={"score": 5},
    )
    assert response.status_code == 409
    assert "已解决" in response.json()["detail"]

    resolved = _seed_ticket(db_session, shop_data, phone)
    other_phone = "13800000013"
    login(other_phone)
    forbidden = api_client.post(
        f"/api/store/handoffs/{resolved.id}/rating",
        json={"score": 5},
    )
    assert forbidden.status_code == 404

    login(phone)
    invalid = api_client.post(
        f"/api/store/handoffs/{resolved.id}/rating",
        json={"score": 6},
    )
    assert invalid.status_code == 422
    long_comment = api_client.post(
        f"/api/store/handoffs/{resolved.id}/rating",
        json={"score": 5, "comment": "x" * 501},
    )
    assert long_comment.status_code == 422


def test_low_score_notifies_staff_and_creates_follow_up(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    from app.domain.models import Notification

    phone = "13800000016"
    login(phone)
    ticket = _seed_ticket(db_session, shop_data, phone)
    rating = api_client.post(
        f"/api/store/handoffs/{ticket.id}/rating",
        json={"score": 2, "comment": "等待太久"},
    )
    assert rating.status_code == 200, rating.text
    follow_up_ref = rating.json()["follow_up_ticket_ref"]
    assert follow_up_ref
    db_session.expire_all()
    follow_up = db_session.execute(
        select(HandoffTicket).where(
            HandoffTicket.follow_up_for_ticket_id == ticket.id
        )
    ).scalar_one()
    assert follow_up_ref == f"TK-{follow_up.id:06d}"
    assert follow_up.status == "open"
    assert follow_up.queue == "complaint"
    assert follow_up.priority == "high"
    assert follow_up.intent == "LOW_CSAT"
    assert "原工单" in follow_up.last_user_message
    conversation = db_session.get(Conversation, ticket.conversation_id)
    assert conversation is not None
    assert conversation.status == "handoff"
    follow_up_audit = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "low_csat_follow_up_created"
        )
    ).scalars().one()
    assert follow_up_audit.payload_json["follow_up_ticket_id"] == follow_up.id
    assert follow_up_audit.payload_json["rating_id"] > 0

    notes = db_session.execute(
        select(Notification).where(
            Notification.type == "low_csat_alert"
        )
    ).scalars().all()
    assert len(notes) == 2
    alert = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "low_csat_alert"
        )
    ).scalars().one()
    assert alert.payload_json["ticket_id"] == ticket.id
    assert alert.payload_json["score"] == 2

    customer_tickets = api_client.get("/api/store/handoffs").json()
    assert any(row["id"] == follow_up.id for row in customer_tickets)

    admin_login()
    claimed = api_client.post(
        f"/api/admin/handoffs/{follow_up.id}/claim"
    )
    assert claimed.status_code == 200, claimed.text
    replied = api_client.post(
        f"/api/admin/handoffs/{follow_up.id}/messages",
        json={"content": "抱歉让您久等了，我们已安排回访。"},
    )
    assert replied.status_code == 200, replied.text
    customer_tickets = api_client.get("/api/store/handoffs").json()
    row = next(item for item in customer_tickets if item["id"] == follow_up.id)
    assert "已安排回访" in row["last_reply"]
