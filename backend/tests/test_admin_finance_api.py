from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Refund

ADDRESS = {
    "recipient_name": "财务测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "财务测试路 1 号",
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


def test_finance_summary_pending_and_ledger(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    assert api_client.get("/api/admin/finance").status_code == 401

    login()
    order_no = _paid_order(api_client, "finance-paid-0001")
    refund = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款"}
    )
    proposal_id = refund.json()["proposals"][0]["id"]
    admin_login()
    assert api_client.post(f"/api/admin/proposals/{proposal_id}/approve").status_code == 200

    summary = api_client.get("/api/admin/finance?days=30")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["window_days"] == 30
    assert body["paid_count"] == 1
    assert body["paid_amount_cents"] == 89900
    assert body["refund_count"] == 1
    assert body["refund_amount_cents"] == 89900
    assert body["net_amount_cents"] == 0
    assert body["anomalies"] == []
    assert {row["kind"] for row in body["ledger"]} == {"payment", "refund"}

    second = _paid_order(api_client, "finance-pending-0001")
    pending = api_client.post(
        "/api/chat", json={"text": f"订单 {second} 我要退款"}
    )
    assert pending.status_code == 200
    summary = api_client.get("/api/admin/finance?days=30").json()
    assert summary["pending_proposal_count"] == 1
    assert summary["pending_proposal_amount_cents"] == 89900

    admin_login("agent@test.local", "agent123456")
    assert api_client.get("/api/admin/finance").status_code == 200


def test_finance_detects_over_refund(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order_no = _paid_order(api_client, "finance-anomaly-0001")
    refund = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款 100 元"}
    )
    proposal_id = refund.json()["proposals"][0]["id"]
    admin_login()
    assert api_client.post(f"/api/admin/proposals/{proposal_id}/approve").status_code == 200

    db_session.expire_all()
    row = db_session.execute(select(Refund)).scalar_one()
    row.amount_cents = 90000
    db_session.commit()

    body = api_client.get("/api/admin/finance").json()
    assert any(
        item["order_no"] == order_no
        and item["kind"] == "refund_over_payable"
        for item in body["anomalies"]
    )
