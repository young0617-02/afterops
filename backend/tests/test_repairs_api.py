from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Shipment

ADDRESS = {
    "recipient_name": "维修测试顾客",
    "recipient_phone": "13800000007",
    "region": "华东",
    "city": "上海",
    "detail": "维修测试路 7 号",
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


def _delivery(client: TestClient, order_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-REP-{uuid4().hex[:8]}"},
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


def test_repair_full_flow_and_duplicate_guard(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "repair-flow")
    admin_login()
    _delivery(api_client, order_no)

    created = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "左耳无声",
        },
    )
    assert created.status_code == 200, created.text
    repair = created.json()
    repair_id = repair["id"]
    assert repair["status"] == "requested"
    assert repair["sku"] == "SKU-EAR-001"
    assert repair["title"] == "降噪耳机 Pro"

    duplicate = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "重复申请",
        },
    )
    assert duplicate.status_code == 409
    assert "进行中" in duplicate.json()["detail"]

    listed = api_client.get("/api/admin/repairs")
    assert listed.status_code == 200, listed.text
    assert any(row["id"] == repair_id for row in listed.json())

    approved = api_client.post(f"/api/admin/repairs/{repair_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    tracking = api_client.post(
        f"/api/store/repairs/{repair_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-REP-BACK"},
    )
    assert tracking.status_code == 200, tracking.text
    assert tracking.json()["status"] == "in_transit"

    received = api_client.post(f"/api/admin/repairs/{repair_id}/receive")
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "received"

    missing_tracking = api_client.post(
        f"/api/admin/repairs/{repair_id}/complete",
        json={"resolution_note": "已更换电池并回寄"},
    )
    assert missing_tracking.status_code == 422

    early_confirm = api_client.post(
        f"/api/store/repairs/{repair_id}/confirm-receipt"
    )
    assert early_confirm.status_code == 409
    assert "已寄回" in early_confirm.json()["detail"]

    completed = api_client.post(
        f"/api/admin/repairs/{repair_id}/complete",
        json={
            "resolution_note": "已更换电池并回寄",
            "return_carrier": "顺丰速运",
            "return_tracking_no": "SF-REPAIR-OUT-001",
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "return_shipped"
    assert completed.json()["resolution_note"] == "已更换电池并回寄"
    assert completed.json()["return_carrier"] == "顺丰速运"
    assert completed.json()["return_tracking_no"] == "SF-REPAIR-OUT-001"
    assert completed.json()["repaired_at"] is not None

    customer_rows = api_client.get("/api/store/repairs").json()
    row = next(item for item in customer_rows if item["id"] == repair_id)
    assert row["status"] == "return_shipped"
    assert row["return_tracking_no"] == "SF-REPAIR-OUT-001"

    confirmed = api_client.post(
        f"/api/store/repairs/{repair_id}/confirm-receipt"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"
    assert confirmed.json()["completed_at"] is not None
    duplicate_confirm = api_client.post(
        f"/api/store/repairs/{repair_id}/confirm-receipt"
    )
    assert duplicate_confirm.status_code == 409

    customer_rows = api_client.get("/api/store/repairs").json()
    row = next(item for item in customer_rows if item["id"] == repair_id)
    assert row["status"] == "completed"

    event_types = set(
        db_session.execute(
            select(AuditEvent.event_type).where(
                AuditEvent.event_type.like("repair_%")
            )
        ).scalars().all()
    )
    assert {
        "repair_requested",
        "repair_approved",
        "repair_tracking_added",
        "repair_received",
        "repair_completed",
        "repair_customer_received",
    } <= event_types


def test_repair_guards_warranty_and_permissions(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "repair-guards")
    admin_login()

    not_delivered = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "未签收申请",
        },
    )
    assert not_delivered.status_code == 409
    assert "已签收" in not_delivered.json()["detail"]

    _delivery(api_client, order_no)
    wrong_sku = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-PWR-001",
            "issue": "错误商品",
        },
    )
    assert wrong_sku.status_code == 422
    assert "不属于该订单" in wrong_sku.json()["detail"]

    shipment = db_session.execute(select(Shipment)).scalar_one()
    shipment.delivered_at = datetime(2025, 1, 1, tzinfo=UTC)
    db_session.commit()
    expired = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "过期申请",
        },
    )
    assert expired.status_code == 409
    assert "保修期" in expired.json()["detail"]

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert api_client.get("/api/admin/repairs").status_code == 200
    assert api_client.post("/api/admin/repairs/1/approve").status_code == 403
