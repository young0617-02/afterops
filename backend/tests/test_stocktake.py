from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, InventoryMovement


def _create_product(client: TestClient) -> None:
    response = client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-STOCK-001",
            "title": "盘点商品",
            "price_cents": 1000,
            "initial_qty": 10,
        },
    )
    assert response.status_code == 200, response.text


def test_stocktake_adjusts_inventory_and_audits(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    _create_product(api_client)
    response = api_client.post(
        "/api/admin/products/SKU-STOCK-001/inventory/default/stocktake",
        json={"counted_qty": 7, "note": "月度盘点"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "sku": "SKU-STOCK-001",
        "warehouse_id": "default",
        "before_qty": 10,
        "after_qty": 7,
        "delta": -3,
    }
    movement = db_session.execute(
        select(InventoryMovement)
        .where(InventoryMovement.reason == "stocktake")
        .order_by(InventoryMovement.id.desc())
    ).scalars().first()
    assert movement is not None
    assert movement.qty_delta == -3
    assert movement.qty_after == 7
    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "inventory_stocktake")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["delta"] == -3
    assert audit.payload_json["note"] == "月度盘点"

    same = api_client.post(
        "/api/admin/products/SKU-STOCK-001/inventory/default/stocktake",
        json={"counted_qty": 7},
    )
    assert same.status_code == 200, same.text
    assert same.json()["delta"] == 0
    assert (
        db_session.execute(
            select(InventoryMovement).where(
                InventoryMovement.reason == "stocktake"
            )
        ).scalars().all()
        == [movement]
    )

    reconciled = api_client.get(
        "/api/admin/inventory-reconciliation"
    ).json()
    assert reconciled["discrepancies"] == []


def test_stocktake_permissions(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    _create_product(api_client)
    negative = api_client.post(
        "/api/admin/products/SKU-STOCK-001/inventory/default/stocktake",
        json={"counted_qty": -1},
    )
    assert negative.status_code == 422

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = api_client.post(
        "/api/admin/products/SKU-STOCK-001/inventory/default/stocktake",
        json={"counted_qty": 5},
    )
    assert forbidden.status_code == 403
