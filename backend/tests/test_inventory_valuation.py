from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Inventory, Product

ADDRESS = {
    "recipient_name": "成本测试顾客",
    "recipient_phone": "13800000019",
    "region": "华东",
    "city": "上海",
    "detail": "成本路 19 号",
}


def test_owner_sets_cost_and_agent_cannot_read_it(
    api_client: TestClient,
    shop_data,
    admin_login,
    db_session,
) -> None:
    admin_login()
    created = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-COST-001",
            "title": "成本测试商品",
            "price_cents": 19900,
            "cost_cents": 12000,
            "initial_qty": 3,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["cost_cents"] == 12000
    assert created.json()["cost_display"] == "120.00"

    updated = api_client.patch(
        "/api/admin/products/SKU-COST-001",
        json={"cost_cents": 15000},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["cost_cents"] == 15000

    negative = api_client.patch(
        "/api/admin/products/SKU-COST-001",
        json={"cost_cents": -1},
    )
    assert negative.status_code == 422

    admin_login("agent@test.local", "agent123456")
    listed = api_client.get("/api/admin/products").json()
    agent_row = next(row for row in listed if row["sku"] == "SKU-COST-001")
    assert agent_row["cost_cents"] is None
    assert agent_row["cost_display"] is None
    detail = api_client.get("/api/admin/products/SKU-COST-001")
    assert detail.status_code == 200
    assert detail.json()["cost_cents"] is None
    forbidden = api_client.patch(
        "/api/admin/products/SKU-COST-001",
        json={"cost_cents": 100},
    )
    assert forbidden.status_code == 403


def test_inventory_valuation_counts_on_hand_and_requires_owner(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    earphone = shop_data["earphone"]
    charger = shop_data["charger"]
    earphone.cost_cents = 30000
    charger.cost_cents = 10000
    charger_inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == charger.id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    charger_inventory.qty_available = 4
    db_session.commit()

    login()
    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "valuation-order"},
    )
    assert checkout.status_code == 201, checkout.text

    db_session.expire_all()
    valuation = api_client.get("/api/admin/inventory-valuation")
    assert valuation.status_code == 401

    admin_login()
    valuation = api_client.get("/api/admin/inventory-valuation")
    assert valuation.status_code == 200, valuation.text
    body = valuation.json()
    assert body["total_qty"] == 19
    assert body["total_value_cents"] == 490000
    lines = {
        (line["sku"], line["warehouse_id"]): line
        for line in body["lines"]
    }
    assert lines[("SKU-EAR-001", "华东")]["qty_on_hand"] == 10
    assert lines[("SKU-EAR-001", "华东")]["qty_available"] == 9
    assert lines[("SKU-EAR-001", "华东")]["qty_reserved"] == 1
    assert lines[("SKU-EAR-001", "华东")]["value_cents"] == 300000
    assert lines[("SKU-EAR-001", "华南")]["value_cents"] == 150000
    assert lines[("SKU-PWR-001", "华东")]["value_cents"] == 40000
    assert body["lines"][0]["sku"] == "SKU-EAR-001"
    assert body["lines"][0]["warehouse_id"] == "华东"

    payment = api_client.post(
        f"/api/store/payments/{checkout.json()['payment_id']}/mock-confirm"
    )
    assert payment.status_code == 200, payment.text
    paid_valuation = api_client.get(
        "/api/admin/inventory-valuation"
    ).json()
    assert paid_valuation["total_qty"] == 18
    assert paid_valuation["total_value_cents"] == 460000
    paid_lines = {
        (line["sku"], line["warehouse_id"]): line
        for line in paid_valuation["lines"]
    }
    assert paid_lines[("SKU-EAR-001", "华东")]["qty_available"] == 9
    assert paid_lines[("SKU-EAR-001", "华东")]["qty_reserved"] == 0

    filtered = api_client.get(
        "/api/admin/inventory-valuation?sku=SKU-EAR-001"
    )
    assert filtered.status_code == 200
    assert {line["sku"] for line in filtered.json()["lines"]} == {
        "SKU-EAR-001"
    }

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert api_client.get("/api/admin/inventory-valuation").status_code == 403
    product = db_session.execute(
        select(Product).where(Product.sku == "SKU-EAR-001")
    ).scalar_one()
    assert product.cost_cents == 30000
