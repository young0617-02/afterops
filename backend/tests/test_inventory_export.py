from __future__ import annotations

from fastapi.testclient import TestClient


def _product(client: TestClient, sku: str, title: str) -> None:
    created = client.post(
        "/api/admin/products",
        json={
            "sku": sku,
            "title": title,
            "category": "测试",
            "price_cents": 1000,
            "initial_qty": 3,
        },
    )
    assert created.status_code == 200, created.text


def test_inventory_csv_exports_rows_and_guards_formulas(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    _product(api_client, "SKU-EXPORT-001", "=SUM(1,1)")
    _product(api_client, "SKU-EXPORT-002", "普通商品")

    response = api_client.get("/api/admin/inventory/export.csv")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert text.startswith("\ufeff")
    assert "SKU,商品,类目,仓库编号,仓库名称,可售库存,预留库存,更新时间" in text
    assert "SKU-EXPORT-001" in text
    assert "'=SUM(1,1)" in text
    assert "SKU-EXPORT-002" in text

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert api_client.get("/api/admin/inventory/export.csv").status_code == 200


def test_inventory_movements_csv_filters_and_auth(
    api_client: TestClient,
    shop_data,
    admin_login,
) -> None:
    admin_login()
    _product(api_client, "SKU-MOVE-001", "流水商品一")
    _product(api_client, "SKU-MOVE-002", "流水商品二")
    adjusted = api_client.put(
        "/api/admin/products/SKU-MOVE-001/inventory/default",
        json={"qty_available": 1},
    )
    assert adjusted.status_code == 200, adjusted.text

    response = api_client.get(
        "/api/admin/inventory-movements/export.csv",
        params={"sku": "SKU-MOVE-001"},
    )
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8")
    assert "时间,SKU,商品,仓库编号" in text
    assert "SKU-MOVE-001" in text
    assert "商品创建" in text
    assert "手工调整" in text
    assert ",-2," in text
    assert "SKU-MOVE-002" not in text

    empty = api_client.get(
        "/api/admin/inventory-movements/export.csv",
        params={"from": "2099-01-01"},
    )
    assert empty.status_code == 200
    lines = [
        line
        for line in empty.content.decode("utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1

    api_client.delete("/api/admin/session")
    assert (
        api_client.get("/api/admin/inventory-movements/export.csv").status_code
        == 401
    )