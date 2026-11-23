from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "时间线测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "时间线路 1 号",
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
        client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
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


def test_customer_timeline_aggregates_after_sales_events(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "timeline-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-TIMELINE-OUT-0001")

    repair = api_client.post(
        "/api/store/repairs",
        json={"order_no": order_no, "sku": "SKU-EAR-001", "issue": "左耳无声"},
    )
    assert repair.status_code == 200, repair.text

    created_return = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "时间线退货"},
    )
    assert created_return.status_code == 200, created_return.text
    return_id = created_return.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-TIMELINE-BACK"},
        ).status_code
        == 200
    )

    invoice = api_client.post(
        "/api/store/invoices",
        json={
            "order_no": order_no,
            "invoice_type": "personal",
            "title": "时间线测试抬头",
        },
    )
    assert invoice.status_code == 200, invoice.text

    chat = api_client.post("/api/chat", json={"text": "转人工"})
    assert chat.status_code == 200, chat.text

    proposal = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "refund",
            "order_no": order_no,
            "amount_cents": 1000,
            "reason": "时间线手动退款",
        },
    )
    assert proposal.status_code == 200, proposal.text
    assert (
        api_client.post(
            f"/api/admin/proposals/{proposal.json()['proposal_id']}/approve"
        ).status_code
        == 200
    )

    response = api_client.get("/api/store/after-sales/timeline")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows
    kinds = {row["kind"] for row in rows}
    assert {
        "return",
        "return_tracking",
        "repair",
        "invoice",
        "handoff",
        "proposal",
        "refund",
    } <= kinds
    return_events = [
        row for row in rows if row["kind"] == "return"
    ]
    assert any(row["order_no"] == order_no for row in return_events)
    assert any("退货申请已通过" in row["title"] for row in return_events)
    tracking_events = [
        row for row in rows if row["kind"] == "return_tracking"
    ]
    assert any("已登记回寄" in row["title"] for row in tracking_events)
    assert any(row["kind"] == "refund" and "10.00 元" in row["detail"] for row in rows)
    occurred = [
        datetime.fromisoformat(row["occurred_at"]) for row in rows
    ]
    assert occurred == sorted(occurred, reverse=True)

    limited = api_client.get(
        "/api/store/after-sales/timeline", params={"limit": 5}
    )
    assert limited.status_code == 200, limited.text
    assert len(limited.json()) == 5

    login("13800000002")
    other = api_client.get("/api/store/after-sales/timeline")
    assert other.status_code == 200, other.text
    assert other.json() == []


def test_admin_customer_timeline_is_scoped_and_requires_admin(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "timeline-admin-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-TIMELINE-ADMIN-0001")
    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "后台时间线测试"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )

    customers = api_client.get(
        "/api/admin/customers", params={"q": "13800000001"}
    )
    assert customers.status_code == 200, customers.text
    customer_id = customers.json()[0]["id"]
    response = api_client.get(
        f"/api/admin/customers/{customer_id}/timeline"
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert any(row["title"] == "退货申请已通过" for row in rows)
    assert any(row["order_no"] == order_no for row in rows)
    assert any(row["kind"] == "return" for row in rows)

    missing = api_client.get("/api/admin/customers/999999/timeline")
    assert missing.status_code == 404

    api_client.delete("/api/admin/session")
    unauth = api_client.get(
        f"/api/admin/customers/{customer_id}/timeline"
    )
    assert unauth.status_code == 401
