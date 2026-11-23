from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    AuditEvent,
    Order,
    Payment,
    Refund,
)
from app.services.audit import verify_chain
from app.settings import settings

ADDRESS = {
    "recipient_name": "提案顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "提案测试路 1 号",
}


def _paid_order(client: TestClient, key: str) -> dict:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 2})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code == 200
    return body


def _pending_order(client: TestClient, key: str) -> dict:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 2})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    return checkout.json()


def test_refund_proposal_requires_human_approval(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _paid_order(api_client, "proposal-refund-0001")
    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款"}
    )
    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert body["intent"] == "REFUND_REQUEST"
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["status"] == "pending"
    assert "已提交退款申请" in body["reply"]
    assert "等待人工审核" in body["reply"]
    assert "已退款" not in body["reply"]

    db_session.expire_all()
    proposal = db_session.execute(select(ActionProposal)).scalar_one()
    assert proposal.status == "pending"
    assert db_session.execute(select(Refund)).scalars().all() == []
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order["order_no"])
    ).scalar_one()
    payment = db_session.execute(
        select(Payment).where(Payment.id == order["payment_id"])
    ).scalar_one()
    assert order_row.status == "paid"
    assert payment.status == "paid"

    admin_login()
    listed = api_client.get("/api/admin/proposals")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [proposal.id]

    approved = api_client.post(f"/api/admin/proposals/{proposal.id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"

    db_session.expire_all()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order["order_no"])
    ).scalar_one()
    payment = db_session.execute(
        select(Payment).where(Payment.id == order["payment_id"])
    ).scalar_one()
    refund = db_session.execute(select(Refund)).scalar_one()
    case = db_session.execute(select(AfterSalesCase)).scalar_one()
    assert order_row.status == "refunded"
    assert payment.status == "refunded"
    assert refund.kind == "refund"
    assert refund.amount_cents == 179800
    assert case.status == "executed"

    again = api_client.post(f"/api/admin/proposals/{proposal.id}/approve")
    assert again.status_code == 409

    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type.like("proposal_%"))
    ).scalars().all()
    assert [event.event_type for event in events] == [
        "proposal_created",
        "proposal_approved",
        "proposal_executed",
    ]


def test_partial_refund_then_remaining_refund(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _paid_order(api_client, "proposal-partial-0001")

    first_chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款 100 元"}
    ).json()
    assert first_chat["proposals"][0]["amount_cents"] == 10000
    first_id = first_chat["proposals"][0]["id"]
    admin_login()
    assert api_client.post(f"/api/admin/proposals/{first_id}/approve").status_code == 200

    db_session.expire_all()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order["order_no"])
    ).scalar_one()
    payment = db_session.execute(
        select(Payment).where(Payment.id == order["payment_id"])
    ).scalar_one()
    assert order_row.status == "partially_refunded"
    assert payment.status == "partially_refunded"

    second_chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款 1698 元"}
    ).json()
    assert second_chat["proposals"][0]["amount_cents"] == 169800
    second_id = second_chat["proposals"][0]["id"]
    assert api_client.post(f"/api/admin/proposals/{second_id}/approve").status_code == 200

    db_session.expire_all()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order["order_no"])
    ).scalar_one()
    payment = db_session.execute(
        select(Payment).where(Payment.id == order["payment_id"])
    ).scalar_one()
    refunds = db_session.execute(select(Refund)).scalars().all()
    assert order_row.status == "refunded"
    assert payment.status == "refunded"
    assert sorted(refund.amount_cents for refund in refunds) == [10000, 169800]


