from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Order, Shipment
from app.services.audit import verify_chain

ADDRESS = {
    "recipient_name": "物流测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "履约测试路 1 号",
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


def _create_shipment(client: TestClient, order_no: str, tracking_no: str):
    return client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": tracking_no},
    )


def _advance(client: TestClient, order_no: str, status: str, **extra):
    return client.post(
        f"/api/admin/orders/{order_no}/shipment/advance",
        json={"status": status, **extra},
    )


def test_owner_ships_and_delivers_order(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "fulfillment-happy-0001")
    admin_login()

    listed = api_client.get("/api/admin/orders")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["order_no"] == order_no)
    assert row["shipments"] == []
    assert row["items"][0]["remaining_qty"] == 1
    assert row["fulfillment_status"] == "unfulfilled"

    created = _create_shipment(api_client, order_no, "SF-E2E-0001")
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "pending"
    assert created.json()["order_status"] == "fulfilling"
    assert created.json()["fulfillment_status"] == "allocated"

    assert _advance(api_client, order_no, "in_transit").status_code == 200
    transit = _advance(
        api_client,
        order_no,
        "out_for_delivery",
        node="上海徐汇营业点",
        description="快递员正在派送",
    )
    assert transit.status_code == 200, transit.text
    assert transit.json()["status"] == "out_for_delivery"
    delivered = _advance(api_client, order_no, "delivered")
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["order_status"] == "delivered"
    assert delivered.json()["fulfillment_status"] == "delivered"

    detail = api_client.get(f"/api/store/orders/{order_no}")
    assert detail.status_code == 200, detail.text
    shipments = detail.json()["shipments"]
    assert len(shipments) == 1
    shipment = shipments[0]
    assert shipment["carrier"] == "顺丰速运"
    assert shipment["tracking_no"] == "SF-E2E-0001"
    assert shipment["status"] == "delivered"
    assert shipment["delivered_at"] is not None
    assert [event["node"] for event in shipment["events"]] == [
        "待揽收",
        "运输中",
        "上海徐汇营业点",
        "已签收",
    ]
    assert detail.json()["status"] == "delivered"
    assert detail.json()["fulfillment_status"] == "delivered"

    again = _advance(api_client, order_no, "delivered")
    assert again.status_code == 409

    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 到哪了"}
    )
    assert chat.status_code == 200, chat.text
    chat_body = chat.json()
    assert chat_body["intent"] == "LOGISTICS_TRACK"
    assert "SF-E2E-0001" in chat_body["reply"]
    assert "已签收" in chat_body["reply"]

    db_session.expire_all()
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")
    events = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type.like("shipment_%"))
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert [event.event_type for event in events] == [
        "shipment_created",
        "shipment_status_changed",
        "shipment_status_changed",
        "shipment_status_changed",
    ]
    assert events[0].payload_json["tracking_no"] == "SF-E2E-0001"


