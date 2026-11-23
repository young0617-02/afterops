from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Inventory, InventoryMovement


def _upload(client: TestClient, content: str):
    return client.post(
        "/api/admin/inventory/import",
        files={
            "file": (
                "inventory.csv",
                content.encode("utf-8-sig"),
                "text/csv",
            )
        },
    )


def test_inventory_import_updates_rows_and_records_movements(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    content = (
        "sku,warehouse_id,warehouse_name,qty_available\n"
        "SKU-EAR-001,华东,,7\n"
        "SKU-EAR-001,bulk,备用仓,3\n"
        "SKU-PWR-001,华东,,5\n"
    )
    response = _upload(api_client, content)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated": 3,
        "changed": 3,
        "skus": ["SKU-EAR-001", "SKU-PWR-001"],
    }

    earphone = api_client.get("/api/admin/products/SKU-EAR-001").json()
    inventory = {
        row["warehouse_id"]: row["qty_available"]
        for row in earphone["inventory"]
    }
    assert inventory == {"bulk": 3, "华东": 7, "华南": 5}
    assert earphone["available_qty"] == 15

    bulk = db_session.execute(
        select(Inventory).where(Inventory.warehouse_id == "bulk")
    ).scalar_one()
    assert bulk.warehouse_name == "备用仓"

    movements = db_session.execute(
        select(InventoryMovement)
        .where(InventoryMovement.note == "批量导入库存")
        .order_by(InventoryMovement.id)
    ).scalars().all()
    assert len(movements) == 3
    movement_by_warehouse = {
        movement.warehouse_id: movement
        for movement in movements
        if movement.product_id == shop_data["earphone"].id
    }
    assert movement_by_warehouse["华东"].qty_delta == -3
    assert movement_by_warehouse["华东"].qty_after == 7
    assert movement_by_warehouse["bulk"].qty_after == 3

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "inventory_imported")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["updated"] == 3
    assert audit.payload_json["changed"] == 3


def test_inventory_import_is_all_or_nothing_and_validates(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    before = api_client.get("/api/admin/products/SKU-EAR-001").json()[
        "available_qty"
    ]
    invalid = _upload(
        api_client,
        "sku,warehouse_id,qty_available\n"
        "SKU-EAR-001,华东,99\n"
        "SKU-NOT-FOUND,华东,1\n",
    )
    assert invalid.status_code == 422
    assert "商品不存在" in invalid.json()["detail"]
    after = api_client.get("/api/admin/products/SKU-EAR-001").json()[
        "available_qty"
    ]
    assert after == before

    duplicate = _upload(
        api_client,
        "sku,warehouse_id,qty_available\n"
        "SKU-EAR-001,华东,1\nSKU-EAR-001,华东,2\n",
    )
    assert duplicate.status_code == 422
    assert "库存行重复" in duplicate.json()["detail"]

    negative = _upload(
        api_client,
        "sku,warehouse_id,qty_available\nSKU-EAR-001,华东,-1\n",
    )
    assert negative.status_code == 422

    missing_header = _upload(
        api_client, "sku,warehouse_id\nSKU-EAR-001,华东\n"
    )
    assert missing_header.status_code == 422
    assert "缺少列" in missing_header.json()["detail"]

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = _upload(
        api_client,
        "sku,warehouse_id,qty_available\nSKU-EAR-001,华东,1\n",
    )
    assert forbidden.status_code == 403