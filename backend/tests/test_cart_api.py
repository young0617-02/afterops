from __future__ import annotations

from fastapi.testclient import TestClient


def test_cart_requires_login(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/cart")
    assert resp.status_code == 401


def test_cart_add_update_remove(api_client: TestClient, shop_data, login) -> None:
    login()
    empty = api_client.get("/api/store/cart")
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    added = api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 2})
    assert added.status_code == 200
    body = added.json()
    assert body["items"][0]["qty"] == 2
    assert body["payable_cents"] == 179800
    assert body["payable_display"] == "1798.00"

    updated = api_client.put("/api/store/cart/items/SKU-EAR-001", json={"qty": 3})
    assert updated.status_code == 200
    assert updated.json()["payable_cents"] == 269700

    removed = api_client.put("/api/store/cart/items/SKU-EAR-001", json={"qty": 0})
    assert removed.status_code == 200
    assert removed.json()["items"] == []


def test_cart_rejects_unknown_and_out_of_stock(api_client: TestClient, shop_data, login) -> None:
    login()
    unknown = api_client.post("/api/store/cart/items", json={"sku": "SKU-NOPE", "qty": 1})
    assert unknown.status_code == 404

    out_of_stock = api_client.post("/api/store/cart/items", json={"sku": "SKU-PWR-001", "qty": 1})
    assert out_of_stock.status_code == 409

    too_many = api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 99})
    assert too_many.status_code == 409


def test_cart_remove_missing_item(api_client: TestClient, shop_data, login) -> None:
    login()
    resp = api_client.delete("/api/store/cart/items/SKU-EAR-001")
    assert resp.status_code == 404
