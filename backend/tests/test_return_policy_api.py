from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Inventory, ReturnRequest

ADDRESS = {
    "recipient_name": "退货政策顾客",
    "recipient_phone": "13800000002",
    "region": "华东",
    "city": "上海",
    "detail": "退货政策路 2 号",
}


def _paid_order(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code
        == 200
    )
    return body["order_no"]


def _delivery(client: TestClient, order_no: str, tracking_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": tracking_no},
    )
    assert created.status_code == 200, created.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        response = client.post(
            f"/api/admin/orders/{order_no}/shipment/advance",
            json={"status": status},
        )
        assert response.status_code == 200, response.text


def _set_policy(
    client: TestClient,
    *,
    window_days: int = 7,
    allow_exchange: bool = True,
    categories: list[str] | None = None,
    category_windows: list[dict] | None = None,
):
    return client.patch(
        "/api/admin/return-policy",
        json={
            "window_days": window_days,
            "allow_exchange": allow_exchange,
            "final_sale_categories": categories or [],
            "category_windows": category_windows or [],
        },
    )


def test_return_policy_defaults_and_owner_update(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
):
    login()
    admin_login()

    defaults = api_client.get("/api/admin/return-policy")
    assert defaults.status_code == 200, defaults.text
    assert defaults.json()["window_days"] == 7
    assert defaults.json()["allow_exchange"] is True
    assert defaults.json()["final_sale_categories"] == []
    assert defaults.json()["updated_at"] is None

    updated = _set_policy(
        api_client,
        window_days=30,
        allow_exchange=False,
        categories=[" 耳机 ", "耳机", "定制"],
        category_windows=[
            {"category": "耳机", "window_days": 30},
            {"category": "充电器", "window_days": 15},
        ],
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["window_days"] == 30
    assert body["allow_exchange"] is False
    assert body["final_sale_categories"] == ["耳机", "定制"]
    assert body["category_windows"] == [
        {"category": "耳机", "window_days": 30},
        {"category": "充电器", "window_days": 15},
    ]
    assert body["updated_at"] is not None

    store_view = api_client.get("/api/store/return-policy")
    assert store_view.status_code == 200, store_view.text
    assert store_view.json()["window_days"] == 30
    assert store_view.json()["final_sale_categories"] == ["耳机", "定制"]
    assert store_view.json()["category_windows"][0] == {
        "category": "耳机",
        "window_days": 30,
    }

    audit = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "return_policy_updated")
    ).scalar_one()
    assert audit.payload_json["after"]["window_days"] == 30
    assert audit.payload_json["before"]["window_days"] == 7

    invalid = _set_policy(api_client, window_days=91)
    assert invalid.status_code == 422

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = _set_policy(api_client, window_days=7)
    assert forbidden.status_code == 403


def test_eligibility_exposes_deadline_and_rejects_final_sale(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
):
    login()
    admin_login()
    order_no = _paid_order(api_client, "return-policy-final-sale")
    _delivery(api_client, order_no, "SF-POLICY-001")

    eligible = api_client.get(
        "/api/store/returns/eligibility", params={"order_no": order_no}
    )
    assert eligible.status_code == 200, eligible.text
    body = eligible.json()
    assert body["eligible"] is True
    assert body["reason"] == ""
    assert body["window_days"] == 7
    assert body["delivered_at"] is not None
    assert body["deadline"] is not None
    assert body["allow_exchange"] is True
    assert body["final_sale_skus"] == []

    assert _set_policy(api_client, categories=["耳机"]).status_code == 200
    blocked = api_client.get(
        "/api/store/returns/eligibility", params={"order_no": order_no}
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["eligible"] is False
    assert "不支持退货类目" in blocked.json()["reason"]
    assert blocked.json()["final_sale_skus"] == ["SKU-EAR-001"]

    create = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "耳机不合适"},
    )
    assert create.status_code == 409
    assert "不支持退货类目" in create.json()["detail"]
    assert db_session.execute(select(ReturnRequest)).scalars().all() == []


