from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Inventory, Notification

ADDRESS = {
    "recipient_name": "发票退款顾客",
    "recipient_phone": "13800000014",
    "region": "华东",
    "city": "上海",
    "detail": "发票退款路 14 号",
}


def _paid_order(
    client: TestClient,
    key: str,
    items: tuple[tuple[str, int], ...] = (("SKU-EAR-001", 1),),
) -> dict:
    for sku, qty in items:
        response = client.post(
            "/api/store/cart/items", json={"sku": sku, "qty": qty}
        )
        assert response.status_code == 200, response.text
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
    return body


def _request_invoice(client: TestClient, order_no: str) -> dict:
    response = client.post(
        "/api/store/invoices",
        json={
            "order_no": order_no,
            "invoice_type": "personal",
            "title": "发票退款顾客",
            "email": "invoice@example.com",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stock_charger(db_session, shop_data) -> None:
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == shop_data["charger"].id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    inventory.qty_available = 5
    db_session.commit()


def _customer_invoice(client: TestClient, invoice_id: int) -> dict:
    rows = client.get("/api/store/invoices").json()
    return next(row for row in rows if row["id"] == invoice_id)


def test_issued_invoice_flags_adjustment_after_refund(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order = _paid_order(api_client, "invoice-adjust-0001")
    invoice = _request_invoice(api_client, order["order_no"])
    assert invoice["amount_cents"] == order["amount_cents"]
    assert invoice["net_paid_cents"] == order["amount_cents"]
    assert invoice["adjustment_required"] is False

    admin_login()
    issued = api_client.post(
        f"/api/admin/invoices/{invoice['id']}/issue",
        json={"invoice_no": "INV-ADJUST-0001"},
    )
    assert issued.status_code == 200, issued.text
    created = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "refund",
            "order_no": order["order_no"],
            "amount_cents": 10000,
            "reason": "发票开具后部分退款",
        },
    )
    assert created.status_code == 200, created.text
    approved = api_client.post(
        f"/api/admin/proposals/{created.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    customer = _customer_invoice(api_client, invoice["id"])
    assert customer["status"] == "issued"
    assert customer["amount_cents"] == order["amount_cents"]
    assert customer["net_paid_cents"] == order["amount_cents"] - 10000
    assert customer["adjustment_required"] is True

    admin_rows = api_client.get(
        "/api/admin/invoices", params={"status": "issued"}
    ).json()
    admin_row = next(row for row in admin_rows if row["id"] == invoice["id"])
    assert admin_row["adjustment_required"] is True
    assert admin_row["net_paid_cents"] == order["amount_cents"] - 10000

    needs_adjustment = api_client.get(
        "/api/admin/invoices", params={"status": "needs_adjustment"}
    ).json()
    assert [row["id"] for row in needs_adjustment] == [invoice["id"]]

    db_session.expire_all()
    adjustment_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "invoice_adjustment_required"
        )
    ).scalars().all()
    assert len(adjustment_events) == 1
    assert adjustment_events[0].payload_json["invoice_id"] == invoice["id"]
    notifications = db_session.execute(
        select(Notification).where(
            Notification.type == "invoice_adjustment_required"
        )
    ).scalars().all()
    assert notifications
    assert any("发票金额需调整" == item.title for item in notifications)

    second = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "refund",
            "order_no": order["order_no"],
            "amount_cents": 500,
            "reason": "第二次退款",
        },
    )
    assert second.status_code == 200, second.text
    assert (
        api_client.post(
            f"/api/admin/proposals/{second.json()['proposal_id']}/approve"
        ).status_code
        == 200
    )
    db_session.expire_all()
    assert (
        len(
            db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type
                    == "invoice_adjustment_required"
                )
            ).scalars().all()
        )
        == 1
    )

    voided = api_client.post(
        f"/api/admin/invoices/{invoice['id']}/void",
        json={"reason": "退款后按净支付重开"},
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["adjustment_required"] is False
    assert (
        api_client.get(
            "/api/admin/invoices", params={"status": "needs_adjustment"}
        ).json()
        == []
    )


def test_invoice_after_partial_refund_uses_net_paid(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order = _paid_order(
        api_client,
        "invoice-adjust-0002",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    requested = api_client.post(
        f"/api/store/orders/{order['order_no']}/cancel-request",
        json={"items": [{"sku": "SKU-PWR-001", "qty": 1}]},
    )
    assert requested.status_code == 200, requested.text
    admin_login()
    approved = api_client.post(
        f"/api/admin/proposals/{requested.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    invoice = _request_invoice(api_client, order["order_no"])
    assert invoice["amount_cents"] == order["amount_cents"] - 19900
    assert invoice["net_paid_cents"] == invoice["amount_cents"]
    assert invoice["adjustment_required"] is False


def test_fully_refunded_order_cannot_request_invoice(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order = _paid_order(api_client, "invoice-adjust-0003")
    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款"}
    )
    proposal_id = chat.json()["proposals"][0]["id"]
    admin_login()
    assert (
        api_client.post(
            f"/api/admin/proposals/{proposal_id}/approve"
        ).status_code
        == 200
    )

    rejected = api_client.post(
        "/api/store/invoices",
        json={
            "order_no": order["order_no"],
            "invoice_type": "personal",
            "title": "已退款订单",
        },
    )
    assert rejected.status_code == 409
    assert "已支付" in rejected.json()["detail"]
