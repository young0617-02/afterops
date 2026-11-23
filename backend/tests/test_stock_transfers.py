from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Inventory, InventoryMovement


def _inventory(db_session, product_id: int, warehouse_id: str) -> Inventory:
    db_session.expire_all()
    return db_session.execute(
        select(Inventory).where(
            Inventory.product_id == product_id,
            Inventory.warehouse_id == warehouse_id,
        )
    ).scalar_one()


def _create(
    client: TestClient,
    *,
    sku: str = "SKU-EAR-001",
    source: str = "华东",
    target: str = "华南",
    qty: int = 3,
    note: str = "补货",
):
    return client.post(
        "/api/admin/stock-transfers",
        json={
            "sku": sku,
            "from_warehouse_id": source,
            "to_warehouse_id": target,
            "qty": qty,
            "note": note,
        },
    )


def test_stock_transfer_lifecycle_moves_stock_on_receive(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    product_id = shop_data["earphone"].id
    admin_login()
    assert _inventory(db_session, product_id, "华东").qty_available == 10

    created = _create(api_client)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "in_transit"
    assert body["transfer_no"].startswith("TRF-")
    assert body["qty"] == 3
    assert body["note"] == "补货"
    transfer_id = body["id"]

    east = _inventory(db_session, product_id, "华东")
    south = _inventory(db_session, product_id, "华南")
    assert east.qty_available == 7
    assert south.qty_available == 5

    out_movement = db_session.execute(
        select(InventoryMovement)
        .where(
            InventoryMovement.product_id == product_id,
            InventoryMovement.reason == "transfer_out",
        )
        .order_by(InventoryMovement.id.desc())
    ).scalars().first()
    assert out_movement is not None
    assert out_movement.qty_delta == -3
    assert body["transfer_no"] in out_movement.note

    listed = api_client.get(
        "/api/admin/stock-transfers", params={"status": "in_transit"}
    )
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [transfer_id]

    received = api_client.post(
        f"/api/admin/stock-transfers/{transfer_id}/receive"
    )
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "received"
    assert received.json()["received_at"] is not None
    assert _inventory(db_session, product_id, "华东").qty_available == 7
    assert _inventory(db_session, product_id, "华南").qty_available == 8

    duplicate = api_client.post(
        f"/api/admin/stock-transfers/{transfer_id}/receive"
    )
    assert duplicate.status_code == 409

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "stock_transfer_received")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["transfer_no"] == body["transfer_no"]
    assert audit.payload_json["qty"] == 3


def test_cancel_transfer_restores_source_and_validation(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    product_id = shop_data["earphone"].id
    admin_login()

    same = _create(api_client, source="华东", target="华东")
    assert same.status_code == 422
    over = _create(api_client, qty=99)
    assert over.status_code == 409
    assert "库存不足" in over.json()["detail"]
    missing = _create(api_client, sku="NO-SUCH-SKU")
    assert missing.status_code == 404
    missing_source = _create(api_client, source="不存在仓")
    assert missing_source.status_code == 404

    created = _create(api_client, qty=2, note="活动备货")
    assert created.status_code == 200, created.text
    transfer_id = created.json()["id"]
    assert _inventory(db_session, product_id, "华东").qty_available == 8
    assert _inventory(db_session, product_id, "华南").qty_available == 5

    cancelled = api_client.post(
        f"/api/admin/stock-transfers/{transfer_id}/cancel",
        json={"reason": "活动取消"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_reason"] == "活动取消"
    assert _inventory(db_session, product_id, "华东").qty_available == 10
    assert _inventory(db_session, product_id, "华南").qty_available == 5

    movement = db_session.execute(
        select(InventoryMovement)
        .where(
            InventoryMovement.product_id == product_id,
            InventoryMovement.reason == "transfer_cancel_in",
        )
        .order_by(InventoryMovement.id.desc())
    ).scalars().first()
    assert movement is not None
    assert movement.qty_delta == 2

    duplicate = api_client.post(
        f"/api/admin/stock-transfers/{transfer_id}/cancel",
        json={"reason": "重复取消"},
    )
    assert duplicate.status_code == 409

    agent_client = api_client
    agent_client.post("/api/admin/session", json={"email": "agent@test.local", "password": "agent123456"})
    forbidden = _create(agent_client)
    assert forbidden.status_code == 403