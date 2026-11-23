from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.return_reasons import classify_return_reason

ADDRESS = {
    "recipient_name": "退货分析顾客",
    "recipient_phone": "13800000011",
    "region": "华东",
    "city": "上海",
    "detail": "退货分析路 11 号",
}


def _delivered_order(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )
    created = client.post(
        f"/api/admin/orders/{body['order_no']}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-AN-{uuid4().hex[:8]}"},
    )
    assert created.status_code == 200, created.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        assert (
            client.post(
                f"/api/admin/orders/{body['order_no']}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )
    return body["order_no"]


def _return(
    client: TestClient,
    order_no: str,
    reason: str,
    category: str,
):
    return client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": reason,
            "reason_category": category,
        },
    )


def test_return_reason_classifier() -> None:
    assert classify_return_reason("耳机有杂音") == "quality"
    assert classify_return_reason("发错颜色了") == "wrong_item"
    assert classify_return_reason("尺寸不合适") == "size"
    assert classify_return_reason("包装破损") == "damaged"
    assert classify_return_reason("不想要了") == "not_needed"
    assert classify_return_reason("随便看看") == "other"


def test_return_analytics_by_reason_status_and_sku(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    first_order = _delivered_order(api_client, "return-analytics-1")
    second_order = _delivered_order(api_client, "return-analytics-2")

    first = _return(api_client, first_order, "耳机有杂音", "quality")
    assert first.status_code == 200, first.text
    assert first.json()["reason_category"] == "quality"
    second = _return(api_client, second_order, "尺寸不合适", "size")
    assert second.status_code == 200, second.text

    approved = api_client.post(
        f"/api/admin/returns/{first.json()['id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    analytics = api_client.get("/api/admin/returns/analytics?days=30")
    assert analytics.status_code == 200, analytics.text
    body = analytics.json()
    assert body["total"] == 2
    assert body["status_counts"]["approved"] == 1
    assert body["status_counts"]["requested"] == 1
    reasons = {row["category"]: row for row in body["reasons"]}
    assert reasons["quality"]["label"] == "质量问题"
    assert reasons["quality"]["count"] == 1
    assert reasons["size"]["count"] == 1
    assert reasons["other"]["count"] == 0
    assert body["top_skus"][0]["sku"] == "SKU-EAR-001"
    assert body["top_skus"][0]["requests"] == 2
    assert body["top_skus"][0]["qty"] == 2
    assert body["approval_rate"] == 0.5
    assert body["avg_approval_hours"] is not None

    assert (
        api_client.get("/api/admin/returns/analytics?days=0").status_code
        == 422
    )

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert (
        api_client.get("/api/admin/returns/analytics").status_code == 200
    )