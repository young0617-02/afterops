from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent
from app.services.audit import verify_chain


def _create(client: TestClient, **overrides) -> dict:
    payload = {
        "sku": "SKU-NEW-001",
        "title": "新品氮化镓充电器",
        "category": "充电器",
        "brand": "沃乘",
        "description": "65W 三口氮化镓",
        "specs": "65W / 2C1A",
        "price_cents": 19900,
        "support_7d_no_reason": True,
        "status": "active",
        "initial_qty": 5,
    }
    payload.update(overrides)
    response = client.post("/api/admin/products", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_product_crud_inventory_and_storefront_visibility(
    api_client: TestClient, shop_data, admin_login, db_session
) -> None:
    assert api_client.get("/api/admin/products").status_code == 401
    assert api_client.post("/api/admin/products", json={}).status_code == 401

    admin_login()
    created = _create(api_client, sku="sku-new-001")
    assert created["sku"] == "SKU-NEW-001"
    assert created["available_qty"] == 5
    assert created["reserved_qty"] == 0
    assert created["inventory"] == [
        {
            "warehouse_id": "default",
            "warehouse_name": "默认仓",
            "qty_available": 5,
            "qty_reserved": 0,
        }
    ]
    assert created["price_display"] == "199.00"

    listed = api_client.get("/api/admin/products")
    assert listed.status_code == 200
    assert any(item["sku"] == "SKU-NEW-001" for item in listed.json())
    filtered = api_client.get(
        "/api/admin/products", params={"q": "氮化镓", "status": "active"}
    )
    assert [item["sku"] for item in filtered.json()] == ["SKU-NEW-001"]

    storefront = api_client.get("/api/store/products/SKU-NEW-001")
    assert storefront.status_code == 200, storefront.text
    assert storefront.json()["price_cents"] == 19900
    assert storefront.json()["available_qty"] == 5
    search = api_client.get("/api/store/products", params={"q": "SKU-NEW-001"})
    assert [item["sku"] for item in search.json()] == ["SKU-NEW-001"]

    duplicate = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-NEW-001",
            "title": "重复",
            "price_cents": 100,
        },
    )
    assert duplicate.status_code == 409

    updated = api_client.patch(
        "/api/admin/products/SKU-NEW-001",
        json={"title": "氮化镓充电器 Pro", "price_cents": 21900},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "氮化镓充电器 Pro"
    assert updated.json()["price_cents"] == 21900

    archived = api_client.patch(
        "/api/admin/products/SKU-NEW-001", json={"status": "archived"}
    )
    assert archived.status_code == 200
    assert api_client.get("/api/store/products/SKU-NEW-001").status_code == 404
    assert (
        api_client.get("/api/admin/products/SKU-NEW-001").json()["status"]
        == "archived"
    )

    reactivated = api_client.patch(
        "/api/admin/products/SKU-NEW-001", json={"status": "active"}
    )
    assert reactivated.status_code == 200

    inventory = api_client.put(
        "/api/admin/products/SKU-NEW-001/inventory/default",
        json={"qty_available": 9},
    )
    assert inventory.status_code == 200, inventory.text
    assert inventory.json()["available_qty"] == 9
    east = api_client.put(
        "/api/admin/products/SKU-NEW-001/inventory/east",
        json={"warehouse_name": "华东仓", "qty_available": 3},
    )
    assert east.status_code == 200
    assert east.json()["available_qty"] == 12
    assert {
        row["warehouse_id"] for row in east.json()["inventory"]
    } == {"default", "east"}

    zero = api_client.put(
        "/api/admin/products/SKU-NEW-001/inventory/east",
        json={"qty_available": 0},
    )
    assert zero.status_code == 200
    detail = api_client.get("/api/store/products/SKU-NEW-001")
    assert detail.status_code == 200
    assert detail.json()["available_qty"] == 9
    assert detail.json()["in_stock"] is True

    negative = api_client.put(
        "/api/admin/products/SKU-NEW-001/inventory/default",
        json={"qty_available": -1},
    )
    assert negative.status_code == 422

    db_session.expire_all()
    events = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type.in_(
            ("product_created", "product_updated", "inventory_updated")
        ))
        .order_by(AuditEvent.id)
    ).scalars().all()
    assert events[0].event_type == "product_created"
    assert any(event.event_type == "product_updated" for event in events)
    assert any(event.event_type == "inventory_updated" for event in events)
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_agent_can_read_products_but_cannot_mutate(
    api_client: TestClient, shop_data, admin_login
) -> None:
    admin_login("agent@test.local", "agent123456")
    assert api_client.get("/api/admin/products").status_code == 200
    assert api_client.get("/api/admin/products/SKU-EAR-001").status_code == 200
    assert (
        api_client.post(
            "/api/admin/products",
            json={"sku": "SKU-AGENT-001", "title": "越权", "price_cents": 100},
        ).status_code
        == 403
    )
    assert (
        api_client.patch(
            "/api/admin/products/SKU-EAR-001", json={"title": "越权修改"}
        ).status_code
        == 403
    )
    assert (
        api_client.put(
            "/api/admin/products/SKU-EAR-001/inventory/default",
            json={"qty_available": 999},
        ).status_code
        == 403
    )
