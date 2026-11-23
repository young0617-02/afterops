from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Notification, RepairRequest

ADDRESS = {
    "recipient_name": "维修报价顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "维修报价路 1 号",
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
        response = client.post(
            f"/api/admin/orders/{order_no}/shipment/advance",
            json={"status": status},
        )
        assert response.status_code == 200, response.text


def _received_repair(client: TestClient, key: str) -> tuple[str, int]:
    order_no = _paid_order(client, key)
    _delivery(client, order_no, f"SF-QUOTE-{key.upper()}")
    created = client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "右耳无声",
        },
    )
    assert created.status_code == 200, created.text
    repair_id = created.json()["id"]
    assert (
        client.post(f"/api/admin/repairs/{repair_id}/approve").status_code
        == 200
    )
    received = client.post(f"/api/admin/repairs/{repair_id}/receive")
    assert received.status_code == 200, received.text
    return order_no, repair_id


def _quote(client: TestClient, repair_id: int, amount_cents: int = 5000) -> dict:
    response = client.post(
        f"/api/admin/repairs/{repair_id}/quote",
        json={"amount_cents": amount_cents, "note": "更换主板"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_repair_quote_accept_pay_and_complete(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, repair_id = _received_repair(api_client, "quote-flow-0001")
    quoted = _quote(api_client, repair_id)
    assert quoted["quote_status"] == "pending"
    assert quoted["quote_cents"] == 5000
    assert quoted["quote_note"] == "更换主板"
    assert quoted["quote_created_at"] is not None
    assert quoted["quote_responded_at"] is None

    blocked = api_client.post(
        f"/api/admin/repairs/{repair_id}/complete",
        json={
            "resolution_note": "已更换主板",
            "return_carrier": "顺丰速运",
            "return_tracking_no": "SF-QUOTE-BACK-0001",
        },
    )
    assert blocked.status_code == 409
    assert "报价" in blocked.json()["detail"]

    accepted = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/accept"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["quote_status"] == "accepted"
    assert accepted.json()["quote_responded_at"] is not None

    unpaid = api_client.post(
        f"/api/admin/repairs/{repair_id}/complete",
        json={
            "resolution_note": "已更换主板",
            "return_carrier": "顺丰速运",
            "return_tracking_no": "SF-QUOTE-BACK-0001",
        },
    )
    assert unpaid.status_code == 409

    paid = api_client.post(f"/api/store/repairs/{repair_id}/quote/pay")
    assert paid.status_code == 200, paid.text
    paid_body = paid.json()
    assert paid_body["quote_status"] == "paid"
    assert paid_body["quote_paid_at"] is not None
    assert paid_body["quote_payment_ref"].startswith("RPQ-")

    timeline = api_client.get("/api/store/after-sales/timeline")
    assert timeline.status_code == 200, timeline.text
    titles = [row["title"] for row in timeline.json()]
    assert "维修报价已发送" in titles
    assert "维修报价已接受" in titles
    assert "维修费已支付" in titles

    completed = api_client.post(
        f"/api/admin/repairs/{repair_id}/complete",
        json={
            "resolution_note": "已更换主板并测试正常",
            "return_carrier": "顺丰速运",
            "return_tracking_no": "SF-QUOTE-BACK-0001",
            "parts_cost_cents": 3000,
            "parts_note": "主板配件",
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "return_shipped"
    assert completed.json()["parts_cost_cents"] == 3000
    assert completed.json()["parts_note"] == "主板配件"

    admin_list = api_client.get(
        "/api/admin/repairs", params={"status": "all"}
    )
    assert admin_list.status_code == 200, admin_list.text
    admin_row = next(
        item for item in admin_list.json() if item["id"] == repair_id
    )
    assert admin_row["parts_cost_cents"] == 3000
    assert admin_row["parts_note"] == "主板配件"

    customer_list = api_client.get("/api/store/repairs")
    assert customer_list.status_code == 200, customer_list.text
    customer_row = next(
        item for item in customer_list.json() if item["id"] == repair_id
    )
    assert customer_row["parts_cost_cents"] is None
    assert customer_row["parts_note"] is None

    ledger = api_client.get(
        "/api/admin/finance/after-sales-charges?days=30"
    )
    assert ledger.status_code == 200, ledger.text
    ledger_body = ledger.json()
    assert ledger_body["repair_fee_cents"] == 5000
    assert ledger_body["repair_parts_cost_cents"] == 3000
    assert ledger_body["repair_margin_cents"] == 2000
    assert ledger_body["repair_margin_display"] == "20.00"

    db_session.expire_all()
    stored = db_session.execute(select(RepairRequest)).scalar_one()
    assert stored.quote_status == "paid"
    assert stored.quote_cents == 5000
    assert stored.parts_cost_cents == 3000
    assert stored.parts_note == "主板配件"
    events = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type.like("repair_quote_%")
        )
    ).scalars().all()
    assert set(events) == {
        "repair_quote_issued",
        "repair_quote_accepted",
        "repair_quote_paid",
    }
    issued_notice = db_session.execute(
        select(Notification)
        .where(Notification.type == "repair_quote_issued")
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert issued_notice is not None
    assert "50.00" in issued_notice.body


def test_repair_quote_decline_allows_return_without_payment(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, repair_id = _received_repair(api_client, "quote-decline-0001")
    _quote(api_client, repair_id, 3000)
    declined = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/decline"
    )
    assert declined.status_code == 200, declined.text
    assert declined.json()["quote_status"] == "declined"

    completed = api_client.post(
        f"/api/admin/repairs/{repair_id}/complete",
        json={
            "resolution_note": "顾客拒绝报价，原样寄回",
            "return_carrier": "顺丰速运",
            "return_tracking_no": "SF-QUOTE-DECLINE-BACK",
        },
    )
    assert completed.status_code == 200, completed.text
    db_session.expire_all()
    stored = db_session.execute(select(RepairRequest)).scalar_one()
    assert stored.quote_status == "declined"
    assert stored.quote_paid_at is None


def test_repair_quote_validations(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    order_no = _paid_order(api_client, "quote-validate-0001")
    _delivery(api_client, order_no, "SF-QUOTE-VALIDATE-0001")
    created = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "按键失灵",
        },
    )
    assert created.status_code == 200, created.text
    repair_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/approve").status_code
        == 200
    )

    before_receive = api_client.post(
        f"/api/admin/repairs/{repair_id}/quote",
        json={"amount_cents": 1000, "note": "未收货报价"},
    )
    assert before_receive.status_code == 409
    assert "已收货" in before_receive.json()["detail"]

    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/receive").status_code
        == 200
    )
    zero = api_client.post(
        f"/api/admin/repairs/{repair_id}/quote",
        json={"amount_cents": 0, "note": "零元"},
    )
    assert zero.status_code == 422
    empty_note = api_client.post(
        f"/api/admin/repairs/{repair_id}/quote",
        json={"amount_cents": 1000, "note": ""},
    )
    assert empty_note.status_code == 422

    _quote(api_client, repair_id)
    pay_before_accept = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/pay"
    )
    assert pay_before_accept.status_code == 409
    assert (
        api_client.post(
            f"/api/store/repairs/{repair_id}/quote/accept"
        ).status_code
        == 200
    )
    accept_again = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/accept"
    )
    assert accept_again.status_code == 409

    login("13800000002")
    denied = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/pay"
    )
    assert denied.status_code == 404
    db_session.expire_all()
    stored = db_session.execute(select(RepairRequest)).scalar_one()
    assert stored.quote_status == "accepted"
    assert stored.quote_paid_at is None


