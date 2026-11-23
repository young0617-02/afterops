from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Message
from app.main import app
from app.services.audit import verify_chain
from app.settings import settings

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _upload(client: TestClient, name: str = "chat.png") -> int:
    response = client.post(
        "/api/store/attachments",
        files={"file": (name, PNG, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_chat_message_with_images_and_ai_boundary(
    api_client: TestClient,
    shop_data,
    login,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    first = _upload(api_client, "first.png")
    second = _upload(api_client, "second.png")

    response = api_client.post(
        "/api/chat",
        json={
            "text": "耳机有杂音，能退吗",
            "attachment_ids": [first, second],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "无法识别图片" in body["reply"]
    assert "2 张图片" in body["reply"]

    detail = api_client.get(
        f"/api/chat/conversations/{body['conversation_id']}"
    ).json()
    user_message = detail["messages"][0]
    assert user_message["role"] == "user"
    assert user_message["content"] == "耳机有杂音，能退吗"
    assert user_message["image_urls"] == [
        f"/api/store/attachments/{first}",
        f"/api/store/attachments/{second}",
    ]
    image = api_client.get(user_message["image_urls"][0])
    assert image.status_code == 200
    assert image.content == PNG

    db_session.expire_all()
    stored = db_session.execute(
        select(Message)
        .where(Message.conversation_id == body["conversation_id"])
        .order_by(Message.id)
    ).scalars().first()
    assert stored is not None
    assert stored.attachment_ids == [first, second]
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "chat_answered")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["attachment_count"] == 2
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_chat_image_only_reply_and_validation(
    api_client: TestClient,
    shop_data,
    login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    attachment_id = _upload(api_client)

    response = api_client.post(
        "/api/chat",
        json={"text": "", "attachment_ids": [attachment_id]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "ATTACHMENT"
    assert "无法识别图片" in body["reply"]
    assert "转人工" in body["reply"]
    assert body["handoff"]["ticket_ref"] is None

    detail = api_client.get(
        f"/api/chat/conversations/{body['conversation_id']}"
    ).json()
    assert detail["messages"][0]["content"] == ""
    assert detail["messages"][0]["image_urls"] == [
        f"/api/store/attachments/{attachment_id}"
    ]

    empty = api_client.post("/api/chat", json={"text": "   "})
    assert empty.status_code == 422

    too_many = api_client.post(
        "/api/chat",
        json={"text": "看看", "attachment_ids": [1, 2, 3, 4]},
    )
    assert too_many.status_code == 422


def test_chat_rejects_foreign_attachment(
    api_client: TestClient,
    shop_data,
    login,
    otp_login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login("13800000001")
    attachment_id = _upload(api_client)

    with TestClient(app) as other:
        otp_login(other, "13900000002")
        response = other.post(
            "/api/chat",
            json={"text": "我的耳机", "attachment_ids": [attachment_id]},
        )
        assert response.status_code == 422
        assert "不属于当前顾客" in response.json()["detail"]


def test_handoff_thread_exposes_customer_images(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    attachment_id = _upload(api_client, "defect.png")
    chat = api_client.post(
        "/api/chat",
        json={"text": "转人工", "attachment_ids": [attachment_id]},
    )
    assert chat.status_code == 200, chat.text
    ticket_ref = chat.json()["handoff"]["ticket_ref"]
    assert ticket_ref

    admin_login()
    listed = api_client.get("/api/admin/handoffs").json()
    ticket = next(
        item for item in listed["items"] if item["ticket_ref"] == ticket_ref
    )
    detail = api_client.get(f"/api/admin/handoffs/{ticket['id']}")
    assert detail.status_code == 200, detail.text
    user_message = detail.json()["messages"][0]
    assert user_message["content"] == "转人工"
    assert user_message["image_urls"] == [
        f"/api/admin/attachments/{attachment_id}"
    ]
    image = api_client.get(user_message["image_urls"][0])
    assert image.status_code == 200
    assert image.content == PNG
