from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import ActionProposal, AuditEvent, HandoffTicket, Notification

ADDRESS = {
    "recipient_name": "SLA 测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "SLA 测试路 1 号",
}


def _paid_order(client: TestClient) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "sla-order-0001"},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code
        == 200
    )
    return body["order_no"]


def test_sla_reminders_are_sent_once(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client)
    refund = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款"}
    )
    assert refund.status_code == 200
    human = api_client.post("/api/chat", json={"text": "转人工"})
    assert human.status_code == 200

    past = datetime.now(UTC) - timedelta(hours=2)
    proposal = db_session.execute(select(ActionProposal)).scalar_one()
    ticket = db_session.execute(select(HandoffTicket)).scalar_one()
    proposal.created_at = past
    ticket.created_at = past
    db_session.commit()

    from app.services.sla import send_sla_reminders

    first = send_sla_reminders(
        db_session, proposal_minutes=30, handoff_minutes=15
    )
    assert first == {
        "proposals": 1,
        "handoffs": 1,
        "handoff_resolutions": 0,
        "handoff_first_responses": 0,
        "handoff_escalations": 0,
    }
    db_session.expire_all()
    types = [
        row.type
        for row in db_session.execute(
            select(Notification).where(
                Notification.type.in_(("proposal_sla", "handoff_sla"))
            )
        ).scalars().all()
    ]
    assert types.count("proposal_sla") == 2
    assert types.count("handoff_sla") == 2
    assert len(
        db_session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type.in_(
                    ("proposal_sla_reminder", "handoff_sla_reminder")
                )
            )
        ).scalars().all()
    ) == 2

    second = send_sla_reminders(
        db_session, proposal_minutes=30, handoff_minutes=15
    )
    assert second == {
        "proposals": 0,
        "handoffs": 0,
        "handoff_resolutions": 0,
        "handoff_first_responses": 0,
        "handoff_escalations": 0,
    }


def test_sla_reminders_follow_ticket_priority(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    assert api_client.post("/api/chat", json={"text": "转人工"}).status_code == 200
    assert (
        api_client.post("/api/chat", json={"text": "你能保证退款吗"}).status_code
        == 200
    )
    assert (
        api_client.post("/api/chat", json={"text": "我要投诉"}).status_code
        == 200
    )

    past = datetime.now(UTC) - timedelta(minutes=6)
    for ticket in db_session.execute(select(HandoffTicket)).scalars().all():
        ticket.created_at = past
    db_session.commit()

    from app.services.sla import send_sla_reminders

    first = send_sla_reminders(
        db_session,
        proposal_minutes=30,
        handoff_minutes=15,
        handoff_high_minutes=10,
        handoff_urgent_minutes=5,
    )
    assert first == {
        "proposals": 0,
        "handoffs": 1,
        "handoff_resolutions": 0,
        "handoff_first_responses": 0,
        "handoff_escalations": 0,
    }
    db_session.expire_all()
    reminders = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "handoff_sla_reminder"
        )
    ).scalars().all()
    assert [row.payload_json["priority"] for row in reminders] == ["urgent"]

    for ticket in db_session.execute(select(HandoffTicket)).scalars().all():
        if ticket.priority == "high":
            ticket.created_at = datetime.now(UTC) - timedelta(minutes=11)
    db_session.commit()
    second = send_sla_reminders(
        db_session,
        proposal_minutes=30,
        handoff_minutes=15,
        handoff_high_minutes=10,
        handoff_urgent_minutes=5,
    )
    assert second == {
        "proposals": 0,
        "handoffs": 1,
        "handoff_resolutions": 0,
        "handoff_first_responses": 0,
        "handoff_escalations": 0,
    }
    db_session.expire_all()
    reminders = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "handoff_sla_reminder")
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert [row.payload_json["priority"] for row in reminders] == [
        "urgent",
        "high",
    ]


def test_resolution_sla_reminds_claimed_ticket_once(
    api_client: TestClient,
    shop_data,
    login,
    db_session,
) -> None:
    from app.domain.models import User

    login()
    assert api_client.post("/api/chat", json={"text": "转人工"}).status_code == 200
    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    ticket = db_session.execute(select(HandoffTicket)).scalar_one()
    ticket.status = "claimed"
    ticket.claimed_by = agent.id
    ticket.claimed_at = datetime.now(UTC) - timedelta(hours=25)
    db_session.commit()

    from app.services.sla import send_sla_reminders

    first = send_sla_reminders(
        db_session,
        proposal_minutes=30,
        handoff_minutes=15,
        handoff_resolution_hours=24,
    )
    assert first == {
        "proposals": 0,
        "handoffs": 0,
        "handoff_resolutions": 1,
        "handoff_first_responses": 0,
        "handoff_escalations": 0,
    }
    db_session.expire_all()
    notes = db_session.execute(
        select(Notification).where(
            Notification.type == "handoff_resolution_sla"
        )
    ).scalars().all()
    assert len(notes) == 2
    audits = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "handoff_resolution_sla_reminder"
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload_json["waited_hours"] >= 25

    second = send_sla_reminders(
        db_session,
        proposal_minutes=30,
        handoff_minutes=15,
        handoff_resolution_hours=24,
    )
    assert second == {
        "proposals": 0,
        "handoffs": 0,
        "handoff_resolutions": 0,
        "handoff_first_responses": 0,
        "handoff_escalations": 0,
    }


