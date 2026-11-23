from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent
from app.services.audit import verify_chain

ADDRESS = {
    "recipient_name": "审计顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "审计测试路 1 号",
}


def _checkout(client: TestClient, key: str) -> dict:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 2})
    resp = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _events(db_session, merchant_id: int) -> list[AuditEvent]:
    db_session.expire_all()
    return db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.merchant_id == merchant_id)
        .order_by(AuditEvent.id)
    ).scalars().all()


def test_checkout_and_payment_write_audit_chain(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    body = _checkout(api_client, "audit-checkout-0001")
    assert api_client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code == 200

    events = _events(db_session, shop_data["merchant"].id)
    assert [event.event_type for event in events] == ["order_created", "payment_paid"]
    assert events[0].payload_json["order_no"] == body["order_no"]
    assert events[0].prev_hash == "GENESIS"
    assert events[1].prev_hash == events[0].row_hash
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_audit_chain_detects_tampering(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    body = _checkout(api_client, "audit-checkout-0002")
    api_client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")
    events = _events(db_session, shop_data["merchant"].id)
    events[1].row_hash = "tampered"
    db_session.commit()

    ok, detail = verify_chain(db_session, shop_data["merchant"].id)
    assert ok is False
    assert detail == events[1].id


def test_cancel_writes_audit_event(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    body = _checkout(api_client, "audit-checkout-0003")
    assert api_client.post(f"/api/store/orders/{body['order_no']}/cancel").status_code == 200

    events = _events(db_session, shop_data["merchant"].id)
    assert [event.event_type for event in events] == ["order_created", "order_cancelled"]
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_idempotent_replay_does_not_duplicate_audit(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    body = _checkout(api_client, "audit-checkout-0004")
    second = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "audit-checkout-0004"},
    )
    assert second.status_code == 201
    assert second.json()["order_no"] == body["order_no"]
    api_client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")
    api_client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")

    events = _events(db_session, shop_data["merchant"].id)
    assert [event.event_type for event in events] == ["order_created", "payment_paid"]
