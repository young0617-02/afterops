from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import OtpChallenge


def _request_code(client: TestClient, phone: str) -> str:
    resp = client.post("/api/store/otp/request", json={"phone": phone})
    assert resp.status_code == 202, resp.text
    code = resp.json()["debug_code"]
    assert code
    return code


def test_session_required(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_login_and_me(api_client: TestClient, shop_data) -> None:
    code = _request_code(api_client, "13800000001")
    resp = api_client.post(
        "/api/store/session",
        json={"phone": "13800000001", "code": code},
    )
    assert resp.status_code == 200
    assert resp.json()["phone"] == "13800000001"

    me = api_client.get("/api/store/me")
    assert me.status_code == 200
    assert me.json()["phone"] == "13800000001"


def test_invalid_phone(api_client: TestClient, shop_data) -> None:
    resp = api_client.post("/api/store/otp/request", json={"phone": "12345678901"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_wrong_code(api_client: TestClient, shop_data) -> None:
    code = _request_code(api_client, "13800000001")
    wrong = "000000" if code != "000000" else "111111"
    resp = api_client.post(
        "/api/store/session",
        json={"phone": "13800000001", "code": wrong},
    )
    assert resp.status_code == 401


def test_code_cannot_be_reused(api_client: TestClient, shop_data) -> None:
    code = _request_code(api_client, "13800000001")
    first = api_client.post(
        "/api/store/session", json={"phone": "13800000001", "code": code}
    )
    assert first.status_code == 200
    second = api_client.post(
        "/api/store/session", json={"phone": "13800000001", "code": code}
    )
    assert second.status_code == 401


def test_expired_code(
    api_client: TestClient, shop_data, db_session
) -> None:
    code = _request_code(api_client, "13800000001")
    challenge = db_session.execute(
        select(OtpChallenge).order_by(OtpChallenge.id.desc()).limit(1)
    ).scalar_one()
    challenge.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    resp = api_client.post(
        "/api/store/session", json={"phone": "13800000001", "code": code}
    )
    assert resp.status_code == 401


def test_otp_request_rate_limited(api_client: TestClient, shop_data) -> None:
    phone = "13700000011"
    for _ in range(5):
        resp = api_client.post("/api/store/otp/request", json={"phone": phone})
        assert resp.status_code == 202
    blocked = api_client.post("/api/store/otp/request", json={"phone": phone})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert blocked.headers.get("retry-after")


def test_login_rate_limited(api_client: TestClient, shop_data) -> None:
    phone = "13700000012"
    _request_code(api_client, phone)
    for _ in range(10):
        resp = api_client.post(
            "/api/store/session", json={"phone": phone, "code": "999999"}
        )
        assert resp.status_code in (401, 429)
    blocked = api_client.post(
        "/api/store/session", json={"phone": phone, "code": "999999"}
    )
    assert blocked.status_code == 429


def test_logout(api_client: TestClient, shop_data, login) -> None:
    login()
    assert api_client.delete("/api/store/session").status_code == 200
    assert api_client.get("/api/store/me").status_code == 401
