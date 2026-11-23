from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Inventory

ADDRESS = {
    "recipient_name": "AI 事实查询顾客",
    "recipient_phone": "13800000015",
    "region": "华东",
    "city": "上海",
    "detail": "AI 事实查询路 15 号",
}


def _paid_order(
    client: TestClient,
    key: str,
    items: tuple[tuple[str, int], ...] = (("SKU-EAR-001", 1),),
) -> str:
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
    return body["order_no"]


def _deliver(client: TestClient, order_no: str, tracking_no: str) -> None:
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


def _chat(client: TestClient, text: str) -> dict:
    response = client.post("/api/chat", json={"text": text})
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


def test_chat_refund_progress_uses_refund_ledger(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    _stock_charger(db_session, shop_data)
    login()
    order_no = _paid_order(
        api_client,
        "chat-refund-ledger-0001",
        (("SKU-EAR-001", 1), ("SKU-PWR-001", 1)),
    )
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

    body = _chat(api_client, f"订单 {order_no} 退款进度")
    assert body["intent"] == "REFUND_STATUS"
    assert "退款申请已执行" in body["reply"]
    assert "199.00" in body["reply"]
    assert "取消未发货商品" in body["reply"]
    assert "到账时间以支付渠道为准" in body["reply"]
    assert "已退款" not in body["reply"]
    assert any(
        citation.startswith("refunds:") for citation in body["citations"]
    )


def test_chat_invoice_progress_and_adjustment(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "chat-invoice-status-0001")
    before = _chat(api_client, f"订单 {order_no} 发票状态")
    assert before["intent"] == "INVOICE_STATUS"
    assert "暂无发票申请" in before["reply"]

    created = api_client.post(
        "/api/store/invoices",
        json={
            "order_no": order_no,
            "invoice_type": "personal",
            "title": "AI 测试抬头",
        },
    )
    assert created.status_code == 200, created.text
    invoice = created.json()
    admin_login()
    issued = api_client.post(
        f"/api/admin/invoices/{invoice['id']}/issue",
        json={"invoice_no": "INV-CHAT-0001"},
    )
    assert issued.status_code == 200, issued.text
    refund = api_client.post(
        "/api/admin/action-proposals",
        json={
            "action_type": "refund",
            "order_no": order_no,
            "amount_cents": 1000,
            "reason": "开票后价保退款",
        },
    )
    assert refund.status_code == 200, refund.text
    assert (
        api_client.post(
            f"/api/admin/proposals/{refund.json()['proposal_id']}/approve"
        ).status_code
        == 200
    )

    body = _chat(api_client, f"订单 {order_no} 发票进度")
    assert body["intent"] == "INVOICE_STATUS"
    assert "已开票" in body["reply"]
    assert "净支付" in body["reply"]
    assert "需作废后按净支付重开" in body["reply"]
    assert any(
        citation.startswith("invoice:") for citation in body["citations"]
    )


def test_chat_repair_progress_includes_return_tracking(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "chat-repair-status-0001")
    admin_login()
    _deliver(api_client, order_no, "SF-CHAT-REP-OUT-0001")
    created = api_client.post(
        "/api/store/repairs",
        json={
            "order_no": order_no,
            "sku": "SKU-EAR-001",
            "issue": "右耳无声",
        },
    )
    assert created.status_code == 200, created.text
    repair_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/approve").status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/store/repairs/{repair_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-CHAT-REP-BACK"},
        ).status_code
        == 200
    )
    assert (
        api_client.post(f"/api/admin/repairs/{repair_id}/receive").status_code
        == 200
    )
    completed = api_client.post(
        f"/api/admin/repairs/{repair_id}/complete",
        json={
            "resolution_note": "已更换右耳单元并寄回",
            "return_carrier": "顺丰速运",
            "return_tracking_no": "SF-CHAT-REP-OUT-0002",
        },
    )
    assert completed.status_code == 200, completed.text

    body = _chat(api_client, f"订单 {order_no} 维修进度")
    assert body["intent"] == "REPAIR_STATUS"
    assert "维修寄回中" in body["reply"]
    assert "SF-CHAT-REP-OUT-0002" in body["reply"]
    assert "已更换右耳单元并寄回" in body["reply"]
    assert any(
        citation.startswith("repair:") for citation in body["citations"]
    )


