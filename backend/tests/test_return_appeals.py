from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import (
    ActionProposal,
    AuditEvent,
    Notification,
    ReturnDeductionAppeal,
    ReturnRequest,
)

ADDRESS = {
    "recipient_name": "申诉测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "申诉测试路 1 号",
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


def _received_return(
    client: TestClient,
    key: str,
    *,
    deduction_cents: int = 500,
) -> tuple[str, int]:
    order_no = _paid_order(client, key)
    _delivery(client, order_no, f"SF-APPEAL-{key.upper()}")
    created = client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "质检扣减申诉测试",
            "resolution": "refund",
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    received = client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "deduction_cents": deduction_cents,
            "inspection_note": "外包装破损",
        },
    )
    assert received.status_code == 200, received.text
    return order_no, return_id


def _upload_attachment(client: TestClient, name: str) -> int:
    response = client.post(
        "/api/store/attachments",
        files={
            "file": (
                name,
                bytes.fromhex("89504e470d0a1a0a") + name.encode(),
                "image/png",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_return_deduction_appeal_accept_creates_refund_proposal(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    order_no, return_id = _received_return(
        api_client, "appeal-accept-0001"
    )
    assert order_no.startswith("ORD-")
    attachment_id = _upload_attachment(api_client, "appeal-proof.png")
    created = api_client.post(
        f"/api/store/returns/{return_id}/appeal",
        json={
            "reason": "商品完好，扣减不合理",
            "attachment_ids": [attachment_id],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["appeal"]["status"] == "pending"
    assert body["appeal"]["requested_cents"] == 500
    assert body["appeal"]["attachment_ids"] == [attachment_id]
    assert body["appeal_deadline"] is not None
    assert body["can_appeal"] is False

    duplicate = api_client.post(
        f"/api/store/returns/{return_id}/appeal",
        json={"reason": "再次申诉"},
    )
    assert duplicate.status_code == 409
    assert "已提交过" in duplicate.json()["detail"]

    filtered = api_client.get(
        "/api/admin/returns", params={"status": "appeal"}
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["counts"]["appeals"] == 1
    assert [item["id"] for item in filtered.json()["items"]] == [return_id]

    accepted = api_client.post(
        f"/api/admin/returns/{return_id}/appeal/accept",
        json={"approved_cents": 300, "note": "复核后部分支持"},
    )
    assert accepted.status_code == 200, accepted.text
    appeal = accepted.json()["appeal"]
    assert appeal["status"] == "accepted"
    assert appeal["approved_cents"] == 300
    assert appeal["resolution_note"] == "复核后部分支持"

    db_session.expire_all()
    proposal = db_session.execute(
        select(ActionProposal).where(ActionProposal.created_by == "staff")
    ).scalar_one()
    assert proposal.payload_json["amount_cents"] == 300
    assert proposal.created_by == "staff"
    assert "质检扣减申诉补偿" in proposal.payload_json["reason"]
    stored = db_session.execute(select(ReturnDeductionAppeal)).scalar_one()
    assert stored.status == "accepted"
    assert stored.approved_cents == 300
    event_types = db_session.execute(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type.in_(
                ("return_appeal_created", "return_appeal_accepted")
            )
        )
    ).scalars().all()
    assert set(event_types) == {
        "return_appeal_created",
        "return_appeal_accepted",
    }
    notification = db_session.execute(
        select(Notification)
        .where(
            Notification.type == "return_appeal_accepted",
            Notification.recipient_type == "customer",
        )
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert notification is not None
    assert "追加退款" in notification.body

    again = api_client.post(
        f"/api/admin/returns/{return_id}/appeal/accept",
        json={"approved_cents": 100},
    )
    assert again.status_code == 409
    filtered_after = api_client.get(
        "/api/admin/returns", params={"status": "appeal"}
    )
    assert filtered_after.json()["counts"]["appeals"] == 0
    assert filtered_after.json()["items"] == []


def test_return_deduction_appeal_reject_requires_reason(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, return_id = _received_return(
        api_client, "appeal-reject-0001"
    )
    created = api_client.post(
        f"/api/store/returns/{return_id}/appeal",
        json={"reason": "扣减金额过高"},
    )
    assert created.status_code == 200, created.text

    missing_note = api_client.post(
        f"/api/admin/returns/{return_id}/appeal/reject",
        json={"note": ""},
    )
    assert missing_note.status_code == 422
    rejected = api_client.post(
        f"/api/admin/returns/{return_id}/appeal/reject",
        json={"note": "照片显示包装已拆封，维持扣减"},
    )
    assert rejected.status_code == 200, rejected.text
    appeal = rejected.json()["appeal"]
    assert appeal["status"] == "rejected"
    assert appeal["resolution_note"] == "照片显示包装已拆封，维持扣减"
    assert appeal["approved_cents"] is None

    db_session.expire_all()
    assert db_session.execute(
        select(ActionProposal).where(ActionProposal.created_by == "staff")
    ).scalar_one_or_none() is None
    notification = db_session.execute(
        select(Notification)
        .where(Notification.type == "return_appeal_rejected")
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert notification is not None
    assert "照片显示包装已拆封" in notification.body


def test_customer_withdraws_pending_deduction_appeal(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, return_id = _received_return(
        api_client, "appeal-withdraw-0001"
    )
    created = api_client.post(
        f"/api/store/returns/{return_id}/appeal",
        json={"reason": "先提交申诉"},
    )
    assert created.status_code == 200, created.text

    withdrawn = api_client.post(
        f"/api/store/returns/{return_id}/appeal/withdraw"
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["appeal"]["status"] == "withdrawn"
    assert withdrawn.json()["appeal"]["resolved_at"] is not None

    blocked = api_client.post(
        f"/api/admin/returns/{return_id}/appeal/accept",
        json={"approved_cents": 100},
    )
    assert blocked.status_code == 409
    db_session.expire_all()
    stored = db_session.execute(select(ReturnDeductionAppeal)).scalar_one()
    assert stored.status == "withdrawn"


def test_deduction_appeal_validations_and_ownership(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "appeal-validate-0001")
    admin_login()
    _delivery(api_client, order_no, "SF-APPEAL-VALIDATE")
    created = api_client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "待审核退货"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]

    pending = api_client.post(
        f"/api/store/returns/{return_id}/appeal",
        json={"reason": "尚未收货"},
    )
    assert pending.status_code == 409
    assert "已收货" in pending.json()["detail"]

    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    no_deduction = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"inspection_note": "外观完好"},
    )
    assert no_deduction.status_code == 200, no_deduction.text
    no_deduction_appeal = api_client.post(
        f"/api/store/returns/{return_id}/appeal",
        json={"reason": "无关申诉"},
    )
    assert no_deduction_appeal.status_code == 409
    assert "没有质检扣减" in no_deduction_appeal.json()["detail"]

    _other_order, other_return_id = _received_return(
        api_client, "appeal-validate-0002"
    )
    appeal = api_client.post(
        f"/api/store/returns/{other_return_id}/appeal",
        json={"reason": "金额不合理"},
    )
    assert appeal.status_code == 200, appeal.text

    excessive = api_client.post(
        f"/api/admin/returns/{other_return_id}/appeal/accept",
        json={"approved_cents": 501},
    )
    assert excessive.status_code == 422
    assert "核准金额" in excessive.json()["detail"]

    db_session.expire_all()
    request = db_session.execute(
        select(ReturnRequest).where(ReturnRequest.id == other_return_id)
    ).scalar_one()
    request.received_at = datetime.now(UTC) - timedelta(days=8)
    db_session.commit()
    expired = api_client.post(
        f"/api/store/returns/{other_return_id}/appeal",
        json={"reason": "超期申诉"},
    )
    assert expired.status_code == 409
    assert "申诉期限" in expired.json()["detail"]

    login("13800000002")
    denied = api_client.post(
        f"/api/store/returns/{other_return_id}/appeal/withdraw"
    )
    assert denied.status_code == 404

    db_session.expire_all()
    assert db_session.execute(
        select(func.count(ReturnDeductionAppeal.id))
    ).scalar_one() == 1