def test_repair_quote_can_be_revised_before_confirmation(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, repair_id = _received_repair(
        api_client, "quote-revision-0001"
    )
    first = _quote(api_client, repair_id, 5000)
    first_created_at = first["quote_created_at"]
    assert first["quote_revised_at"] is None

    pending_revision = _quote(api_client, repair_id, 6000)
    assert pending_revision["quote_status"] == "pending"
    assert pending_revision["quote_cents"] == 6000
    assert pending_revision["quote_created_at"].rstrip("Z") == (
        first_created_at.rstrip("Z")
    )
    assert pending_revision["quote_revised_at"] is not None
    assert pending_revision["quote_responded_at"] is None

    declined = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/decline"
    )
    assert declined.status_code == 200, declined.text
    assert declined.json()["quote_status"] == "declined"

    declined_revision = _quote(api_client, repair_id, 8000)
    assert declined_revision["quote_status"] == "pending"
    assert declined_revision["quote_cents"] == 8000
    assert declined_revision["quote_responded_at"] is None
    assert declined_revision["quote_revised_at"] is not None

    db_session.expire_all()
    stored = db_session.get(RepairRequest, repair_id)
    assert stored is not None
    assert stored.quote_status == "pending"
    assert stored.quote_cents == 8000
    assert stored.quote_reminded_at is None

    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "repair_quote_revised"
        )
    ).scalars().all()
    assert len(events) == 2
    assert events[0].payload_json["old_amount_cents"] == 5000
    assert events[0].payload_json["amount_cents"] == 6000
    assert events[1].payload_json["old_amount_cents"] == 6000
    assert events[1].payload_json["amount_cents"] == 8000

    updated_notifications = db_session.execute(
        select(Notification).where(
            Notification.type == "repair_quote_updated"
        )
    ).scalars().all()
    assert len(updated_notifications) == 2

    timeline = api_client.get("/api/store/after-sales/timeline")
    assert timeline.status_code == 200, timeline.text
    titles = [row["title"] for row in timeline.json()]
    assert "维修报价已更新" in titles
    assert "维修报价已发送" not in titles

    accepted = api_client.post(
        f"/api/store/repairs/{repair_id}/quote/accept"
    )
    assert accepted.status_code == 200, accepted.text
    paid = api_client.post(f"/api/store/repairs/{repair_id}/quote/pay")
    assert paid.status_code == 200, paid.text
    assert paid.json()["quote_cents"] == 8000

    blocked = api_client.post(
        f"/api/admin/repairs/{repair_id}/quote",
        json={"amount_cents": 9000, "note": "已支付后改价"},
    )
    assert blocked.status_code == 409
    assert "不能修改" in blocked.json()["detail"]
