from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, HandoffTicket


def test_staff_adds_internal_notes_not_visible_to_customer(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    chat = api_client.post("/api/chat", json={"text": "转人工"})
    assert chat.status_code == 200, chat.text
    conversation_id = chat.json()["conversation_id"]
    admin_login("agent@test.local", "agent123456")
    ticket = db_session.execute(select(HandoffTicket)).scalar_one()

    note = api_client.post(
        f"/api/admin/handoffs/{ticket.id}/notes",
        json={"content": "顾客情绪激动，优先回电"},
    )
    assert note.status_code == 200, note.text
    assert note.json()["content"] == "顾客情绪激动，优先回电"
    assert note.json()["author_email"] == "agent@test.local"

    detail = api_client.get(f"/api/admin/handoffs/{ticket.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [row["content"] for row in body["notes"]] == [
        "顾客情绪激动，优先回电"
    ]
    assert [row["role"] for row in body["messages"]] == [
        "user",
        "assistant",
    ]

    customer_view = api_client.get(
        f"/api/chat/conversations/{conversation_id}"
    )
    assert customer_view.status_code == 200, customer_view.text
    contents = [row["content"] for row in customer_view.json()["messages"]]
    assert all("顾客情绪激动，优先回电" not in text for text in contents)

    empty = api_client.post(
        f"/api/admin/handoffs/{ticket.id}/notes",
        json={"content": "   "},
    )
    assert empty.status_code == 422
    too_long = api_client.post(
        f"/api/admin/handoffs/{ticket.id}/notes",
        json={"content": "长" * 1001},
    )
    assert too_long.status_code == 422

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "handoff_note_added")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["ticket_id"] == ticket.id
    assert audit.payload_json["length"] == len("顾客情绪激动，优先回电")

    assert (
        api_client.post(
            f"/api/admin/handoffs/{ticket.id}/resolve",
            json={"note": "已处理"},
        ).status_code
        == 200
    )
    after_resolve = api_client.post(
        f"/api/admin/handoffs/{ticket.id}/notes",
        json={"content": "解决后补充记录"},
    )
    assert after_resolve.status_code == 200