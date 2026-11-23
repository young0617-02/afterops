from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import AuditEvent

ADDRESS = {
    "recipient_name": "审计测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "审计测试路 1 号",
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


def test_admin_lists_filters_and_verifies_audit(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    assert api_client.get("/api/admin/audit").status_code == 401

    login()
    order_no = _paid_order(api_client, "audit-list-0001")
    admin_login()

    response = api_client.get("/api/admin/audit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 2
    assert "order_created" in body["event_types"]
    assert "payment_paid" in body["event_types"]
    assert body["items"]
    assert body["items"][0]["id"] > body["items"][-1]["id"]
    assert any(
        item["payload"].get("order_no") == order_no for item in body["items"]
    )
    assert all(len(item["row_hash"]) == 64 for item in body["items"])
    assert all(
        item["prev_hash"] == "GENESIS" or len(item["prev_hash"]) == 64
        for item in body["items"]
    )
    assert body["items"][-1]["prev_hash"] == "GENESIS"

    filtered = api_client.get(
        "/api/admin/audit", params={"event_type": "payment_paid"}
    )
    assert filtered.status_code == 200
    filtered_body = filtered.json()
    assert filtered_body["items"]
    assert {item["event_type"] for item in filtered_body["items"]} == {
        "payment_paid"
    }
    assert filtered_body["total"] == len(filtered_body["items"])

    first_page = api_client.get("/api/admin/audit", params={"limit": 1})
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 1
    assert first_body["has_more"] is True
    assert first_body["next_before_id"] == first_body["items"][0]["id"]

    second_page = api_client.get(
        "/api/admin/audit",
        params={"limit": 1, "before_id": first_body["next_before_id"]},
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert second_body["items"][0]["id"] < first_body["items"][0]["id"]

    verify = api_client.get("/api/admin/audit/verify")
    assert verify.status_code == 200, verify.text
    verified = verify.json()
    assert verified["valid"] is True
    assert verified["broken_at"] is None
    assert verified["checked_events"] == body["total"]

    searched = api_client.get("/api/admin/audit", params={"q": order_no})
    assert searched.status_code == 200, searched.text
    searched_body = searched.json()
    assert searched_body["items"]
    assert searched_body["total"] == len(searched_body["items"])
    assert all(
        order_no in json.dumps(item["payload"], ensure_ascii=False)
        for item in searched_body["items"]
    )
    empty = api_client.get(
        "/api/admin/audit", params={"q": "NO-SUCH-ORDER"}
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0

    exported = api_client.get(
        "/api/admin/audit/export", params={"q": order_no}
    )
    assert exported.status_code == 200, exported.text
    assert "attachment" in exported.headers["content-disposition"]
    export_body = exported.json()
    assert export_body["audit_valid"] is True
    assert export_body["count"] == len(export_body["events"])
    assert export_body["truncated"] is False
    assert all(
        order_no in json.dumps(event["payload"], ensure_ascii=False)
        for event in export_body["events"]
    )
    assert (
        api_client.get(
            "/api/admin/audit/export", params={"q": "NO-SUCH-ORDER"}
        ).json()["count"]
        == 0
    )

    admin_login("agent@test.local", "agent123456")
    agent_list = api_client.get("/api/admin/audit")
    assert agent_list.status_code == 200
    assert agent_list.json()["items"]


def test_audit_verify_detects_tampering(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    _paid_order(api_client, "audit-tamper-0001")
    admin_login()

    db_session.expire_all()
    target = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "payment_paid")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert target is not None
    target.payload_json = {**target.payload_json, "tampered": True}
    db_session.commit()

    verify = api_client.get("/api/admin/audit/verify")
    assert verify.status_code == 200, verify.text
    body = verify.json()
    assert body["valid"] is False
    assert body["broken_at"] == target.id
    expected_checked = db_session.execute(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.merchant_id == target.merchant_id,
            AuditEvent.id <= target.id,
        )
    ).scalar_one()
    assert body["checked_events"] == expected_checked

    listing = api_client.get(
        "/api/admin/audit", params={"event_type": "payment_paid"}
    )
    assert listing.status_code == 200
    assert listing.json()["items"][0]["payload"]["tampered"] is True
