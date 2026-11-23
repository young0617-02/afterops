from __future__ import annotations

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "订单详情顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "订单详情路 1 号",
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


def test_admin_order_detail_includes_items_payments_refunds_and_shipment(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    assert (
        api_client.get("/api/admin/orders/NO-SUCH-ORDER").status_code == 401
    )
    login()
    order_no = _paid_order(api_client, "admin-order-detail-0001")
    admin_login()
    shipment = api_client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": "SF-DETAIL-0001"},
    )
    assert shipment.status_code == 200, shipment.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        assert (
            api_client.post(
                f"/api/admin/orders/{order_no}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )

    refund = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款"}
    )
    proposal_id = refund.json()["proposals"][0]["id"]
    assert api_client.post(f"/api/admin/proposals/{proposal_id}/approve").status_code == 200

    detail = api_client.get(f"/api/admin/orders/{order_no}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["order_no"] == order_no
    assert body["status"] == "refunded"
    assert body["customer_phone"] == "13800000001"
    assert body["payable_display"] == "899.00"
    assert [item["sku"] for item in body["items"]] == ["SKU-EAR-001"]
    assert body["items"][0]["line_total_display"] == "899.00"
    assert len(body["payments"]) == 1
    assert body["payments"][0]["status"] == "refunded"
    assert len(body["refunds"]) == 1
    assert body["refunds"][0]["amount_display"] == "899.00"
    assert len(body["shipments"]) == 1
    assert body["shipments"][0]["status"] == "delivered"
    assert body["shipments"][0]["package_no"] == 1
    assert body["shipments"][0]["tracking_no"] == "SF-DETAIL-0001"
    assert [event["node"] for event in body["shipments"][0]["events"]] == [
        "待揽收",
        "运输中",
        "派送中",
        "已签收",
    ]
    assert [proposal["id"] for proposal in body["proposals"]] == [proposal_id]
    assert body["proposals"][0]["status"] == "executed"
    assert body["returns"] == []

    assert (
        api_client.get("/api/admin/orders/NO-SUCH-ORDER").status_code == 404
    )

    admin_login("agent@test.local", "agent123456")
    assert api_client.get(f"/api/admin/orders/{order_no}").status_code == 200
