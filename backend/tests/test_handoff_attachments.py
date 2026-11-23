from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Attachment, AuditEvent
from app.services.audit import verify_chain
from app.settings import settings

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _human_request(client: TestClient) -> dict:
    response = client.post("/api/chat", json={"text": "转人工"})
    assert response.status_code == 200, response.text
    return response.json()


def _ticket_id(client: TestClient, ticket_ref: str) -> int:
    items = client.get("/api/admin/handoffs?status=all").json()["items"]
    return next(item["id"] for item in items if item["ticket_ref"] == ticket_ref)


def _upload_ticket_image(
    client: TestClient, ticket_id: int, name: str = "reply.png"
) -> int:
    response = client.post(
        f"/api/admin/handoffs/{ticket_id}/attachments",
        files={"file": (name, PNG, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_agent_replies_with_images_and_customer_reads_them(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    chat = _human_request(api_client)
    ticket_ref = chat["handoff"]["ticket_ref"]
    admin_login()
    ticket_id = _ticket_id(api_client, ticket_ref)
    first = _upload_ticket_image(api_client, ticket_id, "first.png")
    second = _upload_ticket_image(api_client, ticket_id, "second.png")

    replied = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"content": "请按图示包装后寄回", "attachment_ids": [first, second]},
    )
    assert replied.status_code == 200, replied.text
    assert replied.json()["content"] == "请按图示包装后寄回"

    image_only = api_client.post(
        f"/api/admin/handoffs/{ticket_id}/messages",
        json={"attachment_ids": [first]},
    )
    assert image_only.status_code == 200, image_only.text
    assert image_only.json()["content"] == ""

    detail = api_client.get(f"/api/admin/handoffs/{ticket_id}")
    assert detail.status_code == 200, detail.text
    agent_messages = [
        message
        for message in detail.json()["messages"]
        if message["role"] == "agent"
    ]
    assert agent_messages[0]["content"] == "请按图示包装后寄回"
    assert agent_messages[0]["image_urls"] == [
        f"/api/admin/attachments/{first}",
        f"/api/admin/attachments/{second}",
    ]
    assert agent_messages[1]["content"] == ""
    assert agent_messages[1]["image_urls"] == [
        f"/api/admin/attachments/{first}"
    ]

    conversation = api_client.get(
        f"/api/chat/conversations/{chat['conversation_id']}"
    ).json()
    customer_agent_messages = [
        message
        for message in conversation["messages"]
        if message["role"] == "agent"
    ]
    assert customer_agent_messages[0]["image_urls"] == [
        f"/api/store/attachments/{first}",
        f"/api/store/attachments/{second}",
    ]
    image = api_client.get(customer_agent_messages[0]["image_urls"][0])
    assert image.status_code == 200
    assert image.content == PNG

    db_session.expire_all()
    stored = db_session.execute(
        select(Attachment).where(Attachment.id == first)
    ).scalar_one()
    assert stored.uploaded_by_user_id is not None
    upload_audit = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "handoff_attachment_uploaded"
        )
    ).scalars().all()
    assert len(upload_audit) == 2
    reply_audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "handoff_agent_replied")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert reply_audit is not None
    assert reply_audit.payload_json["attachment_count"] == 1
    assert reply_audit.payload_json["length"] == 0
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_handoff_attachment_permissions_and_validation(
    api_client: TestClient,
    shop_data,
    login,
    otp_login,
    admin_login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login("13800000001")
    first_chat = _human_request(api_client)

    api_client.delete("/api/store/session")
    otp_login(api_client, "13900000002")
    second_chat = _human_request(api_client)
    second_ref = second_chat["handoff"]["ticket_ref"]
    customer_upload = api_client.post(
        "/api/store/attachments",
        files={"file": ("mine.png", PNG, "image/png")},
    ).json()["id"]

    api_client.delete("/api/store/session")
    admin_login()
    first_id = _ticket_id(api_client, first_chat["handoff"]["ticket_ref"])
    second_id = _ticket_id(api_client, second_ref)
    foreign = _upload_ticket_image(api_client, first_id, "foreign.png")

    cross_ticket = api_client.post(
        f"/api/admin/handoffs/{second_id}/messages",
        json={"content": "附件发错", "attachment_ids": [foreign]},
    )
    assert cross_ticket.status_code == 422
    assert "不是员工上传" in cross_ticket.json()["detail"]

    customer_image = api_client.post(
        f"/api/admin/handoffs/{second_id}/messages",
        json={"content": "回传顾客图片", "attachment_ids": [customer_upload]},
    )
    assert customer_image.status_code == 422
    assert "不是员工上传" in customer_image.json()["detail"]

    both_empty = api_client.post(
        f"/api/admin/handoffs/{second_id}/messages",
        json={"content": "   "},
    )
    assert both_empty.status_code == 422

    too_many = api_client.post(
        f"/api/admin/handoffs/{second_id}/messages",
        json={"attachment_ids": [1, 2, 3, 4]},
    )
    assert too_many.status_code == 422

    missing = api_client.post(
        "/api/admin/handoffs/999999/attachments",
        files={"file": ("reply.png", PNG, "image/png")},
    )
    assert missing.status_code == 404

    resolved = api_client.post(
        f"/api/admin/handoffs/{second_id}/resolve",
        json={"note": "测试解决"},
    )
    assert resolved.status_code == 200, resolved.text
    after_resolve = api_client.post(
        f"/api/admin/handoffs/{second_id}/attachments",
        files={"file": ("late.png", PNG, "image/png")},
    )
    assert after_resolve.status_code == 409
