from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent, Conversation, Message
from app.main import app
from app.services.audit import verify_chain

ADDRESS = {
    "recipient_name": "聊天顾客",
    "recipient_phone": "13800000001",
    "region": "华东",
    "city": "上海",
    "detail": "聊天测试路 1 号",
}


def _checkout(client: TestClient, key: str) -> dict:
    client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 2})
    resp = client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _delivered_order(api_client: TestClient, admin_login, key: str) -> dict:
    order = _checkout(api_client, key)
    paid = api_client.post(
        f"/api/store/payments/{order['payment_id']}/mock-confirm"
    )
    assert paid.status_code == 200, paid.text
    admin_login()
    shipment = api_client.post(
        f"/api/admin/orders/{order['order_no']}/shipment",
        json={"carrier": "顺丰速运", "tracking_no": f"SF-{key[-12:]}"},
    )
    assert shipment.status_code == 200, shipment.text
    for status in ("in_transit", "out_for_delivery", "delivered"):
        advanced = api_client.post(
            f"/api/admin/orders/{order['order_no']}/shipment/advance",
            json={"status": status},
        )
        assert advanced.status_code == 200, advanced.text
    return order


def test_chat_requires_login(api_client: TestClient, shop_data) -> None:
    resp = api_client.post("/api/chat", json={"text": "你好"})
    assert resp.status_code == 401