def test_first_response_sla_reminds_assignee_once(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    first = api_client.post("/api/chat", json={"text": "转人工"})
    assert first.status_code == 200, first.text
    admin_login("agent@test.local", "agent123456")
    ticket = db_session.execute(
        select(HandoffTicket).where(
            HandoffTicket.conversation_id == first.json()["conversation_id"]
        )
    ).scalar_one()
    assert api_client.post(f"/api/admin/handoffs/{ticket.id}/claim").status_code == 200
    now = datetime.now(UTC)
    ticket.created_at = now - timedelta(minutes=40)
    ticket.claimed_at = now - timedelta(minutes=35)
    db_session.commit()

    from app.services.sla import send_sla_reminders

    stats = send_sla_reminders(
        db_session, handoff_first_response_minutes=30
    )
    assert stats == {
        "proposals": 0,
        "handoffs": 0,
        "handoff_resolutions": 0,
        "handoff_first_responses": 1,
        "handoff_escalations": 0,
    }
    db_session.expire_all()
    notes = db_session.execute(
        select(Notification).where(
            Notification.type == "handoff_first_response_sla"
        )
    ).scalars().all()
    assert len(notes) == 1
    assert notes[0].recipient_type == "user"
    audits = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "handoff_first_response_sla_reminder"
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload_json["ticket_id"] == ticket.id
    assert audits[0].payload_json["waited_minutes"] >= 40

    again = send_sla_reminders(
        db_session, handoff_first_response_minutes=30
    )
    assert again["handoff_first_responses"] == 0

    second = api_client.post("/api/chat", json={"text": "转人工"})
    assert second.status_code == 200, second.text
    second_ticket = db_session.execute(
        select(HandoffTicket).where(
            HandoffTicket.conversation_id == second.json()["conversation_id"]
        )
    ).scalar_one()
    replied = api_client.post(
        f"/api/admin/handoffs/{second_ticket.id}/messages",
        json={"content": "已收到"},
    )
    assert replied.status_code == 200, replied.text
    second_ticket.created_at = now - timedelta(minutes=40)
    second_ticket.claimed_at = now - timedelta(minutes=35)
    db_session.commit()

    after_reply = send_sla_reminders(
        db_session, handoff_first_response_minutes=30
    )
    assert after_reply["handoff_first_responses"] == 0
    db_session.expire_all()
    assert len(
        db_session.execute(
            select(Notification).where(
                Notification.type == "handoff_first_response_sla"
            )
        ).scalars().all()
    ) == 1


def test_first_response_escalation_notifies_owner_once(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    from app.domain.models import User

    login()
    human = api_client.post("/api/chat", json={"text": "转人工"})
    assert human.status_code == 200, human.text
    admin_login("agent@test.local", "agent123456")
    ticket = db_session.execute(
        select(HandoffTicket).where(
            HandoffTicket.conversation_id == human.json()["conversation_id"]
        )
    ).scalar_one()
    assert (
        api_client.post(f"/api/admin/handoffs/{ticket.id}/claim").status_code
        == 200
    )
    now = datetime.now(UTC)
    ticket.created_at = now - timedelta(minutes=70)
    ticket.claimed_at = now - timedelta(minutes=65)
    db_session.commit()
    owner = db_session.execute(
        select(User).where(User.email == "owner@test.local")
    ).scalar_one()

    from app.services.sla import send_sla_reminders

    stats = send_sla_reminders(
        db_session,
        handoff_first_response_minutes=30,
        handoff_escalation_minutes=60,
    )
    assert stats == {
        "proposals": 0,
        "handoffs": 0,
        "handoff_resolutions": 0,
        "handoff_first_responses": 1,
        "handoff_escalations": 1,
    }
    db_session.expire_all()
    ticket = db_session.get(HandoffTicket, ticket.id)
    assert ticket is not None
    assert ticket.priority == "high"
    assert ticket.escalated_at is not None
    notes = db_session.execute(
        select(Notification).where(
            Notification.type == "handoff_first_response_escalation"
        )
    ).scalars().all()
    assert len(notes) == 1
    assert notes[0].recipient_type == "user"
    assert notes[0].user_id == owner.id
    audit = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "handoff_escalated"
        )
    ).scalars().one()
    assert audit.payload_json["ticket_id"] == ticket.id
    assert audit.payload_json["priority_to"] == "high"
    assert audit.payload_json["owner_user_ids"] == [owner.id]

    again = send_sla_reminders(
        db_session,
        handoff_first_response_minutes=30,
        handoff_escalation_minutes=60,
    )
    assert again["handoff_escalations"] == 0
    assert len(
        db_session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "handoff_escalated"
            )
        ).scalars().all()
    ) == 1
