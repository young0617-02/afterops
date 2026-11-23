from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.models import (
    Notification,
    Refund,
    RepairRequest,
    ReturnDeductionAppeal,
    ReturnPickup,
)
from app.services.after_sales_reminders import send_after_sales_reminders

ADDRESS = {
    "recipient_name": "售后提醒顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "售后提醒路 1 号",
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
        assert (
            client.post(
                f"/api/admin/orders/{order_no}/shipment/advance",
                json={"status": status},
            ).status_code
            == 200
        )


def _approved_return(
    client: TestClient, order_no: str
) -> tuple[int, int]:
    created = client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "售后提醒测试"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    return_id = body["id"]
    item_id = body["items"][0]["id"]
    assert (
        client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    return return_id, item_id


def test_after_sales_reminders_trigger_once(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    now = datetime.now(UTC)

    repair_order = _paid_order(api_client, "reminder-repair-0001")
    _delivery(api_client, repair_order, "SF-REM-REPAIR")
    repair = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": repair_order,
            "sku": "SKU-EAR-001",
            "issue": "报价提醒测试",
        },
    )
    assert repair.status_code == 200, repair.text
    repair_id = repair.json()["id"]
    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/approve").status_code
        == 200
    )
    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/receive").status_code
        == 200
    )
    quoted = api_client.post(
        f"/api/admin/repairs/{repair_id}/quote",
        json={"amount_cents": 5000, "note": "更换主板"},
    )
    assert quoted.status_code == 200, quoted.text
    repair_row = db_session.get(RepairRequest, repair_id)
    repair_row.quote_created_at = now - timedelta(hours=25)
    db_session.commit()

    pickup_order = _paid_order(api_client, "reminder-pickup-0001")
    _delivery(api_client, pickup_order, "SF-REM-PICKUP")
    pickup_return_id, _ = _approved_return(api_client, pickup_order)
    pickup = api_client.post(
        f"/api/store/returns/{pickup_return_id}/pickup",
        json={
            "scheduled_date": (now.date() + timedelta(days=1)).isoformat(),
            "time_window": "09:00-12:00",
        },
    )
    assert pickup.status_code == 200, pickup.text
    pickup_row = db_session.execute(
        select(ReturnPickup).where(
            ReturnPickup.return_request_id == pickup_return_id
        )
    ).scalar_one()
    pickup_row.scheduled_date = now.date() - timedelta(days=1)
    db_session.commit()

    appeal_order = _paid_order(api_client, "reminder-appeal-0001")
    _delivery(api_client, appeal_order, "SF-REM-APPEAL")
    appeal_return_id, item_id = _approved_return(api_client, appeal_order)
    received = api_client.post(
        f"/api/admin/returns/{appeal_return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "disposition": "restock",
                    "deduction_cents": 500,
                    "responsibility": "customer",
                }
            ],
            "inspection_note": "缺少配件",
        },
    )
    assert received.status_code == 200, received.text
    proposal_id = received.json()["refund_proposal_id"]
    assert proposal_id
    assert (
        api_client.post(
            f"/api/admin/proposals/{proposal_id}/approve"
        ).status_code
        == 200
    )
    appeal = api_client.post(
        f"/api/store/returns/{appeal_return_id}/appeal",
        json={"reason": "扣减有异议"},
    )
    assert appeal.status_code == 200, appeal.text
    appeal_row = db_session.execute(
        select(ReturnDeductionAppeal).where(
            ReturnDeductionAppeal.return_request_id == appeal_return_id
        )
    ).scalar_one()
    appeal_row.created_at = now - timedelta(hours=25)
    refund_row = db_session.execute(select(Refund)).scalar_one()
    refund_row.executed_at = now - timedelta(hours=25)
    refund_row.settlement_status = "processing"
    db_session.commit()

    stats = send_after_sales_reminders(db_session, now=now)
    assert stats == {
        "pickup_overdue": 1,
        "quote_pending": 1,
        "refund_processing": 1,
        "appeal_pending": 1,
        "supplement_pending": 0,
    }

    db_session.expire_all()
    assert db_session.get(ReturnPickup, pickup_row.id).reminded_at is not None
    assert db_session.get(RepairRequest, repair_id).quote_reminded_at is not None
    assert db_session.get(Refund, refund_row.id).settlement_reminded_at is not None
    assert db_session.get(
        ReturnDeductionAppeal, appeal_row.id
    ).reminded_at is not None

    def count_types(*types: str) -> int:
        return int(
            db_session.execute(
                select(func.count(Notification.id)).where(
                    Notification.type.in_(types)
                )
            ).scalar_one()
        )

    assert count_types("pickup_overdue") == 2
    assert count_types("repair_quote_reminder") == 1
    assert count_types("refund_settlement_overdue") == 2
    assert count_types("return_appeal_overdue") == 2

    again = send_after_sales_reminders(db_session, now=now)
    assert again == {
        "pickup_overdue": 0,
        "quote_pending": 0,
        "refund_processing": 0,
        "appeal_pending": 0,
        "supplement_pending": 0,
    }
    assert count_types(
        "pickup_overdue",
        "repair_quote_reminder",
        "refund_settlement_overdue",
        "return_appeal_overdue",
    ) == 7
