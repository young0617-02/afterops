from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_api_security_headers_and_origin_guard(api_client, shop_data) -> None:
    health = api_client.get("/health")
    assert health.status_code == 200
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in health.headers["content-security-policy"]

    evil = api_client.post(
        "/api/store/otp/request",
        json={"phone": "13800000001"},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403
    assert evil.json()["code"] == "origin_not_allowed"
    assert evil.headers["x-frame-options"] == "DENY"

    allowed = api_client.post(
        "/api/store/otp/request",
        json={"phone": "13800000001"},
        headers={"Origin": "http://127.0.0.1:3000"},
    )
    assert allowed.status_code == 202

    read = api_client.get(
        "/health", headers={"Origin": "https://evil.example"}
    )
    assert read.status_code == 200


def test_health_headers_with_headers_middleware(api_client) -> None:
    response = api_client.get("/health")
    assert "permissions-policy" in response.headers
    assert "referrer-policy" in response.headers


def test_production_settings_reject_default_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="prod")

    strong = Settings(
        app_env="prod",
        session_secret="s" * 40,
        mock_psp_secret="p" * 24,
        audit_anchor_secret="a" * 40,
        demo_owner_password="owner-prod-password",
        demo_agent_password="agent-prod-password",
    )
    assert strong.app_env == "prod"
    assert "http://127.0.0.1:3000" in strong.allowed_origin_set