def test_expired_window_blocks_return_and_chat(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
):
    login()
    admin_login()
    assert _set_policy(api_client, window_days=0).status_code == 200
    order_no = _paid_order(api_client, "return-policy-expired")
    _delivery(api_client, order_no, "SF-POLICY-002")

    eligibility = api_client.get(
        "/api/store/returns/eligibility", params={"order_no": order_no}
    )
    assert eligibility.status_code == 200, eligibility.text
    assert eligibility.json()["eligible"] is False
    assert "已超过退货期限" in eligibility.json()["reason"]

    create = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "过期退货"},
    )
    assert create.status_code == 409
    assert "已超过退货期限" in create.json()["detail"]

    chat = api_client.post("/api/chat", json={"text": f"订单 {order_no} 我要退货"})
    assert chat.status_code == 200, chat.text
    reply = chat.json()["reply"]
    assert "暂时不能提交退货申请" in reply
    assert "已超过退货期限" in reply
    assert (
        db_session.execute(select(ReturnRequest)).scalars().all() == []
    )


def test_exchange_switch_only_blocks_exchange(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
):
    login()
    admin_login()
    assert _set_policy(api_client, allow_exchange=False).status_code == 200
    order_no = _paid_order(api_client, "return-policy-exchange")
    _delivery(api_client, order_no, "SF-POLICY-003")

    exchange = api_client.get(
        "/api/store/returns/eligibility",
        params={"order_no": order_no, "resolution": "exchange"},
    )
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["eligible"] is False
    assert "未开放换货" in exchange.json()["reason"]

    refund = api_client.get(
        "/api/store/returns/eligibility", params={"order_no": order_no}
    )
    assert refund.status_code == 200, refund.text
    assert refund.json()["eligible"] is True

    blocked = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "想换货", "resolution": "exchange"},
    )
    assert blocked.status_code == 409
    assert "未开放换货" in blocked.json()["detail"]

    allowed = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "改为退款"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["resolution"] == "refund"


def test_category_windows_apply_per_item(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    charger = shop_data["charger"]
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == charger.id)
    ).scalars().first()
    assert inventory is not None
    inventory.qty_available = 5
    db_session.commit()

    admin_login()
    assert _set_policy(
        api_client,
        window_days=0,
        category_windows=[{"category": "耳机", "window_days": 30}],
    ).status_code == 200

    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    api_client.post("/api/store/cart/items", json={"sku": "SKU-PWR-001", "qty": 1})
    checkout = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "category-windows-order"},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        api_client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )
    _delivery(api_client, body["order_no"], "SF-CATEGORY-WINDOW")

    eligibility = api_client.get(
        "/api/store/returns/eligibility", params={"order_no": body["order_no"]}
    )
    assert eligibility.status_code == 200, eligibility.text
    data = eligibility.json()
    assert data["eligible"] is True
    assert data["reason"] == ""
    items = {item["sku"]: item for item in data["items"]}
    assert items["SKU-EAR-001"]["window_days"] == 30
    assert items["SKU-EAR-001"]["eligible"] is True
    assert items["SKU-PWR-001"]["window_days"] == 0
    assert items["SKU-PWR-001"]["eligible"] is False
    assert "已超过退货期限" in items["SKU-PWR-001"]["reason"]

    full = api_client.post(
        "/api/store/returns",
        json={"order_no": body["order_no"], "reason": "全部退货"},
    )
    assert full.status_code == 409
    assert "已超过退货期限" in full.json()["detail"]

    partial = api_client.post(
        "/api/store/returns",
        json={
            "order_no": body["order_no"],
            "reason": "只退耳机",
            "items": [{"sku": "SKU-EAR-001", "qty": 1}],
        },
    )
    assert partial.status_code == 200, partial.text
    assert [item["sku"] for item in partial.json()["items"]] == [
        "SKU-EAR-001"
    ]
