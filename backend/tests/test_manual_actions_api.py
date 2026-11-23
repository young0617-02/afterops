from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import ActionProposal, AuditEvent, Refund
from app.services.audit import verify_chain

ADDRESS = {
    "recipient_name": "手动退款顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "手动退款路 1 号",
}


def _paid_order(client: TestClient, key: str) -> dict:
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
    return body


def test_owner_creates_partial_manual_refund_and_approves(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _paid_order(api_client, "manual-refund-0001")
    admin_login()

    created = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "refund",
            "order_no": order["order_no"],
            "amount_cents": 10000,
            "reason": "电话协商部分退款",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["amount_cents"] == 10000
    assert body["amount_display"] == "100.00"
    proposal_id = body["proposal_id"]

    detail = api_client.get(f"/api/store/orders/{order['order_no']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "paid"

    proposals = api_client.get("/api/admin/proposals?status=pending").json()
    proposal = next(row for row in proposals if row["id"] == proposal_id)
    assert proposal["created_by"] == "staff"
    assert proposal["order_no"] == order["order_no"]

    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"

    detail = api_client.get(f"/api/store/orders/{order['order_no']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "partially_refunded"
    assert detail.json()["payment"]["status"] == "partially_refunded"

    db_session.expire_all()
    refunds = db_session.execute(select(Refund)).scalars().all()
    assert len(refunds) == 1
    assert refunds[0].amount_cents == 10000
    proposal_row = db_session.get(ActionProposal, proposal_id)
    assert proposal_row is not None
    assert proposal_row.created_by == "staff"
    event_types = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type.in_(
                ("proposal_created", "proposal_approved", "proposal_executed")
            )
        )
    ).scalars().all()
    assert set(event_types) == {
        "proposal_created",
        "proposal_approved",
        "proposal_executed",
    }
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_manual_refund_requires_owner_and_valid_payment(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order = _paid_order(api_client, "manual-refund-0002")

    admin_login("agent@test.local", "agent123456")
    assert (
        api_client.post(
            "/api/admin/action-proposals",
            json={"action_type": "refund", "order_no": order["order_no"], "amount_cents": 1000},
        ).status_code
        == 403
    )

    admin_login()
    too_much = api_client.post(
        "/api/admin/action-proposals",
        json={"action_type": "refund", "order_no": order["order_no"], "amount_cents": 90000},
    )
    assert too_much.status_code == 409
    assert "可退上限" in too_much.json()["detail"]

    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    pending = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "manual-refund-unpaid"},
    )
    assert pending.status_code == 201
    unpaid_order = pending.json()
    invalid = api_client.post(
        "/api/admin/action-proposals",
        json={"action_type": "refund", "order_no": unpaid_order["order_no"], "amount_cents": 1000},
    )
    assert invalid.status_code == 409
    assert "未支付" in invalid.json()["detail"]

    zero = api_client.post(
        "/api/admin/action-proposals",
        json={"action_type": "refund", "order_no": order["order_no"], "amount_cents": 0},
    )
    assert zero.status_code == 422


def test_owner_creates_manual_compensation(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _paid_order(api_client, "manual-compensation-0001")
    admin_login()
    created = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "compensation",
            "order_no": order["order_no"],
            "amount_cents": 5000,
            "reason": "延误补偿",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["action_type"] == "compensation"
    assert body["status"] == "pending"
    assert body["amount_display"] == "50.00"

    approved = api_client.post(
        f"/api/admin/proposals/{body['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    detail = api_client.get(f"/api/store/orders/{order['order_no']}")
    assert detail.json()["status"] == "paid"

    db_session.expire_all()
    refunds = db_session.execute(select(Refund)).scalars().all()
    assert len(refunds) == 1
    assert refunds[0].kind == "compensation"
    assert refunds[0].amount_cents == 5000


def test_owner_creates_manual_address_change(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order = _paid_order(api_client, "manual-address-0001")
    admin_login()
    missing = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "address_change",
            "order_no": order["order_no"],
        },
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "validation_error"

    created = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "address_change",
            "order_no": order["order_no"],
            "new_address_text": "上海市浦东新区人工改址路 88 号",
            "reason": "电话协商改地址",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["action_type"] == "address_change"
    assert body["risk_level"] == "L1"
    assert body["amount_cents"] is None

    approved = api_client.post(
        f"/api/admin/proposals/{body['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    detail = api_client.get(f"/api/store/orders/{order['order_no']}")
    assert detail.json()["status"] == "paid"
    assert detail.json()["address"]["detail"] == "上海市浦东新区人工改址路 88 号"
