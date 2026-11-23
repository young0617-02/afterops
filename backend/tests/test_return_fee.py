from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    ActionProposal,
    AuditEvent,
    Inventory,
    Product,
    ReturnRequest,
)

ADDRESS = {
    "recipient_name": "运费测试顾客",
    "recipient_phone": "13800000005",
    "region": "华东",
    "city": "上海",
    "detail": "运费测试路 5 号",
}


def _set_policy(client: TestClient, fee_cents: int) -> None:
    response = client.patch(
        "/api/admin/return-policy",
        json={
            "window_days": 7,
            "return_shipping_fee_cents": fee_cents,
            "allow_exchange": True,
            "final_sale_categories": [],
        },
    )
    assert response.status_code == 200, response.text


def _enable_charger(db_session, quantity: int = 5) -> None:
    charger = db_session.execute(
        select(Product).where(Product.sku == "SKU-PWR-001")
    ).scalar_one()
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == charger.id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    inventory.qty_available = quantity
    db_session.commit()


def _paid_order(
    client: TestClient, key: str, *, charger: bool = False, qty: int = 1
) -> str:
    sku = "SKU-PWR-001" if charger else "SKU-EAR-001"
    client.post("/api/store/cart/items", json={"sku": sku, "qty": qty})
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


def _return_and_receive(
    client: TestClient,
    order_no: str,
    tracking_no: str,
    items: list[dict] | None = None,
) -> dict:
    payload = {"order_no": order_no, "reason": "运费测试"}
    if items is not None:
        payload["items"] = items
    created = client.post("/api/store/returns", json=payload)
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert client.post(f"/api/admin/returns/{return_id}/approve").status_code == 200
    assert (
        client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": tracking_no},
        ).status_code
        == 200
    )
    received = client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    return received.json()


def test_full_return_deducts_shipping_fee(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _set_policy(api_client, 1200)
    order_no = _paid_order(api_client, "fee-full")
    _delivery(api_client, order_no, "SF-FEE-001")

    received = _return_and_receive(api_client, order_no, "SF-FEE-RETURN-1")
    assert received["refund_amount_cents"] == 88700
    assert received["refund_proposal_id"] is not None
    proposal = db_session.get(ActionProposal, received["refund_proposal_id"])
    assert proposal is not None
    assert proposal.payload_json["amount_cents"] == 88700

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["return_shipping_fee_cents"] == 1200
    assert audit.payload_json["refund_amount_cents"] == 88700
    assert audit.payload_json["refund_scope"] == "full"


def test_partial_return_deducts_shipping_fee(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _set_policy(api_client, 1200)
    order_no = _paid_order(api_client, "fee-partial", qty=2)
    _delivery(api_client, order_no, "SF-FEE-002")

    received = _return_and_receive(
        api_client,
        order_no,
        "SF-FEE-RETURN-2",
        items=[{"sku": "SKU-EAR-001", "qty": 1}],
    )
    assert received["refund_amount_cents"] == 88700
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["refund_scope"] == "partial"
    assert audit.payload_json["refund_amount_cents"] == 88700


def test_fee_above_refund_creates_no_proposal(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    _enable_charger(db_session, 5)
    _set_policy(api_client, 20000)
    order_no = _paid_order(api_client, "fee-over", charger=True)
    _delivery(api_client, order_no, "SF-FEE-003")

    received = _return_and_receive(api_client, order_no, "SF-FEE-RETURN-3")
    assert received["status"] == "received"
    assert received["refund_proposal_id"] is None
    assert received["refund_amount_cents"] == 0
    assert db_session.execute(select(ActionProposal)).scalars().all() == []
    return_row = db_session.execute(select(ReturnRequest)).scalar_one()
    assert return_row.refund_amount_cents == 0

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["refund_proposal_id"] is None
    assert audit.payload_json["refund_amount_cents"] == 0
    assert audit.payload_json["return_shipping_fee_cents"] == 20000


def test_return_fee_validation(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    too_high = api_client.patch(
        "/api/admin/return-policy",
        json={
            "window_days": 7,
            "return_shipping_fee_cents": 20001,
            "allow_exchange": True,
            "final_sale_categories": [],
        },
    )
    assert too_high.status_code == 422
    negative = api_client.patch(
        "/api/admin/return-policy",
        json={
            "window_days": 7,
            "return_shipping_fee_cents": -1,
            "allow_exchange": True,
            "final_sale_categories": [],
        },
    )
    assert negative.status_code == 422