def test_chat_return_progress_includes_tracking_and_refund(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "chat-return-status-0001")
    admin_login()
    _deliver(api_client, order_no, "SF-CHAT-RET-OUT-0001")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "聊天查询退货进度",
            "resolution": "refund",
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/store/returns/{return_id}/tracking",
            json={"carrier": "顺丰速运", "tracking_no": "SF-CHAT-RET-BACK"},
        ).status_code
        == 200
    )

    body = _chat(api_client, f"订单 {order_no} 退货进度")
    assert body["intent"] == "RETURN_STATUS"
    assert "回寄中" in body["reply"]
    assert "SF-CHAT-RET-BACK" in body["reply"]
    assert any(
        citation.startswith("return:") for citation in body["citations"]
    )


def test_chat_exchange_progress_includes_replacement_shipment(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "chat-exchange-status-0001")
    admin_login()
    _deliver(api_client, order_no, "SF-CHAT-EXC-OUT-0001")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "聊天查询换货补发进度",
            "resolution": "exchange",
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    received = api_client.post(f"/api/admin/returns/{return_id}/receive")
    assert received.status_code == 200, received.text
    replacement_no = received.json()["replacement_order_no"]
    assert (
        api_client.post(
            f"/api/admin/orders/{replacement_no}/shipment",
            json={"carrier": "京东物流", "tracking_no": "JD-CHAT-EXC-BACK-0001"},
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/admin/orders/{replacement_no}/shipment/advance",
            json={"status": "in_transit"},
        ).status_code
        == 200
    )

    body = _chat(api_client, f"订单 {order_no} 退货进度")
    assert body["intent"] == "RETURN_STATUS"
    assert f"换货订单 {replacement_no}" in body["reply"]
    assert "补发" in body["reply"]
    assert "JD-CHAT-EXC-BACK-0001" in body["reply"]
    assert "运输中" in body["reply"]
    assert any(
        citation.startswith("return:") for citation in body["citations"]
    )


def test_chat_return_progress_includes_deduction_appeal(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "chat-appeal-status-0001")
    admin_login()
    _deliver(api_client, order_no, "SF-CHAT-APPEAL-OUT-0001")
    created = api_client.post(
        "/api/store/returns",
        json={
            "order_no": order_no,
            "reason": "聊天查询扣减申诉",
            "resolution": "refund",
        },
    )
    assert created.status_code == 200, created.text
    return_id = created.json()["id"]
    assert (
        api_client.post(f"/api/admin/returns/{return_id}/approve").status_code
        == 200
    )
    received = api_client.post(
        f"/api/admin/returns/{return_id}/receive",
        json={
            "deduction_cents": 500,
            "inspection_note": "包装破损",
        },
    )
    assert received.status_code == 200, received.text
    appeal = api_client.post(
        f"/api/store/returns/{return_id}/appeal",
        json={"reason": "商品完好，申请复核扣减"},
    )
    assert appeal.status_code == 200, appeal.text

    pending = _chat(api_client, f"订单 {order_no} 退货进度")
    assert pending["intent"] == "RETURN_STATUS"
    assert "扣减申诉待处理" in pending["reply"]
    assert "申请 5.00 元" in pending["reply"]

    accepted = api_client.post(
        f"/api/admin/returns/{return_id}/appeal/accept",
        json={"approved_cents": 300, "note": "部分支持"},
    )
    assert accepted.status_code == 200, accepted.text
    resolved = _chat(api_client, f"订单 {order_no} 退货进度")
    assert resolved["intent"] == "RETURN_STATUS"
    assert "扣减申诉已支持" in resolved["reply"]
    assert "核准 3.00 元" in resolved["reply"]
    assert any(
        citation.startswith("return:") for citation in resolved["citations"]
    )