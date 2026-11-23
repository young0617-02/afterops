from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _stream_data(lines) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ")
    ]


def test_chat_stream_replays_and_resumes(
    api_client: TestClient,
    shop_data,
    login,
) -> None:
    login()
    chat = api_client.post("/api/chat", json={"text": "你好"})
    assert chat.status_code == 200, chat.text
    conversation_id = chat.json()["conversation_id"]
    detail = api_client.get(
        f"/api/chat/conversations/{conversation_id}"
    ).json()
    first_id = detail["messages"][0]["id"]
    last_id = detail["messages"][-1]["id"]

    with api_client.stream(
        "GET",
        f"/api/chat/conversations/{conversation_id}/stream"
        "?after_id=0&wait_seconds=1",
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "text/event-stream"
        )
        payloads = _stream_data(response.iter_lines())
    assert [row["role"] for row in payloads] == ["user", "assistant"]
    assert payloads[-1]["id"] == last_id

    with api_client.stream(
        "GET",
        f"/api/chat/conversations/{conversation_id}/stream"
        f"?after_id={last_id}&wait_seconds=1",
    ) as response:
        assert response.status_code == 200
        assert _stream_data(response.iter_lines()) == []

    with api_client.stream(
        "GET",
        f"/api/chat/conversations/{conversation_id}/stream"
        "?after_id=0&wait_seconds=1",
        headers={"Last-Event-ID": str(first_id)},
    ) as response:
        resumed = _stream_data(response.iter_lines())
    assert [row["id"] for row in resumed] == [last_id]
    assert resumed[0]["role"] == "assistant"


def test_chat_stream_requires_login_and_ownership(
    api_client: TestClient,
    shop_data,
    login,
    otp_login,
) -> None:
    login("13800000031")
    chat = api_client.post("/api/chat", json={"text": "你好"})
    conversation_id = chat.json()["conversation_id"]

    api_client.delete("/api/store/session")
    with api_client.stream(
        "GET",
        f"/api/chat/conversations/{conversation_id}/stream"
        "?after_id=0&wait_seconds=1",
    ) as response:
        assert response.status_code == 401

    otp_login(api_client, "13900000032")
    with api_client.stream(
        "GET",
        f"/api/chat/conversations/{conversation_id}/stream"
        "?after_id=0&wait_seconds=1",
    ) as response:
        assert response.status_code == 404

    invalid = api_client.get(
        f"/api/chat/conversations/{conversation_id}/stream"
        "?after_id=-1&wait_seconds=1"
    )
    assert invalid.status_code == 422
