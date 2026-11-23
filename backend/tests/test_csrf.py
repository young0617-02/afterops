from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.auth import COOKIE_NAME
from app.services.csrf import (
    ADMIN_CSRF_COOKIE,
    CUSTOMER_CSRF_COOKIE,
    csrf_token_for,
)


def _login_with_response(client: TestClient, phone: str = "13800000001"):
    otp = client.post("/api/store/otp/request", json={"phone": phone})
    assert otp.status_code == 202, otp.text
    return client.post(
        "/api/store/session",
        json={"phone": phone, "code": otp.json()["debug_code"]},
    )


def test_login_issues_bound_csrf_cookie(api_client: TestClient, shop_data) -> None:
    response = _login_with_response(api_client)
    assert response.status_code == 200, response.text

    session_token = api_client.cookies.get(COOKIE_NAME)
    csrf_token = api_client.cookies.get(CUSTOMER_CSRF_COOKIE)
    assert session_token and csrf_token
    assert csrf_token == csrf_token_for(session_token)
    assert csrf_token != session_token

    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(row for row in cookies if row.startswith(f"{COOKIE_NAME}="))
    csrf_cookie = next(row for row in cookies if row.startswith(f"{CUSTOMER_CSRF_COOKIE}="))
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie


def test_customer_mutation_requires_csrf(api_client: TestClient, shop_data, login) -> None:
    login()
    api_client.cookies.delete(CUSTOMER_CSRF_COOKIE)

    denied = api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "csrf_token_invalid"

    restored = api_client.get("/api/store/me")
    assert restored.status_code == 200
    assert api_client.cookies.get(CUSTOMER_CSRF_COOKIE)

    allowed = api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    assert allowed.status_code == 200
    assert allowed.json()["items"][0]["sku"] == "SKU-EAR-001"


def test_wrong_csrf_token_rejected(api_client: TestClient, shop_data, login) -> None:
    login()
    forged = api_client.post(
        "/api/store/cart/items",
        json={"sku": "SKU-EAR-001", "qty": 1},
        headers={"X-CSRF-Token": "forged-token"},
    )
    assert forged.status_code == 403
    assert forged.json()["code"] == "csrf_token_invalid"

    token = api_client.cookies.get(CUSTOMER_CSRF_COOKIE)
    allowed = api_client.post(
        "/api/store/cart/items",
        json={"sku": "SKU-EAR-001", "qty": 1},
        headers={"X-CSRF-Token": token},
    )
    assert allowed.status_code == 200


def test_admin_mutation_requires_admin_csrf(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    admin_login()
    api_client.cookies.delete(ADMIN_CSRF_COOKIE)

    payload = {
        "sku": "SKU-CSRF-001",
        "title": "CSRF 测试商品",
        "category": "充电器",
        "brand": "测试",
        "description": "csrf",
        "specs": "csrf",
        "price_cents": 1000,
        "support_7d_no_reason": True,
        "status": "active",
        "initial_qty": 1,
    }
    denied = api_client.post("/api/admin/products", json=payload)
    assert denied.status_code == 403
    assert denied.json()["code"] == "csrf_token_invalid"

    assert api_client.get("/api/admin/products").status_code == 200
    assert api_client.cookies.get(ADMIN_CSRF_COOKIE)
    allowed = api_client.post("/api/admin/products", json=payload)
    assert allowed.status_code == 200, allowed.text


def test_webhook_is_exempt_from_csrf(api_client: TestClient, shop_data, login) -> None:
    login()
    api_client.cookies.delete(CUSTOMER_CSRF_COOKIE)
    response = api_client.post(
        "/api/webhooks/psp/mock",
        json={"event_id": "evt-csrf", "payment_id": 1, "status": "paid"},
        headers={"X-Mock-Signature": "bad-signature"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
