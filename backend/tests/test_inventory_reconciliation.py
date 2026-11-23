from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Inventory, Product

ADDRESS = {
    "recipient_name": "对账顾客",
    "recipient_phone": "13800000014",
    "region": "华东",
    "city": "上海",
    "detail": "对账路 14 号",
}


def test_inventory_reconciliation_detects_drift(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    admin_login()
    created = api_client.post(
        "/api/admin/products",
        json={
            "sku": "SKU-RECON-001",
            "title": "对账商品",
            "price_cents": 5000,
            "initial_qty": 4,
        },
    )
    assert created.status_code == 200, created.text

    initial = api_client.get("/api/admin/inventory-reconciliation")
    assert initial.status_code == 200, initial.text
    body = initial.json()
    assert body["tracked"] == 1
    assert body["matched"] == 1
    assert body["untracked"] >= 1
    assert body["discrepancies"] == []

    api_client.post(
        "/api/store/cart/items", json={"sku": "SKU-RECON-001", "qty": 1}
    )
    checkout = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "recon-checkout"},
    )
    assert checkout.status_code == 201, checkout.text
    after_checkout = api_client.get(
        "/api/admin/inventory-reconciliation"
    ).json()
    assert after_checkout["tracked"] == 1
    assert after_checkout["matched"] == 1

    product = db_session.execute(
        select(Product).where(Product.sku == "SKU-RECON-001")
    ).scalar_one()
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == product.id,
            Inventory.warehouse_id == "default",
        )
    ).scalar_one()
    inventory.qty_available = 5
    db_session.commit()

    drift = api_client.get("/api/admin/inventory-reconciliation")
    assert drift.status_code == 200, drift.text
    body = drift.json()
    assert body["tracked"] == 1
    assert body["matched"] == 0
    assert len(body["discrepancies"]) == 1
    row = body["discrepancies"][0]
    assert row["sku"] == "SKU-RECON-001"
    assert row["qty_available"] == 5
    assert row["movement_total"] == 3
    assert row["delta"] == 2

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert (
        api_client.get("/api/admin/inventory-reconciliation").status_code
        == 200
    )
