from __future__ import annotations

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "概览测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "概览测试路 1 号",
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
    confirm = client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")
    assert confirm.status_code == 200, confirm.text
    return body["order_no"]


def _refund_proposal(client: TestClient, order_no: str) -> int:
    chat = client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款"}
    )
    assert chat.status_code == 200, chat.text
    return chat.json()["proposals"][0]["id"]


def test_overview_tracks_todo_and_24h_metrics(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    assert api_client.get("/api/admin/overview").status_code == 401

    login()
    first_order = _paid_order(api_client, "overview-paid-0001")
    proposal_id = _refund_proposal(api_client, first_order)
    human = api_client.post("/api/chat", json={"text": "转人工"})
    assert human.status_code == 200, human.text
    assert human.json()["handoff"]["ticket_ref"].startswith("TK-")

    admin_login()
    initial = api_client.get("/api/admin/overview")
    assert initial.status_code == 200, initial.text
    body = initial.json()
    assert body["window_hours"] == 24
    assert body["pending_proposals"] == 1
    assert body["pending_handoffs"] == 1
    assert body["claimed_handoffs"] == 0
    assert body["urgent_handoffs"] == 0
    assert body["unfulfilled_orders"] == 1
    assert body["pending_returns"] == 0
    assert body["paid_orders"] == 1
    assert body["paid_amount_cents"] == 89900
    assert body["paid_amount_display"] == "899.00"
    assert body["proposals_created"] == 1
    assert body["proposals_executed"] == 0
    assert body["handoffs_created"] == 1
    assert body["ai_chats"] == 2
    assert body["approval_median_seconds"] is None
    assert body["unread_notifications"] > 0
    assert body["audit_valid"] is True
    assert body["audit_checked_events"] > 0
    assert body["audit_broken_at"] is None

    weekly = api_client.get("/api/admin/overview?hours=168")
    assert weekly.status_code == 200, weekly.text
    assert weekly.json()["window_hours"] == 168
    assert weekly.json()["paid_orders"] == 1
    assert api_client.get("/api/admin/overview?hours=0").status_code == 422

    trend = api_client.get("/api/admin/overview/trend?days=7")
    assert trend.status_code == 200, trend.text
    trend_body = trend.json()
    assert len(trend_body) == 7
    today = trend_body[-1]
    assert today["paid_orders"] >= 1
    assert today["paid_amount_cents"] >= 89900
    assert today["proposals"] >= 1
    assert today["handoffs"] >= 1
    assert today["ai_chats"] >= 2

    ticket_id = api_client.get("/api/admin/handoffs").json()["items"][0]["id"]
    assert (
        api_client.post(f"/api/admin/handoffs/{ticket_id}/claim").status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/admin/handoffs/{ticket_id}/resolve",
            json={"note": "概览验证"},
        ).status_code
        == 200
    )
    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text

    second_order = _paid_order(api_client, "overview-paid-0002")
    after_approval = api_client.get("/api/admin/overview").json()
    assert after_approval["pending_proposals"] == 0
    assert after_approval["pending_handoffs"] == 0
    assert after_approval["claimed_handoffs"] == 0
    assert after_approval["proposals_executed"] == 1
    assert after_approval["handoffs_resolved"] == 1
    assert after_approval["refunds"] == 1
    assert after_approval["refund_amount_cents"] == 89900
    assert after_approval["refund_amount_display"] == "899.00"
    assert after_approval["approval_median_seconds"] is not None
    assert after_approval["approval_median_seconds"] >= 0
    assert after_approval["unfulfilled_orders"] == 1
    assert after_approval["paid_orders"] == 2

    shipment = api_client.post(
        f"/api/admin/orders/{second_order}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": "SF-OVERVIEW-0001"},
    )
    assert shipment.status_code == 200, shipment.text
    advanced = api_client.post(
        f"/api/admin/orders/{second_order}/shipment/advance",
        json={"status": "in_transit"},
    )
    assert advanced.status_code == 200, advanced.text
    assert api_client.get("/api/admin/overview").json()["unfulfilled_orders"] == 0

    admin_login("agent@test.local", "agent123456")
    agent_view = api_client.get("/api/admin/overview")
    assert agent_view.status_code == 200
    assert agent_view.json()["window_hours"] == 24
