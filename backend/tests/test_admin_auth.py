from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.passwords import hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert hashed.startswith("$argon2")
    assert verify_password("secret123", hashed) is True
    assert verify_password("wrongpass", hashed) is False


def test_admin_me_and_logout(api_client: TestClient, shop_data, admin_login) -> None:
    admin_login()
    me = api_client.get("/api/admin/me")
    assert me.status_code == 200
    assert me.json()["role"] == "owner"

    assert api_client.delete("/api/admin/session").status_code == 200
    assert api_client.get("/api/admin/me").status_code == 401


def test_admin_login_rate_limited(api_client: TestClient, shop_data) -> None:
    for _ in range(5):
        resp = api_client.post(
            "/api/admin/session",
            json={"email": "owner@test.local", "password": "wrongpass"},
        )
        assert resp.status_code == 401
    blocked = api_client.post(
        "/api/admin/session",
        json={"email": "owner@test.local", "password": "wrongpass"},
    )
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after")
