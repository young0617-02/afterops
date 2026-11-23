from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent


def _publish(
    client: TestClient,
    *,
    entry_id: str = "POLICY-RETURN",
    topic: str = "return",
    title: str = "15 天无理由退货",
    body: str = "签收后 15 天内可申请退货，质检后由人工核定退款。",
    source: str = "商家后台",
    valid_from: str | None = None,
):
    payload = {
        "entry_id": entry_id,
        "topic": topic,
        "title": title,
        "body": body,
        "source": source,
    }
    if valid_from is not None:
        payload["valid_from"] = valid_from
    return client.post("/api/admin/policies", json=payload)


def test_policy_groups_and_permissions(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    assert api_client.get("/api/admin/policies").status_code == 401

    admin_login()
    response = api_client.get("/api/admin/policies")
    assert response.status_code == 200, response.text
    groups = {group["topic"]: group for group in response.json()}
    assert set(groups) == {"refund_time", "return"}
    assert groups["return"]["current"]["version"] == "v2"
    assert groups["return"]["current"]["entry_id"] == "POLICY-RETURN"
    assert [row["version"] for row in groups["return"]["versions"]] == ["v2", "v1"]
    assert groups["refund_time"]["current"]["version"] == "v1"

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert api_client.get("/api/admin/policies").status_code == 200
    assert _publish(api_client).status_code == 403


def test_publish_new_version_closes_previous(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    today = date.today()
    published = _publish(api_client)
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["version"] == "v3"
    assert body["valid_from"] == today.isoformat()
    assert body["valid_until"] is None

    current = api_client.get("/api/store/policies/return")
    assert current.status_code == 200, current.text
    assert current.json()["version"] == "v3"
    assert "15 天" in current.json()["body"]

    historical = api_client.get(
        "/api/store/policies/return", params={"as_of": "2026-03-01"}
    )
    assert historical.status_code == 200, historical.text
    assert historical.json()["version"] == "v1"

    groups = {
        group["topic"]: group
        for group in api_client.get("/api/admin/policies").json()
    }
    return_group = groups["return"]
    assert return_group["current"]["version"] == "v3"
    previous = next(
        row for row in return_group["versions"] if row["version"] == "v2"
    )
    assert previous["valid_until"] == today.isoformat()

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "policy_published")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["entry_id"] == "POLICY-RETURN"
    assert audit.payload_json["version"] == "v3"
    assert audit.payload_json["previous_version"] == "v2"


def test_same_day_republish_rejected(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    first = _publish(api_client, title="同日修订 A")
    second = _publish(api_client, title="同日修订 B")
    assert first.status_code == 200, first.text
    assert first.json()["version"] == "v3"
    assert second.status_code == 409
    assert "必须晚于最新版本" in second.json()["detail"]

    current = api_client.get("/api/store/policies/return")
    assert current.json()["version"] == "v3"
    assert "同日修订 A" in current.json()["title"]

    groups = {
        group["topic"]: group
        for group in api_client.get("/api/admin/policies").json()
    }
    versions = {row["version"]: row for row in groups["return"]["versions"]}
    assert versions["v2"]["valid_until"] == date.today().isoformat()


def test_new_entry_topic_conflicts_and_validation(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    created = _publish(
        api_client,
        entry_id="POLICY-WARRANTY",
        topic="warranty",
        title="保修政策",
        body="非人为损坏保修 12 个月。",
    )
    assert created.status_code == 200, created.text
    assert created.json()["version"] == "v1"

    store = api_client.get("/api/store/policies/warranty")
    assert store.status_code == 200, store.text
    assert store.json()["version"] == "v1"

    duplicate_topic = _publish(
        api_client,
        entry_id="POLICY-WARRANTY-2",
        topic="warranty",
        title="重复主题",
        body="重复主题。",
    )
    assert duplicate_topic.status_code == 409
    assert "主题已有政策条目" in duplicate_topic.json()["detail"]

    changed_topic = _publish(api_client, topic="return_extra")
    assert changed_topic.status_code == 409
    assert "主题不可更改" in changed_topic.json()["detail"]

    short_id = _publish(api_client, entry_id="A")
    assert short_id.status_code == 422
    bad_topic = _publish(api_client, entry_id="POLICY-OK", topic="9bad")
    assert bad_topic.status_code == 422


def test_chat_cites_published_version(
    api_client: TestClient,
    shop_data,
    admin_login,
    login,
    db_session,
) -> None:
    admin_login()
    published = _publish(
        api_client,
        title="15 天无理由退货（新版）",
        body="签收后 15 天内可申请退货，质检后由人工核定退款。",
    )
    assert published.status_code == 200, published.text

    login()
    chat = api_client.post("/api/chat", json={"text": "耳机能退吗"})
    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert body["intent"] == "RETURN_POLICY"
    assert "15 天" in body["reply"]
    assert "POLICY-RETURN@v3" in body["reply"]
    assert "POLICY-RETURN@v3" in body["citations"]

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "chat_answered")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert "POLICY-RETURN@v3" in audit.payload_json["citations"]
