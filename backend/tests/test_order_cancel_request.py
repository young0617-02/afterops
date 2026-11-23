from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    AuditEvent,
    Inventory,
    InventoryMovement,
    InventoryReservation,
    Notification,
)
from app.services.audit import verify_chain

ADDRESS = {
    "recipient_name": "取消测试顾客",
    "recipient_phone": "13800000010",
    "region": "华东",
    "city": "上海",
    "detail": "取消测试路 10 号",
}


def _paid_order(client: TestClient, key: str, paid: bool = True) -> dict:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    if paid:
        assert (
            client.post(
                f"/api/store/payments/{body['payment_id']}/mock-confirm"
            ).status_code
            == 200
        )
    return body


def _request(client: TestClient, order_no: str):
    return client.post(
        f"/api/store/orders/{order_no}/cancel-request"
    )


def test_cancel_request_blocks_shipment_and_reject_releases_it(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order = _paid_order(api_client, "cancel-request-reject")
    response = _request(api_client, order["order_no"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["amount_cents"] == order["amount_cents"]
    assert body["proposal_id"] > 0

    summary = api_client.get(
        f"/api/store/orders/{order['order_no']}"
    ).json()
    assert summary["cancel_requested_at"] is not None
    duplicate = _request(api_client, order["order_no"])
    assert duplicate.status_code == 409

    admin_login()
    blocked = api_client.post(
        f"/api/admin/orders/{order['order_no']}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-CANCEL-{uuid4().hex[:8]}"},
    )
    assert blocked.status_code == 409
    assert "取消申请" in blocked.json()["detail"]

    rejected = api_client.post(
        f"/api/admin/proposals/{body['proposal_id']}/reject",
        json={"reason": "已排产，无法取消"},
    )
    assert rejected.status_code == 200, rejected.text

    after_reject = api_client.get(
        f"/api/store/orders/{order['order_no']}"
    ).json()
    assert after_reject["cancel_requested_at"] is None
    shipped = api_client.post(
        f"/api/admin/orders/{order['order_no']}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-CANCEL-{uuid4().hex[:8]}"},
    )
    assert shipped.status_code == 200, shipped.text

    notifications = db_session.execute(
        select(Notification).where(
            Notification.type == "order_cancel_requested"
        )
    ).scalars().all()
    assert len(notifications) == 2
    events = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type.in_(
                ("order_cancel_requested", "proposal_rejected")
            )
        )
    ).scalars().all()
    assert events == ["order_cancel_requested", "proposal_rejected"]
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_cancel_request_approval_refunds_and_restocks(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order = _paid_order(api_client, "cancel-request-approve")
    order_id = api_client.get(
        f"/api/store/orders/{order['order_no']}"
    ).json()["id"]
    reservation = db_session.execute(
        select(InventoryReservation).where(
            InventoryReservation.order_id == order_id
        )
    ).scalar_one()
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == reservation.product_id,
            Inventory.warehouse_id == reservation.warehouse_id,
        )
    ).scalar_one()
    before = inventory.qty_available
    assert reservation.status == "committed"

    request = _request(api_client, order["order_no"])
    assert request.status_code == 200, request.text
    proposal_id = request.json()["proposal_id"]

    admin_login()
    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"

    db_session.expire_all()
    refunded = api_client.get(
        f"/api/store/orders/{order['order_no']}"
    ).json()
    assert refunded["status"] == "refunded"
    assert refunded["payment_status"] == "refunded"
    assert refunded["cancel_requested_at"] is None

    restocked = db_session.get(Inventory, inventory.id)
    assert restocked is not None
    assert restocked.qty_available == before + 1
    reservation = db_session.get(InventoryReservation, reservation.id)
    assert reservation is not None
    assert reservation.status == "released"

    movement = db_session.execute(
        select(InventoryMovement)
        .where(
            InventoryMovement.order_id == order_id,
            InventoryMovement.reason == "order_cancelled",
        )
        .order_by(InventoryMovement.id.desc())
    ).scalars().first()
    assert movement is not None
    assert movement.qty_delta == 1
    assert movement.note == "已支付取消回补"

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "order_cancel_restocked")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["proposal_id"] == proposal_id
    assert audit.payload_json["lines"][0]["qty"] == 1
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_cancel_request_rejects_unpaid_or_shipped_orders(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    unpaid = _paid_order(api_client, "cancel-request-unpaid", paid=False)
    unpaid_response = _request(api_client, unpaid["order_no"])
    assert unpaid_response.status_code == 409

    paid = _paid_order(api_client, "cancel-request-shipped")
    admin_login()
    shipment = api_client.post(
        f"/api/admin/orders/{paid['order_no']}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-CANCEL-{uuid4().hex[:8]}"},
    )
    assert shipment.status_code == 200, shipment.text
    shipped_response = _request(api_client, paid["order_no"])
    assert shipped_response.status_code == 409