from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    Cart,
    Inventory,
    InventoryReservation,
    Order,
    Payment,
)
from app.main import app
from app.services.orders import expire_order
from app.settings import settings

ADDRESS = {
    "recipient_name": "张三",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "浦东新区测试路 1 号",
}


def _add_to_cart(client: TestClient, qty: int = 2) -> None:
    resp = client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": qty})
    assert resp.status_code == 200, resp.text


def _checkout(client: TestClient, key: str = "checkout-test-0001") -> dict:
    resp = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_checkout_requires_login(api_client: TestClient, shop_data) -> None:
    resp = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "checkout-test-0001"},
    )
    assert resp.status_code == 401


def test_checkout_creates_order_and_reserves_stock(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    earphone_id = shop_data["earphone"].id
    login()
    _add_to_cart(api_client, 2)
    body = _checkout(api_client)

    assert body["amount_cents"] == 179800
    assert body["amount_display"] == "1798.00"
    assert body["status"] == "pending_payment"
    assert body["expires_at"]

    db_session.expire_all()
    order = db_session.execute(
        select(Order).where(Order.order_no == body["order_no"])
    ).scalar_one()
    assert order.status == "pending_payment"
    assert order.payment_status == "unpaid"
    assert order.payable_cents == 179800
    assert order.recipient_name == "张三"

    payment = db_session.execute(
        select(Payment).where(Payment.id == body["payment_id"])
    ).scalar_one()
    assert payment.status == "created"

    reservations = db_session.execute(
        select(InventoryReservation).where(InventoryReservation.order_id == order.id)
    ).scalars().all()
    assert len(reservations) == 1
    assert reservations[0].status == "reserved"
    assert reservations[0].qty == 2

    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    assert sum(row.qty_available for row in inventory) == 13
    assert sum(row.qty_reserved for row in inventory) == 2

    cart = db_session.execute(select(Cart).where(Cart.customer_id == order.customer_id)).scalars().all()
    assert {row.status for row in cart} == {"ordered"}


def test_checkout_is_idempotent(api_client: TestClient, shop_data, login, db_session) -> None:
    earphone_id = shop_data["earphone"].id
    login()
    _add_to_cart(api_client, 2)
    first = _checkout(api_client, "checkout-test-idem")
    second = _checkout(api_client, "checkout-test-idem")
    assert first["order_no"] == second["order_no"]
    assert first["payment_id"] == second["payment_id"]

    db_session.expire_all()
    orders = db_session.execute(
        select(Order).where(Order.order_no == first["order_no"])
    ).scalars().all()
    assert len(orders) == 1
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    assert sum(row.qty_available for row in inventory) == 13


def test_checkout_rejects_empty_cart(api_client: TestClient, shop_data, login) -> None:
    login()
    resp = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "checkout-test-empty"},
    )
    assert resp.status_code == 409


def test_checkout_requires_idempotency_header(api_client: TestClient, shop_data, login) -> None:
    login()
    _add_to_cart(api_client, 1)
    resp = api_client.post("/api/store/checkout", json={"address": ADDRESS})
    assert resp.status_code == 422


def test_checkout_rejects_insufficient_stock(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    earphone_id = shop_data["earphone"].id
    login()
    _add_to_cart(api_client, 15)

    db_session.expire_all()
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    for row in inventory:
        row.qty_available = 0
    db_session.commit()

    resp = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "checkout-test-no-stock"},
    )
    assert resp.status_code == 409
    db_session.expire_all()
    assert db_session.execute(select(Order)).scalars().all() == []
    assert db_session.execute(select(InventoryReservation)).scalars().all() == []