def test_reject_proposal_leaves_money_untouched(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _paid_order(api_client, "proposal-refund-0002")
    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款"}
    ).json()
    proposal_id = chat["proposals"][0]["id"]

    admin_login()
    rejected = api_client.post(
        f"/api/admin/proposals/{proposal_id}/reject",
        json={"reason": "凭证不足"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    db_session.expire_all()
    assert db_session.execute(select(Refund)).scalars().all() == []
    payment = db_session.execute(
        select(Payment).where(Payment.id == order["payment_id"])
    ).scalar_one()
    assert payment.status == "paid"
    case = db_session.execute(select(AfterSalesCase)).scalar_one()
    assert case.status == "rejected"


def test_compensation_proposal_executes_without_changing_order_status(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _paid_order(api_client, "proposal-comp-0001")
    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 赔我 100 元"}
    ).json()
    assert chat["intent"] == "COMPENSATION_REQUEST"
    assert chat["proposals"][0]["amount_cents"] == 10000
    proposal_id = chat["proposals"][0]["id"]

    admin_login()
    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200

    db_session.expire_all()
    refund = db_session.execute(select(Refund)).scalar_one()
    order_row = db_session.execute(
        select(Order).where(Order.order_no == order["order_no"])
    ).scalar_one()
    payment = db_session.execute(
        select(Payment).where(Payment.id == order["payment_id"])
    ).scalar_one()
    assert refund.kind == "compensation"
    assert refund.amount_cents == 10000
    assert order_row.status == "paid"
    assert payment.status == "paid"


def test_address_change_proposal_updates_after_approval(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _pending_order(api_client, "proposal-address-0001")
    chat = api_client.post(
        "/api/chat",
        json={
            "text": f"订单 {order['order_no']} 改地址，改成 上海市浦东新区新地址 88 号"
        },
    ).json()
    assert chat["intent"] == "ADDRESS_CHANGE"
    proposal_id = chat["proposals"][0]["id"]

    db_session.expire_all()
    before = db_session.execute(
        select(Order).where(Order.order_no == order["order_no"])
    ).scalar_one()
    assert before.address_detail == "提案测试路 1 号"

    admin_login()
    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200

    db_session.expire_all()
    after = db_session.execute(
        select(Order).where(Order.order_no == order["order_no"])
    ).scalar_one()
    assert after.address_detail == "上海市浦东新区新地址 88 号"
    assert after.status == "pending_payment"
    assert db_session.execute(select(Refund)).scalars().all() == []


def test_admin_requires_login(api_client: TestClient, shop_data) -> None:
    assert api_client.get("/api/admin/proposals").status_code == 401

    bad = api_client.post(
        "/api/admin/session",
        json={"email": "owner@test.local", "password": "wrongpass"},
    )
    assert bad.status_code == 401

    ok = api_client.post(
        "/api/admin/session",
        json={
            "email": "owner@test.local",
            "password": settings.demo_owner_password,
        },
    )
    assert ok.status_code == 200
    assert ok.json()["role"] == "owner"
    assert api_client.get("/api/admin/proposals").status_code == 200


def test_agent_cannot_approve(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _paid_order(api_client, "proposal-agent-0001")
    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款"}
    ).json()
    proposal_id = chat["proposals"][0]["id"]

    admin_login("agent@test.local", "agent123456")
    assert api_client.get("/api/admin/proposals").status_code == 200
    assert api_client.post(
        f"/api/admin/proposals/{proposal_id}/approve"
    ).status_code == 403


def test_other_customer_cannot_create_proposal(
    api_client: TestClient, shop_data, login, otp_login, db_session
) -> None:
    from app.main import app

    login()
    order = _paid_order(api_client, "proposal-owner-0001")

    with TestClient(app) as other:
        otp_login(other, "13900000007")
        resp = other.post(
            "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["proposals"] == []
        assert "已提交退款申请" not in body["reply"]

    db_session.expire_all()
    assert db_session.execute(select(ActionProposal)).scalars().all() == []


def test_proposal_search_and_pagination(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    first_order = _paid_order(api_client, "proposal-search-0001")["order_no"]
    second_order = _paid_order(api_client, "proposal-search-0002")["order_no"]
    first = api_client.post(
        "/api/chat", json={"text": f"订单 {first_order} 我要退款"}
    ).json()["proposals"][0]["id"]
    second = api_client.post(
        "/api/chat", json={"text": f"订单 {second_order} 我要退款"}
    ).json()["proposals"][0]["id"]
    admin_login()

    searched = api_client.get(
        "/api/admin/proposals", params={"q": first_order}
    )
    assert searched.status_code == 200, searched.text
    assert [row["id"] for row in searched.json()] == [first]

    by_id = api_client.get("/api/admin/proposals", params={"q": str(second)})
    assert [row["id"] for row in by_id.json()] == [second]

    page = api_client.get(
        "/api/admin/proposals",
        params={"status": "pending", "limit": 1},
    )
    assert len(page.json()) == 1
    cursor = page.json()[0]["id"]
    next_page = api_client.get(
        "/api/admin/proposals",
        params={"status": "pending", "limit": 1, "before_id": cursor},
    )
    assert len(next_page.json()) == 1
    assert next_page.json()[0]["id"] < cursor
