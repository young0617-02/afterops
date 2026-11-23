from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import AuditEvent

ADDRESS = {
    "recipient_name": "发票测试顾客",
    "recipient_phone": "13800000017",
    "region": "华东",
    "city": "上海",
    "detail": "发票路 17 号",
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
        client.post(f"/api/store/payments/{body['payment_id']}/mock-confirm").status_code
        == 200
    )
    return body["order_no"]


def _request_invoice(
    client: TestClient,
    order_no: str,
    *,
    invoice_type: str = "personal",
    title: str = "张三",
    tax_no: str = "",
):
    return client.post(
        "/api/store/invoices",
        json={
            "order_no": order_no,
            "invoice_type": invoice_type,
            "title": title,
            "tax_no": tax_no,
            "email": "invoice@example.com",
        },
    )


def test_customer_requests_invoice_once(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "invoice-create")
    created = _request_invoice(api_client, order_no)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "requested"
    assert body["amount_cents"] == 89900
    assert body["invoice_type"] == "personal"

    mine = api_client.get("/api/store/invoices")
    assert mine.status_code == 200
    assert [row["id"] for row in mine.json()] == [body["id"]]
    duplicate = _request_invoice(api_client, order_no)
    assert duplicate.status_code == 409
    assert "已有进行中" in duplicate.json()["detail"]

    admin_login()
    listed = api_client.get("/api/admin/invoices")
    assert listed.status_code == 200
    assert any(row["id"] == body["id"] for row in listed.json())
    issued = api_client.post(
        f"/api/admin/invoices/{body['id']}/issue",
        json={"invoice_no": "INV-20261105-001"},
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["status"] == "issued"
    assert issued.json()["invoice_no"] == "INV-20261105-001"
    customer_view = api_client.get("/api/store/invoices").json()[0]
    assert customer_view["status"] == "issued"

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "invoice_issued")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["invoice_no"] == "INV-20261105-001"


def test_invoice_guards_and_company_tax_no(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    api_client.post("/api/store/cart/items", json={"sku": "SKU-EAR-001", "qty": 1})
    unpaid = api_client.post(
        "/api/store/checkout",
        json={"address": ADDRESS},
        headers={"Idempotency-Key": "invoice-unpaid"},
    )
    assert unpaid.status_code == 201, unpaid.text
    blocked = _request_invoice(api_client, unpaid.json()["order_no"])
    assert blocked.status_code == 409
    assert "已支付" in blocked.json()["detail"]

    order_no = _paid_order(api_client, "invoice-company")
    missing_tax = _request_invoice(
        api_client, order_no, invoice_type="company", title="某某科技"
    )
    assert missing_tax.status_code == 422
    company = _request_invoice(
        api_client,
        order_no,
        invoice_type="company",
        title="某某科技",
        tax_no="91310000MA1K000000",
    )
    assert company.status_code == 200, company.text

    api_client.delete("/api/store/session")
    login("13800000018")
    other = _request_invoice(api_client, order_no)
    assert other.status_code == 404

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    assert api_client.get("/api/admin/invoices").status_code == 200
    assert (
        api_client.post(
            f"/api/admin/invoices/{company.json()['id']}/issue",
            json={"invoice_no": "INV-X"},
        ).status_code
        == 403
    )


def test_admin_rejects_invoice(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "invoice-reject")
    created = _request_invoice(api_client, order_no)
    assert created.status_code == 200, created.text
    admin_login()
    rejected = api_client.post(
        f"/api/admin/invoices/{created.json()['id']}/reject",
        json={"reason": "抬头信息不完整"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reject_reason"] == "抬头信息不完整"
    customer_view = api_client.get("/api/store/invoices").json()[0]
    assert customer_view["status"] == "rejected"
    again = api_client.post(
        f"/api/admin/invoices/{created.json()['id']}/issue",
        json={"invoice_no": "INV-LATE"},
    )
    assert again.status_code == 409

    reapply = _request_invoice(api_client, order_no, title="重新申请抬头")
    assert reapply.status_code == 200, reapply.text
    assert reapply.json()["id"] != created.json()["id"]


def test_void_issued_invoice_and_reissue(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
    db_session,
) -> None:
    login()
    order_no = _paid_order(api_client, "invoice-void")
    created = _request_invoice(api_client, order_no)
    assert created.status_code == 200, created.text
    invoice_id = created.json()["id"]
    admin_login()
    issued = api_client.post(
        f"/api/admin/invoices/{invoice_id}/issue",
        json={"invoice_no": "INV-VOID-001"},
    )
    assert issued.status_code == 200, issued.text

    blank = api_client.post(
        f"/api/admin/invoices/{invoice_id}/void",
        json={"reason": "   "},
    )
    assert blank.status_code == 422
    voided = api_client.post(
        f"/api/admin/invoices/{invoice_id}/void",
        json={"reason": "税号录入错误，需要重开"},
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["status"] == "voided"
    assert voided.json()["void_reason"] == "税号录入错误，需要重开"
    assert voided.json()["voided_at"]

    again = api_client.post(
        f"/api/admin/invoices/{invoice_id}/void",
        json={"reason": "重复作废"},
    )
    assert again.status_code == 409
    customer_view = api_client.get("/api/store/invoices").json()
    assert len(customer_view) == 1
    assert customer_view[0]["status"] == "voided"
    assert customer_view[0]["void_reason"] == "税号录入错误，需要重开"

    reapply = _request_invoice(api_client, order_no, title="李四")
    assert reapply.status_code == 200, reapply.text
    assert reapply.json()["id"] != invoice_id
    assert reapply.json()["status"] == "requested"
    assert reapply.json()["title"] == "李四"
    history = api_client.get("/api/store/invoices").json()
    assert [row["status"] for row in history] == ["requested", "voided"]
    reissued = api_client.post(
        f"/api/admin/invoices/{reapply.json()['id']}/issue",
        json={"invoice_no": "INV-VOID-002"},
    )
    assert reissued.status_code == 200, reissued.text
    assert reissued.json()["invoice_no"] == "INV-VOID-002"

    audit = db_session.execute(
        select(AuditEvent)
        .where(AuditEvent.event_type == "invoice_voided")
        .order_by(AuditEvent.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.payload_json["invoice_no"] == "INV-VOID-001"
    assert audit.payload_json["reason"] == "税号录入错误，需要重开"


def test_void_requires_issued_and_owner_permission(
    api_client: TestClient,
    shop_data,
    login,
    admin_login,
) -> None:
    login()
    order_no = _paid_order(api_client, "invoice-void-guard")
    created = _request_invoice(api_client, order_no)
    assert created.status_code == 200, created.text
    invoice_id = created.json()["id"]
    admin_login()
    not_issued = api_client.post(
        f"/api/admin/invoices/{invoice_id}/void",
        json={"reason": "还没开票"},
    )
    assert not_issued.status_code == 409

    api_client.delete("/api/admin/session")
    agent = api_client.post(
        "/api/admin/session",
        json={"email": "agent@test.local", "password": "agent123456"},
    )
    assert agent.status_code == 200, agent.text
    forbidden = api_client.post(
        f"/api/admin/invoices/{invoice_id}/void",
        json={"reason": "客服尝试作废"},
    )
    assert forbidden.status_code == 403
