from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent
from app.services.audit_anchors import write_audit_anchors
from app.settings import settings

ADDRESS = {
    "recipient_name": "锚点测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "锚点测试路 1 号",
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


def test_audit_anchor_chain_and_tamper_detection(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "audit_anchor_dir", str(tmp_path))
    monkeypatch.setattr(settings, "audit_anchor_secret", "a" * 40)
    login()
    _paid_order(api_client, "anchor-order-0001")
    assert write_audit_anchors(db_session, cfg=settings) == 1

    admin_login()
    first = api_client.get("/api/admin/audit/anchors")
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["configured"] is True
    assert first_body["count"] == 1
    assert first_body["chain_valid"] is True
    assert first_body["head_covered"] is True
    assert first_body["anchors"][0]["row_match"] is True

    _paid_order(api_client, "anchor-order-0002")
    assert write_audit_anchors(db_session, cfg=settings) == 1
    second = api_client.get("/api/admin/audit/anchors").json()
    assert second["count"] == 2
    assert second["chain_valid"] is True
    assert second["head_covered"] is True
    assert second["anchors"][-1]["prev_valid"] is True

    db_session.expire_all()
    latest = db_session.execute(
        select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
    ).scalar_one()
    latest.row_hash = "tampered"
    db_session.commit()
    tampered = api_client.get("/api/admin/audit/anchors").json()
    assert tampered["chain_valid"] is False
    assert tampered["head_covered"] is False
    assert tampered["anchors"][-1]["row_match"] is False


def test_audit_anchor_signature_tamper_and_unconfigured(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "audit_anchor_dir", str(tmp_path))
    monkeypatch.setattr(settings, "audit_anchor_secret", "b" * 40)
    login()
    _paid_order(api_client, "anchor-order-0003")
    assert write_audit_anchors(db_session, cfg=settings) == 1
    path = next(tmp_path.glob("audit-anchors-*.jsonl"))
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    record["signature"] = "0" * 64
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    admin_login()
    tampered = api_client.get("/api/admin/audit/anchors").json()
    assert tampered["chain_valid"] is False
    assert tampered["anchors"][-1]["signature_valid"] is False

    monkeypatch.setattr(settings, "audit_anchor_secret", "")
    assert write_audit_anchors(db_session, cfg=settings) == 0
    unconfigured = api_client.get("/api/admin/audit/anchors").json()
    assert unconfigured["configured"] is False
