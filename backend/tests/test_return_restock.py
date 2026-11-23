from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Inventory

ADDRESS = {
    "recipient_name": "回补测试顾客",
    "recipient_phone": "13800000003",
    "region": "华东",
    "city": "上海",
    "detail": "回补测试路 3 号",
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
        json={"order_no": order_no, "reason": "回补测试", "resolution": resolution},
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


def _stock(db_session, product_id: int, warehouse_id: str) -> int:
    db_session.expire_all()
    row = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == product_id,
            Inventory.warehouse_id == warehouse_id,
        )
    ).scalar_one()
    return row.qty_available


def test_refund_receipt_restocks_original_warehouse(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    product_id = shop_data["earphone"].id
    assert _stock(db_session, product_id, "华东") == 10

    order_no = _paid_order(api_client, "restock-refund")
    assert _stock(db_session, product_id, "华东") == 9
    _delivery(api_client, order_no, "SF-RESTOCK-001")
    return_id = _approved_return(api_client, order_no, "SF-RETURN-001")

    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "received"
    assert received.json()["restocked_items"] == [
        {"sku": "SKU-EAR-001", "warehouse_id": "华东", "qty": 1}
    ]
    assert _stock(db_session, product_id, "华东") == 10
    assert _stock(db_session, product_id, "华南") == 5

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["restock"] is True
    assert audit.payload_json["restocked_items"] == [
        {"sku": "SKU-EAR-001", "warehouse_id": "华东", "qty": 1}
    ]


def test_receipt_without_restock_keeps_inventory(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    product_id = shop_data["earphone"].id
    order_no = _paid_order(api_client, "restock-skip")
    _delivery(api_client, order_no, "SF-RESTOCK-002")
    return_id = _approved_return(api_client, order_no, "SF-RETURN-002")

    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={"restock": False},
    )
    assert received.status_code == 200, received.text
    assert received.json()["restocked_items"] == []
    assert _stock(db_session, product_id, "华东") == 9

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "return_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["restock"] is False
    assert audit.payload_json["restocked_items"] == []


def test_exchange_receipt_restocks_then_allocates_replacement(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    product_id = shop_data["earphone"].id
    order_no = _paid_order(api_client, "restock-exchange")
    _delivery(api_client, order_no, "SF-RESTOCK-003")
    return_id = _approved_return(
        api_client, order_no, "SF-RETURN-003", resolution="exchange"
    )

    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    assert received.json()["replacement_order_no"]
    assert received.json()["restocked_items"] == [
        {"sku": "SKU-EAR-001", "warehouse_id": "华东", "qty": 1}
    ]
    # 原商品回补 1 件，同款换货订单再占用 1 件，净库存保持购买后的数量。
    assert _stock(db_session, product_id, "华东") == 9
