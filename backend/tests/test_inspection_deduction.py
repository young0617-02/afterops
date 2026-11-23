from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import (
    ActionProposal,
    AuditEvent,
    Inventory,
    Notification,
    Refund,
)

ADDRESS = {
    "recipient_name": "质检扣减顾客",
    "recipient_phone": "13800000012",
    "region": "华东",
    "city": "上海",
    "detail": "质检扣减路 12 号",
}


def _paid_order(
    client: TestClient,
    key: str,
    items: tuple[tuple[str, int], ...] = (("SKU-EAR-001", 1),),
) -> str:
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
    client: TestClient,
    order_no: str,
    tracking_no: str,
    resolution: str = "refund",
) -> int:
    created = client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "质检扣减测试",
            "resolution": resolution,
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    approved = client.post(f"/api/admin/returns/{return_id}/approve")
    assert approved.status_code == 200, approved.text
    tracking = client.post(
        f"/api/store/returns/{return_id}/tracking",
        json={"carrier": "顺丰速运", "tracking_no": tracking_no},
    )
    assert tracking.status_code == 200, tracking.text
    return return_id


def _customer_return(client: TestClient, return_id: int) -> dict:
    rows = client.get("/api/store/returns").json()
    return next(row for row in rows if row["id"] == return_id)


def test_inspection_deduction_reduces_refund_and_records_reason(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    order_no = _paid_order(api_client, "inspection-deduction-0001")
    _delivery(api_client, order_no, "SF-DEDUCT-0001")
    return_id = _approved_return(api_client, order_no, "SF-DEDUCT-RET-0001")

    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "deduction_cents": 5000,
            "inspection_note": "缺少原装耳塞套，折价 50 元",
        },
    )
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["status"] == "received"
    assert body["refund_amount_cents"] == 84900
    assert body["inspection_deduction_cents"] == 5000
    assert body["inspection_note"] == "缺少原装耳塞套，折价 50 元"
    proposal_id = body["refund_proposal_id"]
    assert proposal_id

    customer = _customer_return(api_client, return_id)
    assert customer["refund_amount_cents"] == 84900
    assert customer["inspection_deduction_cents"] == 5000
    assert customer["inspection_note"] == "缺少原装耳塞套，折价 50 元"

    db_session.expire_all()
    proposal = db_session.get(ActionProposal, proposal_id)
    assert proposal is not None
    assert proposal.payload_json["amount_cents"] == 84900
    assert "缺少原装耳塞套" in proposal.payload_json["reason"]

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["base_refund_cents"] == 89900
    assert audit.payload_json["inspection_deduction_cents"] == 5000
    assert audit.payload_json["refund_amount_cents"] == 84900
    assert audit.payload_json["inspection_note"].startswith("缺少原装耳塞套")

    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text
    db_session.expire_all()
    refund = db_session.execute(
        select(Refund).where(Refund.proposal_id == proposal_id)
    ).scalar_one()
    assert refund.amount_cents == 84900


def test_inspection_deduction_validation_and_zero_refund(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    order_no = _paid_order(api_client, "inspection-deduction-0002")
    _delivery(api_client, order_no, "SF-DEDUCT-0002")
    return_id = _approved_return(api_client, order_no, "SF-DEDUCT-RET-0002")

    over = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"deduction_cents": 90000, "inspection_note": "超额"},
    )
    assert over.status_code == 409
    assert "不能超过可退金额" in over.json()["detail"]

    missing_note = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"deduction_cents": 1000},
    )
    assert missing_note.status_code == 422
    assert "需填写原因" in missing_note.json()["detail"]

    zero = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "deduction_cents": 89900,
            "inspection_note": "商品已损毁，无可退金额",
        },
    )
    assert zero.status_code == 200, zero.text
    body = zero.json()
    assert body["refund_amount_cents"] == 0
    assert body["refund_proposal_id"] is None
    assert body["inspection_deduction_cents"] == 89900

    db_session.expire_all()
    notification = db_session.execute(
        select(Notification)
        .where(Notification.type == "return_received")
        .order_by(Notification.id.desc())
    ).scalars().first()
    assert notification is not None
    assert "质检扣减后无需退款" in notification.title


