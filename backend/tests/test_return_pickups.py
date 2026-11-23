from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Notification, ReturnPickup

ADDRESS = {
    "recipient_name": "取件预约顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "取件预约路 1 号",
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


def _approved_return(
    client: TestClient, key: str, *, tracking_no: str | None = None
) -> tuple[str, int]:
    order_no = _paid_order(client, key)
    _delivery(client, order_no, tracking_no or f"SF-PICKUP-{key.upper()}")
    created = client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "上门取件测试"},
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    return order_no, return_id


def _schedule(
    client: TestClient, return_id: int, days: int = 1
) -> dict:
    scheduled = datetime.now(UTC).date() + timedelta(days=days)
    response = client.post(
        f"/api/store/returns/{return_id}/pickup",
        json={
            "scheduled_date": scheduled.isoformat(),
            "time_window": "09:00-12:00",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_pickup_schedule_and_pickup_creates_return_tracking(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    order_no, return_id = _approved_return(api_client, "pickup-flow-0001")
    before = api_client.get("/api/store/returns").json()
    row = next(item for item in before if item["id"] == return_id)
    assert row["pickup"] is None
    assert row["can_schedule_pickup"] is True

    body = _schedule(api_client, return_id)
    pickup = body["pickup"]
    assert pickup["status"] == "scheduled"
    assert pickup["pickup_no"].startswith("PU-")
    assert pickup["scheduled_date"] is not None
    assert pickup["time_window"] == "09:00-12:00"
    assert pickup["address_recipient"] == ADDRESS["recipient_name"]
    assert pickup["address_detail"] == ADDRESS["detail"]
    assert pickup["courier_name"] == "顺丰速运"
    assert body["can_schedule_pickup"] is False
    assert body["tracking_no"] == ""

    duplicate = api_client.post(
        f"/api/store/returns/{return_id}/pickup",
        json={
            "scheduled_date": (datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
            "time_window": "13:00-18:00",
        },
    )
    assert duplicate.status_code == 409
    assert "已有" in duplicate.json()["detail"]

    filtered = api_client.get(
        "/api/admin/returns", params={"status": "pickup"}
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["counts"]["pickups"] == 1
    assert [item["id"] for item in filtered.json()["items"]] == [return_id]

    received_early = api_client.post(
        f"/api/admin/returns/{return_id}/receive"
    )
    assert received_early.status_code == 409
    assert "上门取件预约" in received_early.json()["detail"]

    picked = api_client.post(
        f"/api/admin/returns/{return_id}/pickup/picked-up"
    )
    assert picked.status_code == 200, picked.text
    picked_body = picked.json()
    assert picked_body["pickup"]["status"] == "picked_up"
    assert picked_body["pickup"]["picked_up_at"] is not None
    assert picked_body["status"] == "in_transit"
    assert picked_body["shipment_status"] == "picked_up"
    assert picked_body["carrier"] == "顺丰速运"
    assert picked_body["tracking_no"] == pickup["pickup_no"]
    assert picked_body["tracking_events"][-1]["status"] == "picked_up"
    assert pickup["pickup_no"] in picked_body["tracking_events"][-1]["description"]

    filter_after = api_client.get(
        "/api/admin/returns", params={"status": "pickup"}
    )
    assert filter_after.json()["counts"]["pickups"] == 0
    assert filter_after.json()["items"] == []

    advanced = api_client.post(
        f"/api/admin/returns/{return_id}/tracking/advance",
        json={"status": "in_transit"},
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["shipment_status"] == "in_transit"

    db_session.expire_all()
    stored = db_session.execute(select(ReturnPickup)).scalar_one()
    assert stored.status == "picked_up"
    notice = db_session.execute(
        select(Notification)
        .where(Notification.type == "return_pickup_picked_up")
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert notice is not None
    assert pickup["pickup_no"] in notice.body
    assert any(
        item["order_no"] == order_no
        for item in api_client.get("/api/store/returns").json()
    )


def test_pickup_cancel_allows_reschedule_and_self_ship(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    _order_no, return_id = _approved_return(api_client, "pickup-cancel-0001")
    first = _schedule(api_client, return_id)
    cancelled = api_client.post(
        f"/api/store/returns/{return_id}/pickup/cancel",
        json={"reason": "当天不在家"},
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["pickup"]["status"] == "cancelled"
    assert body["pickup"]["cancel_reason"] == "当天不在家"
    assert body["pickup"]["cancelled_at"] is not None
    assert body["can_schedule_pickup"] is True

    second = _schedule(api_client, return_id, days=2)
    assert second["pickup"]["status"] == "scheduled"
    assert second["pickup"]["pickup_no"] != first["pickup"]["pickup_no"]

    blocked = api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-PICKUP-SELF-0001"},
    )
    assert blocked.status_code == 409
    assert "请先取消" in blocked.json()["detail"]

    cancelled_again = api_client.post(
        f"/api/store/returns/{return_id}/pickup/cancel"
    )
    assert cancelled_again.status_code == 200, cancelled_again.text
    tracked = api_client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": "SF-PICKUP-SELF-0001"},
    )
    assert tracked.status_code == 200, tracked.text
    assert tracked.json()["shipment_status"] == "registered"


def test_pickup_validations(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    _order_no, return_id = _approved_return(
        api_client, "pickup-validate-0001"
    )
    today = datetime.now(UTC).date()

    past = api_client.post(
        f"/api/store/returns/{return_id}/pickup",
        json={
            "scheduled_date": (today - timedelta(days=1)).isoformat(),
            "time_window": "09:00-12:00",
        },
    )
    assert past.status_code == 422
    assert "不能早于今天" in past.json()["detail"]

    far = api_client.post(
        f"/api/store/returns/{return_id}/pickup",
        json={
            "scheduled_date": (today + timedelta(days=8)).isoformat(),
            "time_window": "09:00-12:00",
        },
    )
    assert far.status_code == 422
    assert "最多可预约" in far.json()["detail"]

    bad_window = api_client.post(
        f"/api/store/returns/{return_id}/pickup",
        json={
            "scheduled_date": (today + timedelta(days=1)).isoformat(),
            "time_window": "20:00-22:00",
        },
    )
    assert bad_window.status_code == 422

    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-PICKUP-PRE-0001"},
        ).status_code
        == 200
    )
    after_tracking = api_client.post(
        f"/api/store/returns/{return_id}/pickup",
        json={
            "scheduled_date": (today + timedelta(days=1)).isoformat(),
            "time_window": "13:00-18:00",
        },
    )
    assert after_tracking.status_code == 409
    assert "已登记回寄" in after_tracking.json()["detail"]


def test_admin_cancels_pickup_notifies_customer(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _order_no, return_id = _approved_return(api_client, "pickup-admin-cancel")
    _schedule(api_client, return_id)

    missing = api_client.post(
        f"/api/admin/returns/{return_id}/pickup/cancel",
        json={"note": ""},
    )
    assert missing.status_code == 422
    cancelled = api_client.post(
        f"/api/admin/returns/{return_id}/pickup/cancel",
        json={"note": "该地址今日无法安排取件"},
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["pickup"]["status"] == "cancelled"
    assert body["pickup"]["cancel_reason"] == "该地址今日无法安排取件"
    assert body["status"] == "approved"
    assert body["can_schedule_pickup"] is True

    db_session.expire_all()
    notice = db_session.execute(
        select(Notification)
        .where(Notification.type == "return_pickup_cancelled")
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert notice is not None
    assert "该地址今日无法安排取件" in notice.body
