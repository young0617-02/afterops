from __future__ import annotations

from fastapi.testclient import TestClient


def test_current_policy(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/policies/return")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "v2"
    assert body["valid_until"] is None
    assert "人工核定" in body["body"]


def test_historical_policy(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/policies/return", params={"as_of": "2026-03-01"})
    assert resp.status_code == 200
    assert resp.json()["version"] == "v1"


def test_policy_not_valid_at_time(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/policies/return", params={"as_of": "2024-01-01"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_unknown_policy_topic(api_client: TestClient, shop_data) -> None:
    assert api_client.get("/api/store/policies/nope").status_code == 404


def test_list_current_policies(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/policies")
    assert resp.status_code == 200
    topics = {row["topic"]: row["version"] for row in resp.json()}
    assert topics == {"refund_time": "v1", "return": "v2"}