def test_exchange_return_rejects_inspection_deduction(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    order_no = _paid_order(api_client, "inspection-deduction-0003")
    _delivery(api_client, order_no, "SF-DEDUCT-0003")
    return_id = _approved_return(
        api_client, order_no, "SF-DEDUCT-RET-0003", resolution="exchange"
    )

    rejected = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"deduction_cents": 1000, "inspection_note": "换货不应扣减"},
    )
    assert rejected.status_code == 422
    assert "换货不支持质检扣减" in rejected.json()["detail"]

    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"deduction_cents": 0},
    )
    assert received.status_code == 200, received.text
    assert received.json()["replacement_order_no"]
    assert received.json()["inspection_deduction_cents"] == 0


def _stock_charger(db_session, shop_data) -> None:
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == shop_data["charger"].id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    inventory.qty_available = 5
    db_session.commit()


def test_item_level_deductions_sum_to_refund(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    admin_login()
    order_no = _paid_order(
        api_client,
        "inspection-item-deduction-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    _delivery(api_client, order_no, "SF-ITEM-DED-0001")
    return_id = _approved_return(api_client, order_no, "SF-ITEM-DED-RET-0001")
    rows = api_client.get("/api/store/returns").json()
    request = next(row for row in rows if row["id"] == return_id)
    item_ids = {item["sku"]: item["id"] for item in request["items"]}

    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_ids["SKU-EAR-001"],
                    "disposition": "restock",
                    "deduction_cents": 5000,
                },
                {
                    "item_id": item_ids["SKU-PWR-001"],
                    "disposition": "restock",
                    "deduction_cents": 2000,
                },
            ],
            "inspection_note": "耳机缺配件 50 元，电源划伤 20 元",
        },
    )
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["inspection_deduction_cents"] == 7000
    assert body["refund_amount_cents"] == 102800
    deductions = {item["sku"]: item["inspection_deduction_cents"] for item in body["items"]}
    assert deductions == {"SKU-EAR-001": 5000, "SKU-PWR-001": 2000}

    customer = next(
        row
        for row in api_client.get("/api/store/returns").json()
        if row["id"] == return_id
    )
    assert customer["inspection_deduction_cents"] == 7000
    assert customer["items"][0]["inspection_deduction_cents"] == 5000

    db_session.expire_all()
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["inspection_deduction_cents"] == 7000
    assert len(audit.payload_json["item_deductions"]) == 2


def test_item_level_deduction_validation(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    admin_login()
    order_no = _paid_order(
        api_client,
        "inspection-item-deduction-0002",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    _delivery(api_client, order_no, "SF-ITEM-DED-0002")
    return_id = _approved_return(api_client, order_no, "SF-ITEM-DED-RET-0002")
    rows = api_client.get("/api/store/returns").json()
    request = next(row for row in rows if row["id"] == return_id)
    item_ids = {item["sku"]: item["id"] for item in request["items"]}

    missing_note = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_ids["SKU-EAR-001"],
                    "disposition": "restock",
                    "deduction_cents": 1000,
                }
            ]
        },
    )
    assert missing_note.status_code == 422
    assert "需填写原因" in missing_note.json()["detail"]

    mixed = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_ids["SKU-EAR-001"],
                    "disposition": "restock",
                    "deduction_cents": 1000,
                }
            ],
            "deduction_cents": 1000,
            "inspection_note": "同时整单与逐件",
        },
    )
    assert mixed.status_code == 422
    assert "不能同时" in mixed.json()["detail"]

    over = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_ids["SKU-EAR-001"],
                    "disposition": "restock",
                    "deduction_cents": 120000,
                }
            ],
            "inspection_note": "超额扣减",
        },
    )
    assert over.status_code == 409
    assert "不能超过可退金额" in over.json()["detail"]
