from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, ReturnRequest
from app.settings import settings

ADDRESS = {
    "recipient_name": "质检照片顾客",
    "recipient_phone": "13800000061",
    "region": "华东",
    "city": "上海",
    "detail": "质检照片路 61 号",
}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _delivered_order(client: TestClient, key: str) -> str:
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
    admin = client.post(
        f"/api/admin/orders/{body['order_no']}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-QC-{key}"},
    )
    assert admin.status_code == 200, admin.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        assert (
            client.post(
                f"/api/admin/orders/{body['order_no']}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )
    return body["order_no"]


def _return_id(client: TestClient, order_no: str, approve: bool = True) -> int:
    created = client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "质检照片测试"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    if approve:
        assert (
            client.post(f"/api/admin/returns/{return_id}/approve").status_code
            == 200
        )
    return return_id


def _upload(client: TestClient, return_id: int, name: str = "inspect.png"):
    return client.post(
        f"/api/admin/returns/{return_id}/inspection-attachments",
        files={"file": (name, PNG, "image/png")},
    )


def test_owner_uploads_inspection_photos_and_customer_reads(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    admin_login()
    order_no = _delivered_order(api_client, "qc-photo-1")
    return_id = _return_id(api_client, order_no)

    first = _upload(api_client, return_id, "first.png")
    assert first.status_code == 200, first.text
    assert len(first.json()["inspection_attachment_ids"]) == 1
    attachment_id = first.json()["inspection_attachment_ids"][0]
    second = _upload(api_client, return_id, "second.png")
    assert second.status_code == 200, second.text
    third = _upload(api_client, return_id, "third.png")
    assert third.status_code == 200, third.text
    first_id = first.json()["inspection_attachment_ids"][0]
    second_id = second.json()["inspection_attachment_ids"][1]
    assert third.json()["inspection_attachment_ids"] == [
        first_id,
        second_id,
        third.json()["inspection_attachment_ids"][2],
    ]
    overflow = _upload(api_client, return_id, "fourth.png")
    assert overflow.status_code == 422
    assert "最多 3 张" in overflow.json()["detail"]
    invalid = api_client.post(
        f"/api/admin/returns/{return_id}/inspection-attachments",
        files={"file": ("bad.txt", b"not an image", "text/plain")},
    )
    assert invalid.status_code == 422

    customer_image = api_client.get(f"/api/store/attachments/{attachment_id}")
    assert customer_image.status_code == 200
    assert customer_image.content == PNG
    admin_image = api_client.get(f"/api/admin/attachments/{attachment_id}")
    assert admin_image.status_code == 200

    customer_view = api_client.get("/api/store/returns").json()[0]
    assert customer_view["inspection_attachment_ids"] == (
        third.json()["inspection_attachment_ids"]
    )

    db_session.expire_all()
    request = db_session.get(ReturnRequest, return_id)
    assert request is not None
    assert len(request.inspection_attachment_ids or []) == 3
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_inspection_attachment_added")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["return_id"] == return_id
    assert audit.payload_json["attachment_count"] == 3

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert _upload(api_client, return_id).status_code == 403


def test_inspection_photos_require_valid_return_status(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    login()
    admin_login()
    order_no = _delivered_order(api_client, "qc-photo-2")
    requested = _return_id(api_client, order_no, approve=False)
    blocked = _upload(api_client, requested)
    assert blocked.status_code == 409
    missing = _upload(api_client, 999999)
    assert missing.status_code == 404
