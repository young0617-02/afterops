from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, HandoffTicket, Notification, User
from app.settings import settings


def test_auto_assign_balances_active_agents(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    defaults = api_client.get("/api/admin/handoff-settings")
    assert defaults.status_code == 200, defaults.text
    assert defaults.json()["auto_assign_handoffs"] is False

    created = api_client.post(
        "/api/admin/staff",
        json={
            "email": "agent2@test.local",
            "password": "agent123456",
            "role": "agent",
        },
    )
    assert created.status_code == 200, created.text
    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    agent2 = db_session.execute(
        select(User).where(User.email == "agent2@test.local")
    ).scalar_one()

    enabled = api_client.patch(
        "/api/admin/handoff-settings",
        json={"auto_assign_handoffs": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["auto_assign_handoffs"] is True

    admin_login("agent@test.local", "agent123456")
    admin_login("agent2@test.local", "agent123456")
    admin_login()

    first = api_client.post("/api/chat", json={"text": "转人工"})
    assert first.status_code == 200, first.text
    second = api_client.post("/api/chat", json={"text": "转人工"})
    assert second.status_code == 200, second.text
    db_session.expire_all()
    tickets = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id)
    ).scalars().all()
    assert len(tickets) == 2
    assert tickets[0].status == "claimed"
    assert tickets[0].claimed_by == agent.id
    assert tickets[1].status == "claimed"
    assert tickets[1].claimed_by == agent2.id
    assert tickets[0].claimed_at is not None

    events = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "handoff_auto_assigned")
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert [event.payload_json["assignee_user_id"] for event in events] == [
        agent.id,
        agent2.id,
    ]
    assert [event.payload_json["active_tickets"] for event in events] == [
        0,
        0,
    ]
    notes = db_session.execute(
        select(Notification).where(
            Notification.type == "handoff_auto_assigned"
        )
    ).scalars().all()
    assert len(notes) == 2
    assert {note.user_id for note in notes} == {agent.id, agent2.id}

    disabled = api_client.patch(
        "/api/admin/handoff-settings",
        json={"auto_assign_handoffs": False},
    )
    assert disabled.status_code == 200, disabled.text
    third = api_client.post("/api/chat", json={"text": "转人工"})
    assert third.status_code == 200, third.text
    db_session.expire_all()
    latest = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id.desc()).limit(1)
    ).scalar_one()
    assert latest.status == "open"
    assert latest.claimed_by is None


def test_auto_assign_settings_owner_only(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login("agent@test.local", "agent123456")
    assert api_client.get("/api/admin/handoff-settings").status_code == 200
    patch = api_client.patch(
        "/api/admin/handoff-settings",
        json={"auto_assign_handoffs": True},
    )
    assert patch.status_code == 403

def test_auto_assign_skips_unavailable_agents(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    created = api_client.post(
        "/api/admin/staff",
        json={
            "email": "agent3@test.local",
            "password": "agent123456",
            "role": "agent",
        },
    )
    assert created.status_code == 200, created.text
    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    agent3 = db_session.execute(
        select(User).where(User.email == "agent3@test.local")
    ).scalar_one()
    agent.handoff_available = False
    db_session.commit()
    admin_login("agent3@test.local", "agent123456")
    admin_login("agent@test.local", "agent123456")
    admin_login()
    assert (
        api_client.patch(
            "/api/admin/handoff-settings",
            json={"auto_assign_handoffs": True},
        ).status_code
        == 200
    )
    assert api_client.post("/api/chat", json={"text": "转人工"}).status_code == 200

    db_session.expire_all()
    ticket = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id.desc()).limit(1)
    ).scalar_one()
    assert ticket.claimed_by == agent3.id

    agent3.handoff_available = False
    db_session.commit()
    assert api_client.post("/api/chat", json={"text": "转人工"}).status_code == 200
    db_session.expire_all()
    latest = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id.desc()).limit(1)
    ).scalar_one()
    assert latest.status == "open"
    assert latest.claimed_by is None


