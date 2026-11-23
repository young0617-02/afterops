from __future__ import annotations

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "上下文顾客",
    "recipient_phone": "13800000012",
    "region": "华东",
    "city": "上海",
    "detail": "上下文路 12 号",
}


def test_handoff_detail_includes_customer_context(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    checkout = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "handoff-context-1"},
    )
    assert checkout.status_code == 201, checkout.text
    order_no = checkout.json()["order_no"]
    chat = api_client.post("/api/chat", json={"text": "转人工"})
    assert chat.status_code == 200, chat.text

    admin_login()
    ticket_id = api_client.get("/api/admin/handoffs").json()["items"][0][
        "id"
    ]
    detail = api_client.get(f"/api/admin/handoffs/{ticket_id}")
    assert detail.status_code == 200, detail.text
    context = detail.json()["customer_context"]
    assert context["order_count"] == 1
    assert context["after_sales_count"] == 0
    assert context["pending_proposals"] == 0
    assert context["active_returns"] == 0
    assert context["last_order_at"] is not None
    assert context["recent_orders"][0]["order_no"] == order_no
    assert context["recent_orders"][0]["status"] == "pending_payment"
    assert context["recent_orders"][0]["payable_display"]