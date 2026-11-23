from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Order

ADDRESS = {
    "recipient_name": "配送测试顾客",
    "recipient_phone": "13800000015",
    "region": "华东",
    "city": "上海",
    "detail": "配送路 15 号",
}


def _create_method(
    client: TestClient,
    code: str,
    *,
    fee_cents: int = 0,
    free_over_cents: int | None = None,
    sort_order: int = 0,
) -> dict:
    response = client.post(
        "/api/admin/shipping-methods",
        json={
            "code": code,
            "name": f"{code} 配送",
            "fee_cents": fee_cents,
            "free_over_cents": free_over_cents,
            "sort_order": sort_order,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _checkout(
    client: TestClient,
    key: str,
    *,
    shipping_code: str | None = None,
    coupon_code: str | None = None,
):
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    payload = {"address": ADDRESS}
    if shipping_code:
        payload["shipping_method_code"] = shipping_code
    if coupon_code:
        payload["coupon_code"] = coupon_code
    return client.post(
        "/api/store/checkout",
        json=payload,
        headers={"Idempotency-Key": key},
    )


def test_default_and_express_shipping(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _create_method(api_client, "STANDARD", fee_cents=0)
    _create_method(api_client, "EXPRESS", fee_cents=1200, sort_order=1)

    public = api_client.get("/api/store/shipping-methods")
    assert public.status_code == 200, public.text
    assert [row["code"] for row in public.json()] == ["STANDARD", "EXPRESS"]

    default = _checkout(api_client, "shipping-default")
    assert default.status_code == 201, default.text
    assert default.json()["freight_cents"] == 0
    assert default.json()["amount_cents"] == 89900
    assert default.json()["shipping_method_code"] == "STANDARD"

    express = _checkout(api_client, "shipping-express", shipping_code="EXPRESS")
    assert express.status_code == 201, express.text
    assert express.json()["freight_cents"] == 1200
    assert express.json()["amount_cents"] == 91100
    assert express.json()["shipping_method_name"] == "EXPRESS 配送"

    order = db_session.execute(
        select(Order).where(Order.order_no == express.json()["order_no"])
    ).scalar_one()
    assert order.freight_cents == 1200
    assert order.payable_cents == 91100


def test_free_shipping_coupon_and_disabled_method(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    today = date.today()
    coupon = api_client.post(
        "/api/admin/coupons",
        json={
            "code": "SHIP50",
            "discount_type": "fixed",
            "value": 5000,
            "min_spend_cents": 0,
            "starts_on": today.isoformat(),
            "ends_on": (today + timedelta(days=30)).isoformat(),
            "max_uses": 10,
            "per_customer_limit": 2,
        },
    )
    assert coupon.status_code == 200, coupon.text
    _create_method(
        api_client,
        "FREESHIP",
        fee_cents=1200,
        free_over_cents=50000,
        sort_order=1,
    )
    high = _create_method(
        api_client,
        "HIGHFREE",
        fee_cents=1200,
        free_over_cents=100000,
        sort_order=2,
    )

    free_checkout = _checkout(
        api_client,
        "shipping-free",
        shipping_code="FREESHIP",
        coupon_code="SHIP50",
    )
    assert free_checkout.status_code == 201, free_checkout.text
    body = free_checkout.json()
    assert body["freight_cents"] == 0
    assert body["discount_cents"] == 5000
    assert body["amount_cents"] == 84900

    high_checkout = _checkout(
        api_client, "shipping-high", shipping_code="HIGHFREE"
    )
    assert high_checkout.status_code == 201, high_checkout.text
    assert high_checkout.json()["freight_cents"] == 1200

    disabled = api_client.patch(
        f"/api/admin/shipping-methods/{high['id']}",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    blocked = _checkout(
        api_client, "shipping-disabled", shipping_code="HIGHFREE"
    )
    assert blocked.status_code == 409
    assert "配送方式不可用" in blocked.json()["detail"]

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = api_client.post(
        "/api/admin/shipping-methods",
        json={
            "code": "AGENT",
            "name": "客服配送",
            "fee_cents": 0,
            "free_over_cents": None,
            "sort_order": 0,
        },
    )
    assert forbidden.status_code == 403
