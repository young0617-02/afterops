from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Coupon, CouponRedemption, Order

ADDRESS = {
    "recipient_name": "优惠券顾客",
    "recipient_phone": "13800000009",
    "region": "华东",
    "city": "上海",
    "detail": "优惠券路 9 号",
}


def _coupon_payload(
    code: str,
    *,
    discount_type: str = "fixed",
    value: int = 5000,
    min_spend_cents: int = 0,
    starts_on: date | None = None,
    ends_on: date | None = None,
    max_uses: int | None = 10,
    per_customer_limit: int = 1,
) -> dict:
    today = date.today()
    return {
        "code": code,
        "discount_type": discount_type,
        "value": value,
        "min_spend_cents": min_spend_cents,
        "starts_on": (starts_on or today).isoformat(),
        "ends_on": (ends_on or today + timedelta(days=30)).isoformat(),
        "max_uses": max_uses,
        "per_customer_limit": per_customer_limit,
    }


def _create_coupon(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/admin/coupons", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _checkout(client: TestClient, key: str, coupon: str | None = None):
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    payload = {"address": ADDRESS}
    if coupon:
        payload["coupon_code"] = coupon
    return client.post(
        "/api/store/checkout",
        json=payload,
        headers={"Idempotency-Key": key},
    )


def test_fixed_coupon_applies_and_limits_per_customer(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    created = _create_coupon(
        api_client,
        _coupon_payload("FIXED50", value=5000, min_spend_cents=10000),
    )
    assert created["used_count"] == 0

    checkout = _checkout(api_client, "coupon-fixed", "FIXED50")
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert body["amount_cents"] == 84900
    assert body["discount_cents"] == 5000
    assert body["coupon_code"] == "FIXED50"

    order = db_session.execute(
        select(Order).where(Order.order_no == body["order_no"])
    ).scalar_one()
    assert order.discount_cents == 5000
    assert order.payable_cents == 84900
    redemption = db_session.execute(
        select(CouponRedemption).where(CouponRedemption.order_id == order.id)
    ).scalar_one()
    assert redemption.discount_cents == 5000

    coupon = db_session.execute(
        select(Coupon).where(Coupon.code == "FIXED50")
    ).scalar_one()
    assert coupon.used_count == 1

    second = _checkout(api_client, "coupon-fixed-2", "FIXED50")
    assert second.status_code == 409
    assert "每客使用上限" in second.json()["detail"]


def test_cancel_releases_coupon_for_reuse(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _create_coupon(api_client, _coupon_payload("REUSE10", value=1000))
    first = _checkout(api_client, "coupon-release-1", "REUSE10")
    assert first.status_code == 201, first.text
    order_no = first.json()["order_no"]

    coupon = db_session.execute(
        select(Coupon).where(Coupon.code == "REUSE10")
    ).scalar_one()
    assert coupon.used_count == 1
    cancelled = api_client.post(f"/api/store/orders/{order_no}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    db_session.expire_all()
    coupon = db_session.execute(
        select(Coupon).where(Coupon.code == "REUSE10")
    ).scalar_one()
    assert coupon.used_count == 0
    assert db_session.execute(select(CouponRedemption)).scalars().all() == []

    reused = _checkout(api_client, "coupon-release-2", "REUSE10")
    assert reused.status_code == 201, reused.text
    assert reused.json()["amount_cents"] == 88900


def test_percent_coupon_and_validation(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    _create_coupon(
        api_client,
        _coupon_payload(
            "PCT20", discount_type="percent", value=20, max_uses=None
        ),
    )
    percent = _checkout(api_client, "coupon-percent", "PCT20")
    assert percent.status_code == 201, percent.text
    assert percent.json()["discount_cents"] == 17980
    assert percent.json()["amount_cents"] == 71920

    _create_coupon(
        api_client,
        _coupon_payload("HIGHMIN", value=1000, min_spend_cents=100000),
    )
    threshold = _checkout(api_client, "coupon-threshold", "HIGHMIN")
    assert threshold.status_code == 409
    assert "门槛" in threshold.json()["detail"]

    today = date.today()
    _create_coupon(
        api_client,
        _coupon_payload(
            "EXPIRED",
            value=1000,
            starts_on=today - timedelta(days=10),
            ends_on=today - timedelta(days=1),
        ),
    )
    expired = _checkout(api_client, "coupon-expired", "EXPIRED")
    assert expired.status_code == 409
    assert "有效期" in expired.json()["detail"]

    disabled = _create_coupon(
        api_client, _coupon_payload("DISABLED", value=1000)
    )
    patch = api_client.patch(
        f"/api/admin/coupons/{disabled['id']}",
        json={"status": "disabled"},
    )
    assert patch.status_code == 200, patch.text
    blocked = _checkout(api_client, "coupon-disabled", "DISABLED")
    assert blocked.status_code == 409
    assert "停用" in blocked.json()["detail"]

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = api_client.post(
        "/api/admin/coupons", json=_coupon_payload("AGENT", value=1000)
    )
    assert forbidden.status_code == 403


def test_coupon_scope_category_and_sku(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    from app.domain.models import Inventory

    charger = shop_data["charger"]
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == charger.id)
    ).scalars().first()
    assert inventory is not None
    inventory.qty_available = 5
    db_session.commit()

    login()
    admin_login()
    high_threshold = _create_coupon(
        api_client,
        {
            **_coupon_payload("CAT-THRESHOLD", value=5000, min_spend_cents=20000),
            "scope_type": "category",
            "scope_values": ["充电器"],
        },
    )
    assert high_threshold["scope_type"] == "category"
    assert high_threshold["scope_values"] == ["充电器"]

    api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-PWR-001", "qty": 1}
    )
    rejected = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS, "coupon_code": "CAT-THRESHOLD"},
        headers={"Idempotency-Key": "coupon-scope-threshold"},
    )
    assert rejected.status_code == 409
    assert "适用商品金额未达到" in rejected.json()["detail"]

    _create_coupon(
        api_client,
        {
            **_coupon_payload("CAT-OK", value=5000, min_spend_cents=0),
            "scope_type": "category",
            "scope_values": ["充电器"],
        },
    )
    applied = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS, "coupon_code": "CAT-OK"},
        headers={"Idempotency-Key": "coupon-scope-ok"},
    )
    assert applied.status_code == 201, applied.text
    body = applied.json()
    assert body["discount_cents"] == 5000
    assert body["amount_cents"] == 104800

    _create_coupon(
        api_client,
        {
            **_coupon_payload("SKU-OK", value=3000, min_spend_cents=0),
            "scope_type": "sku",
            "scope_values": ["sku-ear-001"],
        },
    )
    api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-PWR-001", "qty": 1}
    )
    sku_applied = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS, "coupon_code": "SKU-OK"},
        headers={"Idempotency-Key": "coupon-scope-sku"},
    )
    assert sku_applied.status_code == 201, sku_applied.text
    assert sku_applied.json()["discount_cents"] == 3000
    assert sku_applied.json()["amount_cents"] == 106800

    invalid = api_client.post(
        "/api/admin/coupons",
        json={
            **_coupon_payload("CAT-EMPTY", value=1000),
            "scope_type": "category",
            "scope_values": [],
        },
    )
    assert invalid.status_code == 422
