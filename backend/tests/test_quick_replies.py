from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent


def _create(client: TestClient, title: str, content: str, category: str = ""):
    return client.post(
        "/api/admin/quick-replies",
        json={"title": title, "content": content, "category": category},
    )


def test_owner_manages_quick_replies(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    first = _create(api_client, "物流安抚", "您好，正在为您核实物流。", "物流")
    assert first.status_code == 200, first.text
    second = _create(api_client, "退货说明", "退货请先提交申请并保留凭证。")
    assert second.status_code == 200, second.text

    duplicate = _create(api_client, "物流安抚", "重复标题")
    assert duplicate.status_code == 409
    invalid = _create(api_client, "", "内容")
    assert invalid.status_code == 422

    active = api_client.get("/api/admin/quick-replies")
    assert active.status_code == 200
    assert [row["title"] for row in active.json()] == ["退货说明", "物流安抚"]

    updated = api_client.patch(
        f"/api/admin/quick-replies/{first.json()['id']}",
        json={"content": "您好，物流已核实，请稍等。", "status": "disabled"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "disabled"
    assert updated.json()["content"] == "您好，物流已核实，请稍等。"

    active = api_client.get("/api/admin/quick-replies").json()
    assert [row["title"] for row in active] == ["退货说明"]
    all_rows = api_client.get(
        "/api/admin/quick-replies", params={"include_disabled": True}
    ).json()
    assert [row["title"] for row in all_rows] == ["退货说明", "物流安抚"]

    events = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type.like("quick_reply_%")
        )
    ).scalars().all()
    assert events == [
        "quick_reply_created",
        "quick_reply_created",
        "quick_reply_updated",
    ]


def test_agent_can_read_active_but_cannot_manage(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    assert _create(api_client, "售后安抚", "已收到，正在处理。").status_code == 200
    disabled = _create(api_client, "旧话术", "旧内容。")
    assert disabled.status_code == 200
    assert (
        api_client.patch(
            f"/api/admin/quick-replies/{disabled.json()['id']}",
            json={"status": "disabled"},
        ).status_code
        == 200
    )

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    active = api_client.get("/api/admin/quick-replies")
    assert active.status_code == 200
    assert [row["title"] for row in active.json()] == ["售后安抚"]
    all_rows = api_client.get(
        "/api/admin/quick-replies", params={"include_disabled": True}
    )
    assert [row["title"] for row in all_rows.json()] == ["售后安抚"]

    assert _create(api_client, "客服新建", "不应成功").status_code == 403
    patch = api_client.patch(
        f"/api/admin/quick-replies/{active.json()[0]['id']}",
        json={"content": "不应成功"},
    )
    assert patch.status_code == 403
    assert api_client.get("/api/admin/quick-replies").status_code == 200