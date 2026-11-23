from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Coupon, CouponClaim

ADDRESS = {
    "recipient_name": "领券顾客",
    "recipient_phone": "13800000051",
    "region": "华东",
    "city": "上海",
    "detail": "领券路 51 号",
}


def _payload(code: str, **overrides) -> dict:
    today = date.today()
    payload = {
        "code": code,
        "discount_type": "fixed",
        "value": 3000,
        "min_spend_cents": 0,
        "starts_on": today.isoformat(),
        "ends_on": (today + timedelta(days=30)).isoformat(),
        "max_uses": 10,
        "per_customer_limit": 1,
        "claimable": True,
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/admin/coupons", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _checkout(client: TestClient, key: str, code: str):
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    return client.post(
        "/api/store/checkout",
        json={"address": ADDRESS, "coupon_code": code},
        headers={"Idempotency-Key": key},
    )


def test_claim_coupon_appears_in_wallet_and_can_be_used(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    coupon = _create(api_client, _payload("CLAIM30"))

    listed = api_client.get("/api/store/coupons/claimable")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["id"] == coupon["id"])
    assert row["claimed"] is False
    assert row["sold_out"] is False
    assert "code" not in row

    claim = api_client.post(f"/api/store/coupons/{coupon['id']}/claim")
    assert claim.status_code == 200, claim.text
    assert claim.json()["code"] == "CLAIM30"
    assert claim.json()["state"] == "active"
    assert claim.json()["remaining_uses"] == 1

    again = api_client.post(f"/api/store/coupons/{coupon['id']}/claim")
    assert again.status_code == 409
    assert "已领取" in again.json()["detail"]

    claimed_row = next(
        item
        for item in api_client.get(
            "/api/store/coupons/claimable"
        ).json()
        if item["id"] == coupon["id"]
    )
    assert claimed_row["claimed"] is True

    wallet = api_client.get("/api/store/coupons/wallet")
    assert wallet.status_code == 200, wallet.text
    assert [item["code"] for item in wallet.json()] == ["CLAIM30"]

    admin_list = api_client.get("/api/admin/coupons").json()
    admin_row = next(
        item for item in admin_list if item["id"] == coupon["id"]
    )
    assert admin_row["claimable"] is True
    assert admin_row["claim_count"] == 1
    assert admin_row["used_count"] == 0

    db_session.expire_all()
    stored = db_session.execute(
        select(CouponClaim).where(CouponClaim.coupon_id == coupon["id"])
    ).scalar_one()
    assert stored.customer_id == 1
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "coupon_claimed")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["coupon_id"] == coupon["id"]

    checkout = _checkout(api_client, "coupon-claim-checkout", "CLAIM30")
    assert checkout.status_code == 201, checkout.text
    assert checkout.json()["discount_cents"] == 3000
    used_wallet = api_client.get("/api/store/coupons/wallet").json()[0]
    assert used_wallet["times_used"] == 1
    assert used_wallet["remaining_uses"] == 0
    assert used_wallet["state"] == "used_up"


def test_claimable_list_filters_and_claim_guards(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    hidden = _create(
        api_client, _payload("NOTCLAIM", claimable=False)
    )
    disabled = _create(api_client, _payload("DISABLED"))
    assert (
        api_client.patch(
            f"/api/admin/coupons/{disabled['id']}",
            json={"status": "disabled"},
        ).status_code
        == 200
    )
    expired = _create(
        api_client,
        _payload(
            "EXPIRED",
            starts_on=(date.today() - timedelta(days=10)).isoformat(),
            ends_on=(date.today() - timedelta(days=1)).isoformat(),
        ),
    )
    sold_out = _create(
        api_client, _payload("SOLD", max_uses=1)
    )
    db_session.expire_all()
    coupon = db_session.get(Coupon, sold_out["id"])
    assert coupon is not None
    coupon.used_count = 1
    db_session.commit()

    listed = {
        item["id"]: item
        for item in api_client.get("/api/store/coupons/claimable").json()
    }
    assert hidden["id"] not in listed
    assert disabled["id"] not in listed
    assert expired["id"] not in listed
    assert listed[sold_out["id"]]["sold_out"] is True

    for coupon_id in (hidden["id"], disabled["id"], expired["id"]):
        response = api_client.post(f"/api/store/coupons/{coupon_id}/claim")
        assert response.status_code == 409
        assert "不可领取" in response.json()["detail"]
    sold = api_client.post(f"/api/store/coupons/{sold_out['id']}/claim")
    assert sold.status_code == 409
    assert "领完" in sold.json()["detail"]

    api_client.delete("/api/store/session")
    assert api_client.get("/api/store/coupons/claimable").status_code == 401
    assert api_client.get("/api/store/coupons/wallet").status_code == 401
