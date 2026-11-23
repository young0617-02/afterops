from __future__ import annotations

from fastapi.testclient import TestClient


def test_uninitialized_merchant_returns_503(api_client: TestClient) -> None:
    resp = api_client.get("/api/store/products")
    assert resp.status_code == 503
    assert "seed" in resp.json()["detail"]


def test_list_products(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/products")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["sku"] == "SKU-EAR-001"
    assert rows[0]["available_qty"] == 15
    assert rows[0]["in_stock"] is True
    assert rows[1]["sku"] == "SKU-PWR-001"
    assert rows[1]["available_qty"] == 0
    assert rows[1]["in_stock"] is False


def test_search_and_category_filter(api_client: TestClient, shop_data) -> None:
    searched = api_client.get("/api/store/products", params={"q": "耳机"}).json()
    assert [row["sku"] for row in searched] == ["SKU-EAR-001"]

    filtered = api_client.get("/api/store/products", params={"category": "充电器"}).json()
    assert [row["sku"] for row in filtered] == ["SKU-PWR-001"]


def test_product_detail_and_inventory(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/products/SKU-EAR-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["price_display"] == "899.00"
    assert body["available_qty"] == 15
    assert {row["warehouse_id"] for row in body["inventory"]} == {"华东", "华南"}


def test_unknown_product_returns_404(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/products/SKU-NOT-FOUND")
    assert resp.status_code == 404


def test_categories(api_client: TestClient, shop_data) -> None:
    resp = api_client.get("/api/store/categories")
    assert resp.status_code == 200
    assert {row["category"] for row in resp.json()} == {"耳机", "充电器"}
