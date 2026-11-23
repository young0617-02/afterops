from __future__ import annotations

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "客户目录顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "客户目录路 1 号",
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


def test_customer_directory_search_count_and_detail(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    assert api_client.get("/api/admin/customers").status_code == 401

    login()
    order_no = _paid_order(api_client, "customer-directory-0001")
    refund = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款"}
    )
    assert refund.status_code == 200, refund.text
    proposal_id = refund.json()["proposals"][0]["id"]
    updated = api_client.patch(
        "/api/store/me", json={"email": "directory@example.com"}
    )
    assert updated.status_code == 200
    phone = updated.json()["phone"]

    admin_login()
    listed = api_client.get("/api/admin/customers")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["phone"] == phone)
    assert row["order_count"] == 1
    assert row["pending_proposals"] == 1
    assert row["after_sales_count"] == 1
    assert row["active_returns"] == 0
    assert row["last_order_at"]

    for query in (phone, "顾客0001", "directory@example.com"):
        found = api_client.get("/api/admin/customers", params={"q": query})
        assert found.status_code == 200, found.text
        assert [item["id"] for item in found.json()] == [row["id"]]

    detail = api_client.get(f"/api/admin/customers/{row['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [item["order_no"] for item in body["orders"]] == [order_no]
    assert body["orders"][0]["payable_display"] == "899.00"
    assert [item["id"] for item in body["proposals"]] == [proposal_id]
    assert body["returns"] == []

    assert api_client.get("/api/admin/customers/999999").status_code == 404

    admin_login("agent@test.local", "agent123456")
    assert api_client.get("/api/admin/customers").status_code == 200
    assert api_client.get(f"/api/admin/customers/{row['id']}").status_code == 200
