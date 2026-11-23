from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, User
from app.main import app
from app.services.audit import verify_chain


def test_owner_manages_staff_accounts(
    api_client: TestClient, shop_data, admin_login, db_session
) -> None:
    admin_login()
    listed = api_client.get("/api/admin/staff")
    assert listed.status_code == 200, listed.text
    assert {row["email"] for row in listed.json()} == {
        "owner@test.local",
        "agent@test.local",
    }
    owner_id = next(
        row["id"] for row in listed.json() if row["role"] == "owner"
    )

    created = api_client.post(
        "/api/admin/staff",
        json={
            "email": "new-agent@test.local",
            "password": "newagent123",
            "role": "agent",
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["id"]
    assert created.json()["role"] == "agent"
    duplicate = api_client.post(
        "/api/admin/staff",
        json={
            "email": "new-agent@test.local",
            "password": "newagent123",
            "role": "agent",
        },
    )
    assert duplicate.status_code == 409

    with TestClient(app) as agent_client:
        login = agent_client.post(
            "/api/admin/session",
            json={"email": "new-agent@test.local", "password": "newagent123"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["role"] == "agent"
        assert agent_client.get("/api/admin/staff").status_code == 403

        disabled = api_client.patch(
            f"/api/admin/staff/{agent_id}", json={"status": "disabled"}
        )
        assert disabled.status_code == 200, disabled.text
        blocked = agent_client.post(
            "/api/admin/session",
            json={"email": "new-agent@test.local", "password": "newagent123"},
        )
        assert blocked.status_code == 401

    assert (
        api_client.patch(
            f"/api/admin/staff/{agent_id}", json={"status": "active"}
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/admin/staff/{agent_id}/password",
            json={"password": "resetpass123"},
        ).status_code
        == 200
    )
    with TestClient(app) as agent_client:
        old = agent_client.post(
            "/api/admin/session",
            json={"email": "new-agent@test.local", "password": "newagent123"},
        )
        assert old.status_code == 401
        new = agent_client.post(
            "/api/admin/session",
            json={"email": "new-agent@test.local", "password": "resetpass123"},
        )
        assert new.status_code == 200

    self_disable = api_client.patch(
        f"/api/admin/staff/{owner_id}", json={"status": "disabled"}
    )
    assert self_disable.status_code == 409
    self_demote = api_client.patch(
        f"/api/admin/staff/{owner_id}", json={"role": "agent"}
    )
    assert self_demote.status_code == 409

    db_session.expire_all()
    events = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type.like("staff_%"))
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert [event.event_type for event in events] == [
        "staff_created",
        "staff_updated",
        "staff_updated",
        "staff_password_reset",
    ]
    staff = db_session.execute(
        select(User).where(User.email == "new-agent@test.local")
    ).scalar_one()
    assert staff.role == "agent"
    assert staff.status == "active"
    assert staff.password_hash != "resetpass123"
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")
