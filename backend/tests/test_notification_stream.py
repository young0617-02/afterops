from __future__ import annotations

import json

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "通知流顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "通知流路 1 号",
}


def _stream_data(lines) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ")
    ]


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
        assert (
            client.post(
                f"/api/admin/orders/{order_no}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )


def test_customer_notification_stream_replays_and_resumes(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "notification-stream-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-NOTIFY-STREAM-0001")
    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "通知流测试"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )

    with api_client.stream(
        "GET",
        "/api/store/notifications/stream?after_id=0&wait_seconds=1",
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "text/event-stream"
        )
        payloads = _stream_data(response.iter_lines())
    assert payloads
    approved = [
        row for row in payloads if row["type"] == "return_approved"
    ]
    assert len(approved) == 1
    assert approved[0]["title"] == "退货申请已通过"
    last_id = payloads[-1]["id"]

    with api_client.stream(
        "GET",
        f"/api/store/notifications/stream?after_id={last_id}"
        "&wait_seconds=1",
    ) as response:
        resumed = _stream_data(response.iter_lines())
    assert resumed == []

    login("13800000002")
    with api_client.stream(
        "GET",
        "/api/store/notifications/stream?after_id=0&wait_seconds=1",
    ) as response:
        other = _stream_data(response.iter_lines())
    assert other == []


def test_admin_notification_stream_scopes_and_requires_login(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    chat = api_client.post("/api/chat", json={"text": "转人工"})
    assert chat.status_code == 200, chat.text

    with api_client.stream(
        "GET",
        "/api/admin/notifications/stream?after_id=0&wait_seconds=1",
    ) as response:
        assert response.status_code == 401

    admin_login()
    with api_client.stream(
        "GET",
        "/api/admin/notifications/stream?after_id=0&wait_seconds=1",
    ) as response:
        assert response.status_code == 200
        payloads = _stream_data(response.iter_lines())
    assert payloads
    created = [
        row for row in payloads if row["type"] == "handoff_created"
    ]
    assert len(created) == 1
    assert "转人工工单" in created[0]["title"]

    with api_client.stream(
        "GET",
        f"/api/admin/notifications/stream?after_id={payloads[-1]['id']}"
        "&wait_seconds=1",
    ) as response:
        resumed = _stream_data(response.iter_lines())
    assert resumed == []
