from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Inventory

ADDRESS = {
    "recipient_name": "退款记录顾客",
    "recipient_phone": "13800000013",
    "region": "华东",
    "city": "上海",
    "detail": "退款记录路 13 号",
}


def _paid_order(
    client: TestClient,
    key: str,
    items: tuple[tuple[str, int], ...] = (("SKU-EAR-001", 1),),
) -> dict:
    for sku, qty in items:
        response = client.post(
            "/api/store/cart/items", json={"sku": sku, "qty": qty}
        )
        assert response.status_code == 200, response.text
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
    return body


def _detail(client: TestClient, order_no: str) -> dict:
    response = client.get(f"/api/store/orders/{order_no}")
    assert response.status_code == 200, response.text
    return response.json()


def _stock_charger(db_session, shop_data) -> None:
    inventory = db_session.execute(
        select(Inventory).where(
            Inventory.product_id == shop_data["charger"].id,
            Inventory.warehouse_id == "华东",
        )
    ).scalar_one()
    inventory.qty_available = 5
    db_session.commit()


def test_order_without_refunds_has_empty_ledger(
    api_client: TestClient,
    shop_data,
    login,
) -> None:
    login()
    order = _paid_order(api_client, "refund-ledger-empty-0001")
    detail = _detail(api_client, order["order_no"])
    assert detail["refunds"] == []
    assert detail["refunded_total_cents"] == 0
    assert detail["refunded_total_display"] == "0.00"
    assert detail["net_paid_cents"] == order["amount_cents"]
    assert detail["net_paid_display"] == order["amount_display"]


def test_customer_sees_full_refund_ledger_after_approval(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order = _paid_order(api_client, "refund-ledger-full-0001")
    order_no = order["order_no"]
    chat = api_client.post(
        "/api/chat", json={"text": f"订单 {order_no} 我要退款"}
    )
    assert chat.status_code == 200, chat.text
    proposal_id = chat.json()["proposals"][0]["id"]

    admin_login()
    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text

    detail = _detail(api_client, order_no)
    assert len(detail["refunds"]) == 1
    refund = detail["refunds"][0]
    assert refund["kind"] == "refund"
    assert refund["amount_cents"] == order["amount_cents"]
    assert refund["amount_display"] == order["amount_display"]
    assert refund["status"] == "executed"
    assert refund["reason"]
    assert refund["provider_refund_id"].startswith("MOCK-REFUND-")
    assert refund["executed_at"] is not None
    assert detail["refunded_total_cents"] == order["amount_cents"]
    assert detail["net_paid_cents"] == 0
    assert detail["net_paid_display"] == "0.00"


def test_partial_cancel_refund_appears_in_customer_ledger(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order = _paid_order(
        api_client,
        "refund-ledger-cancel-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
    order_no = order["order_no"]
    requested = api_client.post(
        f"/api/store/orders/{order_no}/cancel-request",
        json={"items": [{"sku": "SKU-PWR-001", "qty": 1}]},
    )
    assert requested.status_code == 200, requested.text

    admin_login()
    approved = api_client.post(
        f"/api/admin/proposals/{requested.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    detail = _detail(api_client, order_no)
    assert len(detail["refunds"]) == 1
    refund = detail["refunds"][0]
    assert refund["amount_cents"] == 19900
    assert "取消未发货商品" in refund["reason"]
    assert "SKU-PWR-001" in refund["reason"]
    assert detail["refunded_total_cents"] == 19900
    assert detail["net_paid_cents"] == order["amount_cents"] - 19900


def test_compensation_is_listed_but_does_not_reduce_net_paid(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order = _paid_order(api_client, "refund-ledger-comp-0001")
    order_no = order["order_no"]

    admin_login()
    created = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "compensation",
            "order_no": order_no,
            "amount_cents": 1000,
            "reason": "物流延迟补偿",
        },
    )
    assert created.status_code == 200, created.text
    approved = api_client.post(
        f"/api/admin/proposals/{created.json()['proposal_id']}/approve"
    )
    assert approved.status_code == 200, approved.text

    detail = _detail(api_client, order_no)
    assert len(detail["refunds"]) == 1
    assert detail["refunds"][0]["kind"] == "compensation"
    assert detail["refunds"][0]["amount_cents"] == 1000
    assert detail["refunds"][0]["reason"] == "物流延迟补偿"
    assert detail["refunded_total_cents"] == 0
    assert detail["net_paid_cents"] == order["amount_cents"]
    assert detail["payment_status"] == "paid"
