from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, InventoryMovement


def _create_product(client: TestClient) -> None:
    response = client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-XFER-001",
            "title": "调拨商品",
            "price_cents": 1200,
            "initial_qty": 10,
        },
    )
    assert response.status_code == 200, response.text


def _transfer(client: TestClient, quantity: int, **overrides):
    payload = {
        "from_warehouse_id": "default",
        "to_warehouse_id": "east",
        "quantity": quantity,
        "note": "",
    }
    payload.update(overrides)
    return client.post(
        "/api/admin/products/SKU-XFER-001/inventory/transfer",
        json=payload,
    )


def test_transfer_moves_stock_and_records_both_legs(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    _create_product(api_client)
    response = _transfer(api_client, 4, note="华东补货")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "sku": "SKU-XFER-001",
        "from_warehouse_id": "default",
        "to_warehouse_id": "east",
        "quantity": 4,
        "from_before_qty": 10,
        "from_after_qty": 6,
        "to_before_qty": 0,
        "to_after_qty": 4,
    }

    product = api_client.get("/api/admin/products/SKU-XFER-001").json()
    inventory = {
        row["warehouse_id"]: row["qty_available"]
        for row in product["inventory"]
    }
    assert inventory == {"default": 6, "east": 4}
    assert product["available_qty"] == 10

    movements = db_session.execute(
        select(InventoryMovement)
        .where(InventoryMovement.reason.in_(("transfer_out", "transfer_in")))
        .order_by(InventoryMovement.id)
    ).scalars().all()
    assert [
        (row.reason, row.warehouse_id, row.qty_delta, row.qty_after)
        for row in movements
    ] == [
        ("transfer_out", "default", -4, 6),
        ("transfer_in", "east", 4, 4),
    ]
    assert movements[0].note == "华东补货"
    assert movements[1].note == "华东补货"

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "inventory_transferred")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["quantity"] == 4
    assert audit.payload_json["from_after_qty"] == 6
    assert audit.payload_json["to_after_qty"] == 4
    assert audit.payload_json["note"] == "华东补货"

    reconciled = api_client.get("/api/admin/inventory-reconciliation").json()
    assert reconciled["discrepancies"] == []


def test_transfer_rejects_invalid_requests(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    _create_product(api_client)

    zero = _transfer(api_client, 0)
    assert zero.status_code == 422

    same = _transfer(api_client, 1, to_warehouse_id="default")
    assert same.status_code == 422

    missing_source = _transfer(
        api_client, 1, from_warehouse_id="unknown"
    )
    assert missing_source.status_code == 404

    too_much = _transfer(api_client, 11)
    assert too_much.status_code == 409

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = _transfer(api_client, 1)
    assert forbidden.status_code == 403
