from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AfterSalesCase, AuditEvent
from app.main import app

ADDRESS = {
    "recipient_name": "售后进度顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "售后进度路 1 号",
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
    proposals = chat.json()["proposals"]
    assert len(proposals) == 1
    return proposals[0]["id"]


def test_customer_after_sales_status_and_rejection(
    api_client: TestClient, shop_data, login, admin_login, otp_login
) -> None:
    assert api_client.get("/api/store/after-sales").status_code == 401

    login()
    first_order = _paid_order(api_client, "after-sales-paid-0001")
    first_id = _refund_proposal(api_client, first_order)

    listed = api_client.get("/api/store/after-sales")
    assert listed.status_code == 200, listed.text
    first = listed.json()[0]
    assert first["id"] == first_id
    assert first["status"] == "pending"
    assert first["order_no"] == first_order
    assert first["amount_display"] == "899.00"
    assert first["message"] is None

    admin_login()
    approved = api_client.post(f"/api/admin/proposals/{first_id}/approve")
    assert approved.status_code == 200, approved.text
    executed = api_client.get("/api/store/after-sales").json()[0]
    assert executed["status"] == "executed"
    assert executed["commerce_ref"]
    assert executed["executed_at"] is not None
    assert executed["message"] is None

    second_order = _paid_order(api_client, "after-sales-paid-0002")
    second_id = _refund_proposal(api_client, second_order)
    rejected = api_client.post(
        f"/api/admin/proposals/{second_id}/reject",
        json={"reason": "凭证不足"},
    )
    assert rejected.status_code == 200, rejected.text
    rejected_item = next(
        item
        for item in api_client.get("/api/store/after-sales").json()
        if item["id"] == second_id
    )
    assert rejected_item["status"] == "rejected"
    assert rejected_item["message"] == "凭证不足"

    with TestClient(app) as other:
        otp_login(other, "13900000009")
        other_items = other.get("/api/store/after-sales")
        assert other_items.status_code == 200
        assert other_items.json() == []


def test_customer_handoff_status_is_visible_only_to_owner(
    api_client: TestClient, shop_data, login, admin_login, otp_login
) -> None:
    login()
    chat = api_client.post("/api/chat", json={"text": "转人工"})
    assert chat.status_code == 200, chat.text
    ticket_ref = chat.json()["handoff"]["ticket_ref"]
    assert ticket_ref.startswith("TK-")

    mine = api_client.get("/api/store/handoffs")
    assert mine.status_code == 200, mine.text
    ticket = mine.json()[0]
    assert ticket["ticket_ref"] == ticket_ref
    assert ticket["status"] == "open"
    assert ticket["last_user_message"] == "转人工"

    admin_login()
    admin_ticket_id = api_client.get("/api/admin/handoffs").json()["items"][0]["id"]
    assert (
        api_client.post(f"/api/admin/handoffs/{admin_ticket_id}/claim").status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/admin/handoffs/{admin_ticket_id}/resolve",
            json={"note": "售后进度页验证"},
        ).status_code
        == 200
    )

    resolved = api_client.get("/api/store/handoffs").json()[0]
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None

    with TestClient(app) as other:
        otp_login(other, "13900000010")
        other_tickets = other.get("/api/store/handoffs")
        assert other_tickets.status_code == 200
        assert other_tickets.json() == []


def test_customer_can_withdraw_pending_after_sales(
    api_client: TestClient, shop_data, login, admin_login, otp_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "after-sales-withdraw-0001")
    proposal_id = _refund_proposal(api_client, order_no)

    with TestClient(app) as other:
        otp_login(other, "13900000012")
        denied = other.post(f"/api/store/after-sales/{proposal_id}/withdraw")
        assert denied.status_code == 404

    withdrawn = api_client.post(f"/api/store/after-sales/{proposal_id}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json() == {"id": proposal_id, "status": "withdrawn"}
    item = next(
        row
        for row in api_client.get("/api/store/after-sales").json()
        if row["id"] == proposal_id
    )
    assert item["status"] == "withdrawn"

    db_session.expire_all()
    case = db_session.execute(select(AfterSalesCase)).scalar_one()
    assert case.status == "withdrawn"
    event = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "proposal_withdrawn")
    ).scalar_one()
    assert event.payload_json["proposal_id"] == proposal_id

    again = api_client.post(f"/api/store/after-sales/{proposal_id}/withdraw")
    assert again.status_code == 409

    admin_login()
    assert api_client.get("/api/admin/proposals?status=pending").json() == []
    approve = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approve.status_code == 409


def test_customer_adds_evidence_to_pending_proposal(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    order_no = _paid_order(api_client, "after-sales-evidence-0001")
    proposal_id = _refund_proposal(api_client, order_no)
    upload = api_client.post(
        "/api/store/attachments",
        files={
            "file": (
                "evidence.png",
                bytes.fromhex("89504e470d0a1a0a") + b"evidence",
                "image/png",
            )
        },
    )
    assert upload.status_code == 200, upload.text
    attachment_id = upload.json()["id"]

    attached = api_client.post(
        f"/api/store/after-sales/{proposal_id}/attachments",
        json={"attachment_ids": [attachment_id]},
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["attachment_ids"] == [attachment_id]

    mine = next(
        row
        for row in api_client.get("/api/store/after-sales").json()
        if row["id"] == proposal_id
    )
    assert mine["attachment_ids"] == [attachment_id]

    admin_login()
    listed = api_client.get(
        "/api/admin/proposals", params={"q": str(proposal_id)}
    ).json()
    assert listed[0]["attachment_ids"] == [attachment_id]
    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "proposal_evidence_added"
        )
    ).scalar_one()
    assert event.payload_json["proposal_id"] == proposal_id
