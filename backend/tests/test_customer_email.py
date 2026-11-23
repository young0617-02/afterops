from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Customer, Outbox
from app.services.notifications import notify_customer
from app.settings import settings
from app.worker.outbox import process_once


def _checkout(client: TestClient, key: str, email: str | None) -> dict:
    added = client.post(
        "/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1}
    )
    assert added.status_code == 200, added.text
    payload: dict = {
        "address": {
            "recipient_name": "邮件顾客",
            "recipient_phone": "13800000001",
            "region": "华东",
            "city": "上海",
            "detail": "通知路 1 号",
        }
    }
    if email is not None:
        payload["email"] = email
    response = client.post(
        "/api/store/checkout",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_profile_email_update_and_validation(
    api_client: TestClient, shop_data, login
) -> None:
    login()
    updated = api_client.patch(
        "/api/store/me", json={"email": " Buyer@Example.COM "}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["email"] == "buyer@example.com"
    assert api_client.get("/api/store/me").json()["email"] == "buyer@example.com"

    invalid = api_client.patch("/api/store/me", json={"email": "not-an-email"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"

    cleared = api_client.patch("/api/store/me", json={"email": ""})
    assert cleared.status_code == 200
    assert cleared.json()["email"] is None


def test_profile_email_is_unique_per_merchant(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    assert (
        api_client.patch(
            "/api/store/me", json={"email": "shared@example.com"}
        ).status_code
        == 200
    )

    otp_login(api_client, "13800000002")
    duplicate = api_client.patch(
        "/api/store/me", json={"email": "shared@example.com"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"


def test_checkout_stores_customer_email(
    api_client: TestClient, shop_data, login, db_session: Session
) -> None:
    login()
    _checkout(api_client, "customer-email-checkout", "checkout@example.com")
    db_session.expire_all()
    customer = db_session.execute(
        select(Customer).where(Customer.phone == "13800000001")
    ).scalar_one()
    assert customer.email == "checkout@example.com"


def test_customer_notification_enqueues_email(
    api_client: TestClient, shop_data, login, db_session: Session
) -> None:
    login()
    customer = db_session.execute(
        select(Customer).where(Customer.phone == "13800000001")
    ).scalar_one()
    notify_customer(
        db_session,
        customer.merchant_id,
        customer.id,
        notification_type="test_notice",
        title="测试通知",
        body="订单状态已更新",
        link="/after-sales",
    )
    db_session.commit()

    rows = db_session.execute(
        select(Outbox).where(Outbox.event_type == "customer_notification_email")
    ).scalars().all()
    assert rows == []

    api_client.patch("/api/store/me", json={"email": "notice@example.com"})
    db_session.expire_all()
    customer = db_session.get(Customer, customer.id)
    notify_customer(
        db_session,
        customer.merchant_id,
        customer.id,
        notification_type="test_notice",
        title="测试通知",
        body="订单状态已更新",
        link="/after-sales",
    )
    db_session.commit()

    row = db_session.execute(
        select(Outbox).where(Outbox.event_type == "customer_notification_email")
    ).scalar_one()
    payload = row.payload_json
    assert payload["to"] == "notice@example.com"
    assert payload["subject"] == "[AfterOps] 测试通知"
    assert "订单状态已更新" in payload["body"]
    assert f"{settings.admin_base_url}/after-sales" in payload["body"]
    assert "停用邮件通知" in payload["body"]


def test_customer_notification_respects_email_flag(
    api_client: TestClient, shop_data, login, db_session: Session, monkeypatch
) -> None:
    login()
    api_client.patch("/api/store/me", json={"email": "off@example.com"})
    customer = db_session.execute(
        select(Customer).where(Customer.phone == "13800000001")
    ).scalar_one()
    monkeypatch.setattr(settings, "customer_email_notify_enabled", False)
    notify_customer(
        db_session,
        customer.merchant_id,
        customer.id,
        notification_type="test_notice",
        title="不应发信",
        body="",
    )
    db_session.commit()
    assert (
        db_session.execute(
            select(Outbox).where(
                Outbox.event_type == "customer_notification_email"
            )
        ).scalars().all()
        == []
    )


def test_worker_delivers_customer_email(db_session: Session) -> None:
    row = Outbox(
        merchant_id=1,
        event_type="customer_notification_email",
        aggregate_type="notification",
        aggregate_id="customer:1",
        payload_json={
            "to": "deliver@example.com",
            "subject": "[AfterOps] 发货通知",
            "body": "订单已发货",
        },
    )
    db_session.add(row)
    db_session.commit()

    sent: list[dict] = []
    stats = process_once(db_session, sender=lambda payload: sent.append(payload))
    assert stats == {"processed": 1, "failed": 0, "scheduled": 0}
    assert sent[0]["to"] == "deliver@example.com"
    db_session.refresh(row)
    assert row.status == "processed"


def test_email_preferences_filter_notification_categories(
    api_client: TestClient, shop_data, login, db_session: Session
) -> None:
    login()
    updated = api_client.patch(
        "/api/store/me",
        json={
            "email": "prefs@example.com",
            "notify_email_shipment": False,
            "notify_email_return": False,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["notify_email_shipment"] is False
    assert updated.json()["notify_email_after_sales"] is True
    assert updated.json()["notify_email_return"] is False

    db_session.expire_all()
    customer = db_session.execute(
        select(Customer).where(Customer.phone == "13800000001")
    ).scalar_one()
    notify_customer(
        db_session,
        customer.merchant_id,
        customer.id,
        notification_type="shipment_created",
        title="发货",
    )
    notify_customer(
        db_session,
        customer.merchant_id,
        customer.id,
        notification_type="proposal_executed",
        title="退款",
    )
    notify_customer(
        db_session,
        customer.merchant_id,
        customer.id,
        notification_type="return_approved",
        title="退货",
    )
    db_session.commit()

    rows = db_session.execute(
        select(Outbox).where(Outbox.event_type == "customer_notification_email")
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload_json["subject"] == "[AfterOps] 退款"