def test_chat_order_status_with_citation_and_audit(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    order = _checkout(api_client, "chat-order-0001")
    resp = api_client.post("/api/chat", json={"text": f"订单 {order['order_no']} 状态"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "ORDER_STATUS"
    assert order["order_no"] in body["reply"]
    assert "待支付" in body["reply"]
    assert "1798.00" in body["reply"]
    assert f"order:{order['order_no']}" in body["citations"]
    assert body["handoff"]["available"] is True
    assert body["proposals"] == []

    db_session.expire_all()
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "chat_answered")
    ).scalars().all()
    assert len(events) == 1
    assert events[0].payload_json["intent"] == "ORDER_STATUS"
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_chat_return_request_creates_pending_return(
    api_client: TestClient, shop_data, login, admin_login, db_session
) -> None:
    login()
    order = _delivered_order(api_client, admin_login, "chat-return-0001")

    response = api_client.post(
        "/api/chat",
        json={"text": f"订单 {order['order_no']} 耳机有杂音 我要退货"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "RETURN_REQUEST"
    assert body["returns"][0]["status"] == "requested"
    assert body["returns"][0]["resolution"] == "refund"
    assert "退货退款申请" in body["reply"]
    assert "等待店主审核" in body["reply"]
    assert "已退款" not in body["reply"]
    assert "POLICY-RETURN@v2" in body["citations"]

    listed = api_client.get("/api/store/returns")
    assert listed.status_code == 200
    assert [row["status"] for row in listed.json()] == ["requested"]
    assert listed.json()[0]["resolution"] == "refund"

    again = api_client.post(
        "/api/chat",
        json={"text": f"订单 {order['order_no']} 我要退货"},
    )
    assert again.status_code == 200
    assert again.json()["returns"] == []
    assert "已有进行中的退货申请" in again.json()["reply"]

    db_session.expire_all()
    assert verify_chain(db_session, shop_data["merchant"].id) == (True, "ok")


def test_chat_return_rejects_unshipped_order(
    api_client: TestClient, shop_data, login
) -> None:
    login()
    order = _checkout(api_client, "chat-return-unpaid")
    response = api_client.post(
        "/api/chat",
        json={"text": f"订单 {order['order_no']} 我要退货"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["returns"] == []
    assert "只有已签收订单可以申请退货" in response.json()["reply"]


def test_chat_exchange_request_uses_exchange_resolution(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order = _delivered_order(api_client, admin_login, "chat-exchange-0001")
    response = api_client.post(
        "/api/chat",
        json={"text": f"订单 {order['order_no']} 尺寸不合适 我要换货"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "RETURN_REQUEST"
    assert body["returns"][0]["resolution"] == "exchange"
    assert "换货申请" in body["reply"]


def test_chat_policy_uses_current_version(api_client: TestClient, shop_data, login) -> None:
    login()
    resp = api_client.post("/api/chat", json={"text": "耳机能退吗"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "RETURN_POLICY"
    assert "规则依据" in body["reply"]
    assert "POLICY-RETURN@v2" in body["reply"]
    assert "POLICY-RETURN@v2" in body["citations"]
    assert "保证" not in body["reply"]


def test_chat_high_risk_never_confirms(api_client: TestClient, shop_data, login) -> None:
    login()
    resp = api_client.post("/api/chat", json={"text": "你能保证全额退款吗"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "HIGH_RISK"
    assert "人工" in body["reply"]
    assert "保证" not in body["reply"]
    assert body["handoff"]["queue"] == "complaint"
    assert body["proposals"] == []


def test_chat_human_request(api_client: TestClient, shop_data, login) -> None:
    login()
    resp = api_client.post("/api/chat", json={"text": "转人工"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "HUMAN_REQUEST"
    assert "人工" in body["reply"]
    assert body["handoff"]["ticket_ref"].startswith("TK-")


def test_chat_refund_status_reads_after_sales(
    api_client: TestClient, shop_data, login, admin_login
) -> None:
    login()
    order = _checkout(api_client, "chat-refund-status-0001")
    assert (
        api_client.post(
            f"/api/store/payments/{order['payment_id']}/mock-confirm"
        ).status_code
        == 200
    )

    before = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 退款进度"}
    )
    assert before.status_code == 200, before.text
    assert before.json()["intent"] == "REFUND_STATUS"
    assert "暂无售后申请" in before.json()["reply"]

    request = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 我要退款"}
    )
    assert request.status_code == 200, request.text
    proposal_id = request.json()["proposals"][0]["id"]

    pending = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 退款进度"}
    )
    pending_body = pending.json()
    assert pending_body["intent"] == "REFUND_STATUS"
    assert "等待人工审核" in pending_body["reply"]
    assert "1798.00" in pending_body["reply"]
    assert f"proposal:{proposal_id}" in pending_body["citations"]
    assert "已退款" not in pending_body["reply"]

    admin_login()
    approved = api_client.post(f"/api/admin/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text

    executed = api_client.post(
        "/api/chat", json={"text": f"订单 {order['order_no']} 退款进度"}
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["intent"] == "REFUND_STATUS"
    assert "退款申请已执行" in body["reply"]
    assert "处理引用" in body["reply"]
    assert "已退款" not in body["reply"]
    assert "到账时间以支付渠道为准" in body["reply"]


def test_chat_product_query(api_client: TestClient, shop_data, login) -> None:
    login()
    resp = api_client.post("/api/chat", json={"text": "SKU-EAR-001 多少钱"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "PRODUCT_INFO"
    assert "899.00" in body["reply"]
    assert "15" in body["reply"]
    assert "product:SKU-EAR-001" in body["citations"]


def test_chat_order_without_number_asks_for_it(
    api_client: TestClient, shop_data, login
) -> None:
    login()
    resp = api_client.post("/api/chat", json={"text": "我的订单到哪了"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "LOGISTICS_TRACK"
    assert "订单号" in body["reply"]
    assert body["citations"] == []


def test_chat_does_not_leak_other_customer_order(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    order = _checkout(api_client, "chat-owner-0001")

    with TestClient(app) as other:
        otp_login(other, "13900000005")
        resp = other.post("/api/chat", json={"text": f"订单 {order['order_no']} 状态"})
        assert resp.status_code == 200
        body = resp.json()
        assert "1798.00" not in body["reply"]
        assert "待支付" not in body["reply"]
        assert f"order:{order['order_no']}" not in body["citations"]


def test_chat_unknown_and_persistence(
    api_client: TestClient, shop_data, login, db_session
) -> None:
    login()
    resp = api_client.post("/api/chat", json={"text": "随便聊聊"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "UNKNOWN"
    assert "订单状态" in body["reply"]
    assert "人工" in body["reply"]

    db_session.expire_all()
    conversation = db_session.execute(
        select(Conversation).where(Conversation.id == body["conversation_id"])
    ).scalar_one()
    messages = db_session.execute(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    ).scalars().all()
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].tool_calls_json["audit_id"] == body["audit_id"]


def test_chat_conversation_ownership(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    first = api_client.post("/api/chat", json={"text": "你好"}).json()

    with TestClient(app) as other:
        otp_login(other, "13900000006")
        resp = other.post(
            "/api/chat",
            json={"text": "你好", "conversation_id": first["conversation_id"]},
        )
        assert resp.status_code == 404


def test_chat_history_and_restore(
    api_client: TestClient, shop_data, login, otp_login
) -> None:
    login()
    chat = api_client.post("/api/chat", json={"text": "你好"}).json()

    listed = api_client.get("/api/chat/conversations")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == chat["conversation_id"]
    assert listed.json()[0]["last_message"]

    detail = api_client.get(f"/api/chat/conversations/{chat['conversation_id']}")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["tool_calls_json"]["audit_id"] == chat["audit_id"]

    with TestClient(app) as other:
        otp_login(other, "13900000008")
        assert other.get("/api/chat/conversations").json() == []
        denied = other.get(f"/api/chat/conversations/{chat['conversation_id']}")
        assert denied.status_code == 404
