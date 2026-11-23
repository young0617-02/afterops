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


def _seed_tickets(db_session, shop_data) -> None:
    merchant = shop_data["merchant"]
    owner = db_session.execute(
        select(User).where(User.email == "owner@test.local")
    ).scalar_one()
    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    customer = Customer(merchant_id=merchant.id, phone="13800000009")
    db_session.add(customer)
    db_session.flush()
    conversation = Conversation(
        merchant_id=merchant.id,
        customer_id=customer.id,
        channel="web",
        status="handoff",
    )
    db_session.add(conversation)
    db_session.flush()
    now = datetime.now(UTC)

    def ticket(
        *,
        created_minutes: int,
        claimed_by: int | None = None,
        claimed_minutes: int | None = None,
        resolved_by: int | None = None,
        resolved_minutes: int | None = None,
        priority: str = "normal",
        status: str = "claimed",
    ) -> HandoffTicket:
        return HandoffTicket(
            merchant_id=merchant.id,
            conversation_id=conversation.id,
            customer_id=customer.id,
            queue="after_sales",
            status=status,
            priority=priority,
            intent="REFUND_REQUEST",
            last_user_message="测试工单",
            last_reply="已转人工",
            claimed_by=claimed_by,
            claimed_at=(
                now - timedelta(minutes=claimed_minutes)
                if claimed_minutes is not None
                else None
            ),
            resolved_by=resolved_by,
            resolved_at=(
                now - timedelta(minutes=resolved_minutes)
                if resolved_minutes is not None
                else None
            ),
            created_at=now - timedelta(minutes=created_minutes),
        )

    first = ticket(
        created_minutes=30,
        claimed_by=agent.id,
        claimed_minutes=20,
        resolved_by=agent.id,
        resolved_minutes=5,
        status="resolved",
    )
    urgent = ticket(
        created_minutes=40,
        claimed_by=agent.id,
        claimed_minutes=25,
        priority="urgent",
    )
    unassigned = ticket(created_minutes=10, status="open")
    owner_ticket = ticket(
        created_minutes=5,
        claimed_by=owner.id,
        claimed_minutes=1,
    )
    db_session.add_all([first, urgent, unassigned, owner_ticket])
    db_session.flush()
    for item, user, replied_minutes in (
        (first, agent, 15),
        (urgent, agent, 5),
        (owner_ticket, owner, 3),
    ):
        db_session.add(
            AuditEvent(
                merchant_id=merchant.id,
                conversation_id=conversation.id,
                event_type="handoff_agent_replied",
                payload_json={
                    "ticket_id": item.id,
                    "operator_user_id": user.id,
                },
                prev_hash="0" * 64,
                row_hash="0" * 64,
                created_at=now - timedelta(minutes=replied_minutes),
            )
        )
    db_session.commit()


def test_agent_performance_metrics(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    _seed_tickets(db_session, shop_data)
    admin_login()
    response = api_client.get("/api/admin/agent-performance")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hours"] == 168
    assert body["unassigned_open"] == 1

    rows = {row["email"]: row for row in body["agents"]}
    agent = rows["agent@test.local"]
    assert agent["open_tickets"] == 1
    assert agent["claimed"] == 2
    assert agent["resolved"] == 1
    assert agent["avg_claim_minutes"] == 12.5
    assert agent["avg_resolution_minutes"] == 25.0
    assert agent["first_responses"] == 2
    assert agent["avg_first_response_minutes"] == 25.0
    assert agent["first_response_sla_breaches"] == 1
    assert agent["sla_breaches"] == 1

    owner = rows["owner@test.local"]
    assert owner["open_tickets"] == 1
    assert owner["claimed"] == 1
    assert owner["resolved"] == 0
    assert owner["avg_claim_minutes"] == 4.0
    assert owner["avg_resolution_minutes"] is None
    assert owner["first_responses"] == 1
    assert owner["avg_first_response_minutes"] == 2.0
    assert owner["first_response_sla_breaches"] == 0
    assert owner["sla_breaches"] == 0


def test_agent_performance_permissions_and_window(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    _seed_tickets(db_session, shop_data)
    admin_login()
    assert api_client.get(
        "/api/admin/agent-performance", params={"hours": 0}
    ).status_code == 422
    assert api_client.get(
        "/api/admin/agent-performance", params={"hours": 721}
    ).status_code == 422

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert api_client.get("/api/admin/agent-performance").status_code == 403


def test_resolution_sla_breach_counted(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    _seed_tickets(db_session, shop_data)
    agent = db_session.execute(
        select(User).where(User.email == "agent@test.local")
    ).scalar_one()
    base = db_session.execute(select(HandoffTicket)).scalars().first()
    now = datetime.now(UTC)
    db_session.add(
        HandoffTicket(
            merchant_id=base.merchant_id,
            conversation_id=base.conversation_id,
            customer_id=base.customer_id,
            queue="after_sales",
            status="resolved",
            priority="normal",
            intent="REFUND_REQUEST",
            last_user_message="长时间工单",
            last_reply="已解决",
            claimed_by=agent.id,
            claimed_at=now - timedelta(hours=29),
            resolved_by=agent.id,
            resolved_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=30),
        )
    )
    db_session.commit()

    admin_login()
    body = api_client.get(
        "/api/admin/agent-performance", params={"hours": 168}
    ).json()
    rows = {row["email"]: row for row in body["agents"]}
    assert rows["agent@test.local"]["resolution_sla_breaches"] == 1
    assert rows["owner@test.local"]["resolution_sla_breaches"] == 0