def test_mock_payment_confirm_is_idempotent(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    earphone_id = shop_data["earphone"].id
    login()
    _add_to_cart(api_client, 2)
    body = _checkout(api_client, "checkout-test-pay")

    first = api_client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")
    assert first.status_code == 200
    assert first.json()["status"] == "paid"

    db_session.expire_all()
    payment = db_session.execute(
        select(Payment).where(Payment.id == body["payment_id"])
    ).scalar_one()
    order = db_session.execute(
        select(Order).where(Order.order_no == body["order_no"])
    ).scalar_one()
    assert payment.status == "paid"
    assert order.status == "paid"
    assert order.payment_status == "paid"

    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    assert sum(row.qty_available for row in inventory) == 13
    assert sum(row.qty_reserved for row in inventory) == 0

    reservations = db_session.execute(
        select(InventoryReservation).where(InventoryReservation.order_id == order.id)
    ).scalars().all()
    assert [row.status for row in reservations] == ["committed"]

    second = api_client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")
    assert second.status_code == 200
    db_session.expire_all()
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    assert sum(row.qty_reserved for row in inventory) == 0


def test_mock_webhook_signature(api_client: TestClient, shop_data, login, db_session) -> None:
    login()
    _add_to_cart(api_client, 1)
    body = _checkout(api_client, "checkout-test-webhook")
    event_id = "evt-checkout-test-1"
    signature = hmac.new(
        settings.mock_psp_secret.encode(),
        f"{event_id}:{body['payment_id']}:paid".encode(),
        hashlib.sha256,
    ).hexdigest()

    resp = api_client.post(
        "/api/webhooks/psp/mock",
        json={"event_id": event_id, "payment_id": body["payment_id"], "status": "paid"},
        headers={"X-Mock-Signature": signature},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"

    db_session.expire_all()
    payment = db_session.execute(
        select(Payment).where(Payment.id == body["payment_id"])
    ).scalar_one()
    assert payment.status == "paid"

    bad = api_client.post(
        "/api/webhooks/psp/mock",
        json={"event_id": "evt-bad", "payment_id": body["payment_id"], "status": "paid"},
        headers={"X-Mock-Signature": "bad-signature"},
    )
    assert bad.status_code == 401


def test_order_ownership_and_detail(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    _add_to_cart(api_client, 2)
    body = _checkout(api_client, "checkout-test-owner")

    listed = api_client.get("/api/store/orders")
    assert listed.status_code == 200
    assert [row["order_no"] for row in listed.json()] == [body["order_no"]]

    detail = api_client.get(f"/api/store/orders/{body['order_no']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["address"]["city"] == "上海"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["sku"] == "SKU-EAR-001"
    assert payload["payment"]["status"] == "created"

    with TestClient(app) as other:
        otp_login(other, "13900000002")
        assert other.get(f"/api/store/orders/{body['order_no']}").status_code == 404
        assert other.get("/api/store/orders").json() == []


def test_payment_ownership(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    _add_to_cart(api_client, 1)
    body = _checkout(api_client, "checkout-test-pay-owner")

    with TestClient(app) as other:
        otp_login(other, "13900000003")
        resp = other.post(f"/api/store/payments/{body['payment_id']}/mock-confirm")
        assert resp.status_code == 404


def test_cancel_unpaid_order_releases_stock(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    earphone_id = shop_data["earphone"].id
    login()
    _add_to_cart(api_client, 2)
    body = _checkout(api_client, "checkout-test-cancel")

    resp = api_client.post(f"/api/store/orders/{body['order_no']}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    db_session.expire_all()
    order = db_session.execute(
        select(Order).where(Order.order_no == body["order_no"])
    ).scalar_one()
    payment = db_session.execute(
        select(Payment).where(Payment.id == body["payment_id"])
    ).scalar_one()
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    reservations = db_session.execute(
        select(InventoryReservation).where(InventoryReservation.order_id == order.id)
    ).scalars().all()

    assert order.status == "cancelled"
    assert payment.status == "failed"
    assert sum(row.qty_available for row in inventory) == 15
    assert sum(row.qty_reserved for row in inventory) == 0
    assert [row.status for row in reservations] == ["released"]


def test_cancel_paid_order_rejected(api_client: TestClient, shop_data, login) -> None:
    login()
    _add_to_cart(api_client, 1)
    body = _checkout(api_client, "checkout-test-cancel-paid")
    assert api_client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code == 200

    resp = api_client.post(f"/api/store/orders/{body['order_no']}/cancel")
    assert resp.status_code == 409


def test_cancel_other_customer_order(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    _add_to_cart(api_client, 1)
    body = _checkout(api_client, "checkout-test-cancel-owner")

    with TestClient(app) as other:
        otp_login(other, "13900000004")
        resp = other.post(f"/api/store/orders/{body['order_no']}/cancel")
        assert resp.status_code == 404


def test_expire_order_releases_stock(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    earphone_id = shop_data["earphone"].id
    login()
    _add_to_cart(api_client, 2)
    body = _checkout(api_client, "checkout-test-expire")

    db_session.expire_all()
    order = db_session.execute(
        select(Order).where(Order.order_no == body["order_no"])
    ).scalar_one()
    reservations = db_session.execute(
        select(InventoryReservation).where(InventoryReservation.order_id == order.id)
    ).scalars().all()
    for row in reservations:
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    assert expire_order(db_session, order.id) is True
    db_session.expire_all()
    order = db_session.execute(
        select(Order).where(Order.order_no == body["order_no"])
    ).scalar_one()
    inventory = db_session.execute(
        select(Inventory).where(Inventory.product_id == earphone_id)
    ).scalars().all()
    reservations = db_session.execute(
        select(InventoryReservation).where(InventoryReservation.order_id == order.id)
    ).scalars().all()

    assert order.status == "cancelled"
    assert sum(row.qty_available for row in inventory) == 15
    assert sum(row.qty_reserved for row in inventory) == 0
    assert [row.status for row in reservations] == ["expired"]