def test_heartbeat_drives_online_presence(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login("agent@test.local", "agent123456")
    heartbeat = api_client.post("/api/admin/heartbeat")
    assert heartbeat.status_code == 200, heartbeat.text
    body = heartbeat.json()
    assert body["email"] == "agent@test.local"
    assert body["online"] is True
    assert body["last_seen_at"] is not None

    admin_login()
    staff = api_client.get("/api/admin/staff").json()
    agent_row = next(row for row in staff if row["email"] == "agent@test.local")
    assert agent_row["online"] is True
    assert agent_row["handoff_available"] is True

    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    agent.last_seen_at = datetime.now(UTC) - timedelta(
        seconds=settings.agent_online_window_seconds + 5
    )
    db_session.commit()
    stale = api_client.get("/api/admin/staff").json()
    stale_row = next(
        row for row in stale if row["email"] == "agent@test.local"
    )
    assert stale_row["online"] is False

    admin_login("agent@test.local", "agent123456")
    assert api_client.post("/api/admin/heartbeat").status_code == 200
    assert (
        api_client.patch(
            "/api/admin/me/availability", json={"available": False}
        ).status_code
        == 200
    )
    admin_login()
    offline = api_client.get("/api/admin/staff").json()
    offline_row = next(
        row for row in offline if row["email"] == "agent@test.local"
    )
    assert offline_row["handoff_available"] is False
    assert offline_row["online"] is False


def test_agent_skills_route_auto_assignment(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    created = api_client.post(
        "/api/admin/staff",
        json={
            "email": "agent2@test.local",
            "password": "agent123456",
            "role": "agent",
        },
    )
    assert created.status_code == 200, created.text
    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    agent2 = db_session.execute(
        select(User).where(User.email == "agent2@test.local")
    ).scalar_one()

    updated = api_client.patch(
        f"/api/admin/staff/{agent.id}/skills",
        json={"skills": ["complaint"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["handoff_skills"] == ["complaint"]
    staff = api_client.get("/api/admin/staff").json()
    rows = {row["email"]: row for row in staff}
    assert rows["agent@test.local"]["handoff_skills"] == ["complaint"]
    assert rows["agent2@test.local"]["handoff_skills"] == []

    admin_login("agent@test.local", "agent123456")
    assert (
        api_client.patch(
            f"/api/admin/staff/{agent.id}/skills",
            json={"skills": []},
        ).status_code
        == 403
    )
    admin_login("agent2@test.local", "agent123456")
    admin_login()
    invalid = api_client.patch(
        f"/api/admin/staff/{agent.id}/skills",
        json={"skills": ["unknown"]},
    )
    assert invalid.status_code == 422
    owner = db_session.execute(
        select(User).where(User.email == "owner@test.local")
    ).scalar_one()
    owner_rejected = api_client.patch(
        f"/api/admin/staff/{owner.id}/skills",
        json={"skills": []},
    )
    assert owner_rejected.status_code == 422
    assert "只有客服" in owner_rejected.json()["detail"]

    assert (
        api_client.patch(
            "/api/admin/handoff-settings",
            json={"auto_assign_handoffs": True},
        ).status_code
        == 200
    )

    after_sales = api_client.post("/api/chat", json={"text": "转人工"})
    assert after_sales.status_code == 200
    db_session.expire_all()
    ticket = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id.desc()).limit(1)
    ).scalar_one()
    assert ticket.queue == "after_sales"
    assert ticket.claimed_by == agent2.id

    complaint = api_client.post("/api/chat", json={"text": "我要投诉"})
    assert complaint.status_code == 200
    db_session.expire_all()
    ticket = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id.desc()).limit(1)
    ).scalar_one()
    assert ticket.queue == "complaint"
    assert ticket.claimed_by == agent.id

    assert (
        api_client.patch(
            f"/api/admin/staff/{agent2.id}/skills",
            json={"skills": ["complaint"]},
        ).status_code
        == 200
    )
    unmatched = api_client.post("/api/chat", json={"text": "转人工"})
    assert unmatched.status_code == 200
    db_session.expire_all()
    ticket = db_session.execute(
        select(HandoffTicket).order_by(HandoffTicket.id.desc()).limit(1)
    ).scalar_one()
    assert ticket.queue == "after_sales"
    assert ticket.status == "open"
    assert ticket.claimed_by is None


def test_agent_toggles_own_availability(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login("agent@test.local", "agent123456")
    me = api_client.get("/api/admin/me")
    assert me.status_code == 200, me.text
    assert me.json()["handoff_available"] is True

    updated = api_client.patch(
        "/api/admin/me/availability", json={"available": False}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["handoff_available"] is False
    assert (
        api_client.get("/api/admin/me").json()["handoff_available"] is False
    )

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "staff_availability_changed")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["available"] is False
    assert audit.payload_json["email"] == "agent@test.local"
