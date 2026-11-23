from __future__ import annotations

from fastapi.testclient import TestClient

ADDRESS = {
    "recipient_name": "判责测试顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "判责测试路 1 号",
}


def _paid_order(client: TestClient, key: str) -> str:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    checkout = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert checkout.status_code == 201, checkout.text
    body = checkout.json()
    assert (
        client.post(
            f"/api/store/payments/{body['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )
    return body["order_no"]


def _delivery(client: TestClient, order_no: str, tracking_no: str) -> None:
    created = client.post(
        f"/api/admin/orders/{order_no}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": tracking_no},
    )
    assert created.status_code == 200, created.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        response = client.post(
            f"/api/admin/orders/{order_no}/shipment/advance",
            json={"status": status},
        )
        assert response.status_code == 200, response.text


def _approved_return(
    client: TestClient, key: str
) -> tuple[str, int, int]:
    order_no = _paid_order(client, key)
    _delivery(client, order_no, f"SF-RESP-{key.upper()}")
    created = client.post(
        "/api/store/returns",
        json={"order_no": order_no, "reason": "责任判定测试"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    return_id = body["id"]
    item_id = body["items"][0]["id"]
    assert (
        client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    return order_no, return_id, item_id


def test_return_responsibility_saved_in_view_analytics_and_csv(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    order_no, return_id, item_id = _approved_return(
        api_client, "responsibility-0001"
    )
    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "disposition": "restock",
                    "deduction_cents": 500,
                    "responsibility": "customer",
                }
            ],
            "inspection_note": "缺原装耳塞套，顾客责任扣减 5 元",
        },
    )
    assert received.status_code == 200, received.text
    assert received.json()["items"][0]["responsibility"] == "customer"

    mine = api_client.get("/api/store/returns")
    assert mine.status_code == 200, mine.text
    row = next(item for item in mine.json() if item["id"] == return_id)
    assert row["items"][0]["responsibility"] == "customer"

    analytics = api_client.get(
        "/api/admin/returns/analytics", params={"days": 30}
    )
    assert analytics.status_code == 200, analytics.text
    stats = {
        item["responsibility"]: item
        for item in analytics.json()["responsibilities"]
    }
    assert stats["customer"]["items"] == 1
    assert stats["customer"]["qty"] == 1
    assert stats["customer"]["deduction_cents"] == 500
    assert stats["undetermined"]["items"] == 0

    report = api_client.get(
        "/api/admin/returns/inspection-report.csv", params={"days": 30}
    )
    assert report.status_code == 200, report.text
    assert "text/csv" in report.headers["content-type"]
    content = report.content.decode("utf-8-sig")
    assert "退货单号,订单号,顾客手机号,SKU,商品,数量,处置,责任判定" in content
    assert order_no in content
    assert "SKU-EAR-001" in content
    assert "顾客责任" in content
    assert "5.00" in content
    assert "缺原装耳塞套，顾客责任扣减 5 元" in content
    assert "owner@test.local" in content


def test_merchant_responsibility_rejects_deduction(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    _order_no, return_id, item_id = _approved_return(
        api_client, "responsibility-0002"
    )
    blocked = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "disposition": "restock",
                    "deduction_cents": 100,
                    "responsibility": "merchant",
                }
            ],
            "inspection_note": "商家责任但尝试扣减",
        },
    )
    assert blocked.status_code == 422
    assert "商家责任不支持质检扣减" in blocked.json()["detail"]

    invalid = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "disposition": "restock",
                    "responsibility": "platform",
                }
            ]
        },
    )
    assert invalid.status_code == 422

    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "disposition": "restock",
                    "responsibility": "merchant",
                }
            ],
            "inspection_note": "商家责任，全额退款",
        },
    )
    assert received.status_code == 200, received.text
    assert received.json()["items"][0]["responsibility"] == "merchant"
    assert received.json()["inspection_deduction_cents"] == 0


def test_inspection_report_escapes_formula_cells(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    admin_login()
    _order_no, return_id, item_id = _approved_return(
        api_client, "responsibility-0003"
    )
    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "items": [
                {
                    "item_id": item_id,
                    "disposition": "restock",
                    "responsibility": "other",
                }
            ],
            "inspection_note": '=HYPERLINK("http://example.com")',
        },
    )
    assert received.status_code == 200, received.text
    report = api_client.get(
        "/api/admin/returns/inspection-report.csv", params={"days": 30}
    )
    assert report.status_code == 200, report.text
    content = report.content.decode("utf-8-sig")
    assert "'=HYPERLINK" in content
    assert ",=HYPERLINK" not in content