def test_duplicate_and_invalid_shipment_changes(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    first = _paid_order(api_client, "fulfillment-dup-0001")
    second = _paid_order(api_client, "fulfillment-dup-0002")
    admin_login()

    assert _create_shipment(api_client, first, "SF-DUP-0001").status_code == 200
    assert _create_shipment(api_client, first, "SF-DUP-0002").status_code == 409
    duplicate = _create_shipment(api_client, second, "SF-DUP-0001")
    assert duplicate.status_code == 409

    skipped = _advance(api_client, first, "delivered")
    assert skipped.status_code == 409
    assert _advance(api_client, first, "in_transit").status_code == 200
    assert _advance(api_client, first, "in_transit").status_code == 409


def test_agent_can_list_but_cannot_change_shipment(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order_no = _paid_order(api_client, "fulfillment-agent-0001")
    admin_login("agent@test.local", "agent123456")

    listed = api_client.get("/api/admin/orders")
    assert listed.status_code == 200
    assert any(item["order_no"] == order_no for item in listed.json())

    create = _create_shipment(api_client, order_no, "SF-AGENT-0001")
    assert create.status_code == 403
    advance = _advance(api_client, order_no, "in_transit")
    assert advance.status_code == 403


def test_refunded_order_cannot_be_shipped(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "fulfillment-refunded-0001")
    order = db_session.execute(
        select(Order).where(Order.order_no == order_no)
    ).scalar_one()
    order.status = "refunded"
    order.payment_status = "refunded"
    db_session.commit()

    admin_login()
    rejected = _create_shipment(api_client, order_no, "SF-REFUND-0001")
    assert rejected.status_code == 409
    assert db_session.execute(select(Shipment)).scalars().all() == []


def test_customer_confirms_receipt_after_delivery(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "confirm-receipt-0001")
    admin_login()
    assert _create_shipment(api_client, order_no, "SF-CONFIRM-0001").status_code == 200
    for status in ("in_transit", "out_for_delivery", "delivered"):
        assert _advance(api_client, order_no, status).status_code == 200

    confirmed = api_client.post(f"/api/store/orders/{order_no}/confirm-receipt")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"
    again = api_client.post(f"/api/store/orders/{order_no}/confirm-receipt")
    assert again.status_code == 409
    detail = api_client.get(f"/api/store/orders/{order_no}")
    assert detail.json()["status"] == "completed"

    pending = _paid_order(api_client, "confirm-receipt-0002")
    not_delivered = api_client.post(
        f"/api/store/orders/{pending}/confirm-receipt"
    )
    assert not_delivered.status_code == 409

    db_session.expire_all()
    event = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "order_completed")
    ).scalar_one()
    assert event.payload_json["order_no"] == order_no


def test_admin_order_search(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    first = _paid_order(api_client, "order-search-0001")
    second = _paid_order(api_client, "order-search-0002")
    admin_login()

    by_order = api_client.get("/api/admin/orders", params={"q": first})
    assert by_order.status_code == 200
    assert [row["order_no"] for row in by_order.json()] == [first]

    by_phone = api_client.get(
        "/api/admin/orders", params={"q": "13800000001"}
    )
    assert {row["order_no"] for row in by_phone.json()} >= {first, second}

    missing = api_client.get(
        "/api/admin/orders", params={"q": "NO-SUCH-ORDER"}
    )
    assert missing.status_code == 200
    assert missing.json() == []


def test_customer_order_pagination_and_status_filter(
    api_client: TestClient, shop_data, login
) -> None:
    login()
    first = _paid_order(api_client, "order-page-0001")
    second = _paid_order(api_client, "order-page-0002")
    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    pending = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "order-page-0003"},
    ).json()
    assert (
        api_client.post(
            f"/api/store/orders/{pending['order_no']}/cancel"
        ).status_code
        == 200
    )

    page = api_client.get("/api/store/orders", params={"limit": 2})
    assert page.status_code == 200, page.text
    assert len(page.json()) == 2
    cursor = page.json()[-1]["id"]
    next_page = api_client.get(
        "/api/store/orders", params={"limit": 2, "before_id": cursor}
    )
    assert len(next_page.json()) == 1
    assert next_page.json()[0]["id"] < cursor

    cancelled = api_client.get(
        "/api/store/orders", params={"status": "cancelled"}
    )
    assert [row["order_no"] for row in cancelled.json()] == [
        pending["order_no"]
    ]

    paid = api_client.get("/api/store/orders", params={"status": "paid"})
    assert {row["order_no"] for row in paid.json()} >= {first, second}

    searched = api_client.get("/api/store/orders", params={"q": first})
    assert [row["order_no"] for row in searched.json()] == [first]

    last_address = api_client.get("/api/store/addresses/last")
    assert last_address.status_code == 200
    address = last_address.json()
    assert address["recipient_name"] == ADDRESS["recipient_name"]
    assert address["detail"] == ADDRESS["detail"]
