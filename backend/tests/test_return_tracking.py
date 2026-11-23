from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent

ADDRESS = {
    "recipient_name": "退货物流顾客",
    "recipient_phone": "13800000021",
    "region": "华东",
    "city": "上海",
    "detail": "退货物流路 21 号",
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
        assert (
            client.post(
                f"/api/admin/orders/{order_no}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )


def _approved_return(client: TestClient, order_no: str, key: str) -> int:
    created = client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "退货物流测试"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    tracked = client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": key},
    )
    assert tracked.status_code == 200, tracked.text
    return return_id


def test_return_tracking_events_progress_to_warehouse(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "return-tracking-1")
    admin_login()
    _delivery(api_client, order_no, "SF-OUT-TRACK-1")
    return_id = _approved_return(api_client, order_no, "SF-RETURN-TRACK-1")

    tracked = api_client.get("/api/store/returns").json()[0]
    assert tracked["shipment_status"] == "registered"
    assert [event["status"] for event in tracked["tracking_events"]] == [
        "registered"
    ]
    assert tracked["tracking_events"][0]["label"] == "已登记回寄"

    duplicate = api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-RETURN-CHANGED"},
    )
    assert duplicate.status_code == 409
    assert "不能修改" in duplicate.json()["detail"]

    skip = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/advance",
        json={"status": "delivered"},
    )
    assert skip.status_code == 409
    assert "不能从已登记回寄变更为已到达退货仓" in skip.json()["detail"]

    for status, label in (
        ("picked_up", "已揽收"),
        ("in_transit", "运输中"),
        ("out_for_delivery", "派送中"),
        ("delivered", "已到达退货仓"),
    ):
        advanced = api_client.post(
            f"/api/admin/returns/{return_id}/tracking/advance",
            json={"status": status},
        )
        assert advanced.status_code == 200, advanced.text
        assert advanced.json()["shipment_status"] == status
        assert advanced.json()["tracking_events"][-1]["label"] == label

    finished = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/advance",
        json={"status": "delivered"},
    )
    assert finished.status_code == 409
    assert "已到达退货仓" in finished.json()["detail"]

    customer_view = api_client.get("/api/store/returns").json()[0]
    assert customer_view["shipment_status"] == "delivered"
    assert [event["status"] for event in customer_view["tracking_events"]] == [
        "registered",
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
    ]

    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "received"

    db_session.expire_all()
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_tracking_advanced")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["status"] == "delivered"
    assert audit.payload_json["tracking_no"] == "SF-RETURN-TRACK-1"


def test_return_tracking_requires_owner_and_tracking_number(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "return-tracking-guard")
    admin_login()
    _delivery(api_client, order_no, "SF-OUT-TRACK-2")
    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "物流校验"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )

    no_tracking = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/advance",
        json={"status": "picked_up"},
    )
    assert no_tracking.status_code == 409
    assert "还没有回寄单号" in no_tracking.json()["detail"]

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/advance",
        json={"status": "picked_up"},
    )
    assert forbidden.status_code == 403

    admin_login()
    invalid = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/advance",
        json={"status": "lost"},
    )
    assert invalid.status_code == 422
    missing = api_client.post(
        "/api/admin/returns/999999/tracking/advance",
        json={"status": "picked_up"},
    )
    assert missing.status_code == 404


def test_return_tracking_exception_is_recorded_without_stopping_progress(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    from app.domain.models import Notification

    login()
    order_no = _paid_order(api_client, "return-tracking-exception")
    admin_login()
    _delivery(api_client, order_no, "SF-OUT-TRACK-3")
    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "物流异常测试"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )

    no_tracking = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/exception",
        json={"description": "还没有单号"},
    )
    assert no_tracking.status_code == 409
    assert "还没有回寄单号" in no_tracking.json()["detail"]

    tracked = api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-RETURN-EXCEPTION"},
    )
    assert tracked.status_code == 200, tracked.text

    blank = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/exception",
        json={"description": "   "},
    )
    assert blank.status_code == 422
    exception = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/exception",
        json={"description": "包裹外箱破损，承运商拍照留证"},
    )
    assert exception.status_code == 200, exception.text
    body = exception.json()
    assert body["shipment_status"] == "registered"
    assert body["tracking_events"][-1]["status"] == "exception"
    assert body["tracking_events"][-1]["label"] == "物流异常"
    assert "外箱破损" in body["tracking_events"][-1]["description"]

    advanced = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/advance",
        json={"status": "picked_up"},
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["shipment_status"] == "picked_up"
    assert [event["status"] for event in advanced.json()["tracking_events"]] == [
        "registered",
        "exception",
        "picked_up",
    ]

    db_session.expire_all()
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_tracking_exception")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["return_id"] == return_id
    assert audit.payload_json["shipment_status"] == "registered"
    assert "外箱破损" in audit.payload_json["description"]
    notes = db_session.execute(
        select(Notification).where(
            Notification.type == "return_tracking_exception"
        )
    ).scalars().all()
    assert len(notes) == 1

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/exception",
        json={"description": "客服登记异常"},
    )
    assert forbidden.status_code == 403
