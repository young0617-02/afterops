from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

ADDRESS = {
    "recipient_name": "通知测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "通知测试路 1 号",
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


def test_proposal_notifications_are_recipient_scoped(
    api_client: TestClient, shop_data, login, admin_login, otp_login
) -> None:
    assert api_client.get("/api/store/notifications").status_code == 401
    assert api_client.get("/api/admin/notifications").status_code == 401

    login()
    order_no = _paid_order(api_client, "notification-proposal-0001")
    proposal_id = _refund_proposal(api_client, order_no)

    customer_list = api_client.get("/api/store/notifications")
    assert customer_list.status_code == 200
    assert customer_list.json()["items"] == []
    assert customer_list.json()["unread_count"] == 0

    admin_login()
    staff_list = api_client.get("/api/admin/notifications")
    assert staff_list.status_code == 200, staff_list.text
    staff_body = staff_list.json()
    assert staff_body["unread_count"] >= 1
    created = next(
        item
        for item in staff_body["items"]
        if item["type"] == "proposal_created"
    )
    assert "退款提案" in created["title"]
    assert order_no in created["body"]
    assert created["link"] == "/admin"

    read_all = api_client.post("/api/admin/notifications/read-all")
    assert read_all.status_code == 200
    assert read_all.json()["updated"] >= 1
    assert api_client.get("/api/admin/notifications").json()["unread_count"] == 0

    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text
    customer_list = api_client.get("/api/store/notifications")
    customer_body = customer_list.json()
    assert customer_body["unread_count"] == 1
    executed = customer_body["items"][0]
    assert executed["type"] == "proposal_executed"
    assert executed["title"] == "退款已执行"
    assert order_no in executed["body"]
    assert executed["link"] == "/after-sales"

    marked = api_client.post(
        f"/api/store/notifications/{executed['id']}/read"
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["read_at"] is not None
    assert api_client.get("/api/store/notifications").json()["unread_count"] == 0

    assert (
        api_client.post(
            f"/api/admin/notifications/{executed['id']}/read"
        ).status_code
        == 404
    )

    with TestClient(app) as other:
        otp_login(other, "13900000011")
        assert other.get("/api/store/notifications").json()["items"] == []
        assert (
            other.post(
                f"/api/store/notifications/{executed['id']}/read"
            ).status_code
            == 404
        )


def test_rejection_notification_includes_reason(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order_no = _paid_order(api_client, "notification-reject-0001")
    proposal_id = _refund_proposal(api_client, order_no)
    admin_login()
    rejected = api_client.post(
        f"/api/admin/proposals/{proposal_id}/reject",
        json={"reason": "缺少故障视频"},
    )
    assert rejected.status_code == 200, rejected.text

    body = api_client.get("/api/store/notifications").json()
    assert body["unread_count"] == 1
    item = body["items"][0]
    assert item["type"] == "proposal_rejected"
    assert item["title"] == "退款申请未通过"
    assert "缺少故障视频" in item["body"]


def test_handoff_resolution_notifies_customer(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    chat = api_client.post("/api/chat", json={"text": "转人工"})
    assert chat.status_code == 200, chat.text
    ticket_ref = chat.json()["handoff"]["ticket_ref"]
    admin_login()
    staff = api_client.get("/api/admin/notifications").json()
    assert any(
        item["type"] == "handoff_created" and ticket_ref in item["title"]
        for item in staff["items"]
    )
    assert api_client.get("/api/store/notifications").json()["items"] == []

    ticket_id = api_client.get("/api/admin/handoffs").json()["items"][0]["id"]
    assert (
        api_client.post(f"/api/admin/handoffs/{ticket_id}/claim").status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/admin/handoffs/{ticket_id}/resolve",
            json={"note": "已完成"},
        ).status_code
        == 200
    )
    customer = api_client.get("/api/store/notifications").json()
    assert customer["unread_count"] == 1
    assert customer["items"][0]["type"] == "handoff_resolved"
    assert ticket_ref in customer["items"][0]["body"]


def test_shipment_events_notify_customer(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order_no = _paid_order(api_client, "notification-shipment-0001")
    admin_login()
    created = api_client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": "SF-NOTIFY-0001"},
    )
    assert created.status_code == 200, created.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        response = api_client.post(
            f"/api/admin/orders/{order_no}/shipment/advance",
            json={"status": status},
        )
        assert response.status_code == 200, response.text

    body = api_client.get("/api/store/notifications").json()
    assert body["unread_count"] == 4
    titles = [item["title"] for item in body["items"]]
    assert titles == [
        "订单已签收",
        "订单派送中",
        "订单运输中",
        "订单已创建包裹",
    ]
    assert all(item["link"] == "/orders" for item in body["items"])

    read_all = api_client.post("/api/store/notifications/read-all")
    assert read_all.status_code == 200
    assert read_all.json()["updated"] == 4
    after = api_client.get("/api/store/notifications").json()
    assert after["unread_count"] == 0
    assert all(item["read_at"] is not None for item in after["items"])
