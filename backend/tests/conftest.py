from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from httpx import Headers
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_session
from app.domain import models  # noqa: F401 - register tables on Base.metadata
from app.domain.models import Inventory, Merchant, PolicyEntry, Product, User
from app.main import app
from app.services.csrf import (
    ADMIN_CSRF_COOKIE,
    ADMIN_CSRF_HEADER,
    CUSTOMER_CSRF_COOKIE,
    CUSTOMER_CSRF_HEADER,
)
from app.services.passwords import hash_password
from app.settings import settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_original_testclient_request = TestClient.request


def _request_with_csrf(self, method, url, **kwargs):
    if method.upper() in _UNSAFE_METHODS:
        headers = Headers(kwargs.pop("headers", None))
        if not headers.get(CUSTOMER_CSRF_HEADER):
            customer_token = self.cookies.get(CUSTOMER_CSRF_COOKIE)
            if customer_token:
                headers[CUSTOMER_CSRF_HEADER] = customer_token
        if not headers.get(ADMIN_CSRF_HEADER):
            admin_token = self.cookies.get(ADMIN_CSRF_COOKIE)
            if admin_token:
                headers[ADMIN_CSRF_HEADER] = admin_token
        kwargs["headers"] = headers
    return _original_testclient_request(self, method, url, **kwargs)


TestClient.request = _request_with_csrf


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Factory() as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def api_client(db_session: Session) -> Iterator[TestClient]:
    def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def shop_data(db_session: Session) -> dict[str, object]:
    merchant = Merchant(name=settings.demo_merchant_name)
    db_session.add(merchant)
    db_session.flush()

    earphone = Product(
        merchant_id=merchant.id,
        sku="SKU-EAR-001",
        title="降噪耳机 Pro",
        category="耳机",
        description="主动降噪耳机",
        brand="声谷",
        specs="降噪 45dB / 续航 32h",
        price_cents=89900,
        support_7d_no_reason=True,
        listed_at=date(2026, 3, 18),
    )
    charger = Product(
        merchant_id=merchant.id,
        sku="SKU-PWR-001",
        title="快充移动电源",
        category="充电器",
        description="20000mAh 移动电源",
        brand="沃乘",
        specs="20000mAh / 22.5W",
        price_cents=19900,
        support_7d_no_reason=True,
        listed_at=date(2025, 11, 2),
    )
    db_session.add_all([earphone, charger])
    db_session.flush()

    owner = User(
        merchant_id=merchant.id,
        email="owner@test.local",
        password_hash=hash_password(settings.demo_owner_password),
        role="owner",
        status="active",
    )
    agent = User(
        merchant_id=merchant.id,
        email="agent@test.local",
        password_hash=hash_password("agent123456"),
        role="agent",
        status="active",
    )
    db_session.add_all([owner, agent])

    db_session.add_all(
        [
            Inventory(
                merchant_id=merchant.id,
                product_id=earphone.id,
                warehouse_id="华东",
                warehouse_name="上海青浦仓",
                qty_available=10,
            ),
            Inventory(
                merchant_id=merchant.id,
                product_id=earphone.id,
                warehouse_id="华南",
                warehouse_name="东莞松山湖仓",
                qty_available=5,
            ),
            Inventory(
                merchant_id=merchant.id,
                product_id=charger.id,
                warehouse_id="华东",
                warehouse_name="上海青浦仓",
                qty_available=0,
            ),
        ]
    )
    policy_v1 = PolicyEntry(
        merchant_id=merchant.id,
        entry_id="POLICY-RETURN",
        topic="return",
        title="7 天无理由退货（旧版）",
        body="旧版退货规则。",
        version="v1",
        valid_from=date(2025, 1, 1),
        valid_until=date(2026, 6, 30),
        published_at=date(2024, 12, 20),
        source="测试政策",
    )
    policy_v2 = PolicyEntry(
        merchant_id=merchant.id,
        entry_id="POLICY-RETURN",
        topic="return",
        title="7 天无理由退货（现行）",
        body="现行退货规则，质检后由人工核定退款。",
        version="v2",
        valid_from=date(2026, 7, 1),
        valid_until=None,
        published_at=date(2026, 6, 20),
        source="测试政策",
    )
    refund_policy = PolicyEntry(
        merchant_id=merchant.id,
        entry_id="FAQ-REFUND-TIME",
        topic="refund_time",
        title="退款到账时间",
        body="退款到账时间以支付渠道为准。",
        version="v1",
        valid_from=date(2026, 1, 1),
        valid_until=None,
        published_at=date(2025, 12, 20),
        source="测试政策",
    )
    db_session.add_all([policy_v1, policy_v2, refund_policy])
    db_session.commit()
    return {
        "merchant": merchant,
        "earphone": earphone,
        "charger": charger,
        "policy_v1": policy_v1,
        "policy_v2": policy_v2,
    }


@pytest.fixture()
def otp_login():
    def _login(client: TestClient, phone: str) -> TestClient:
        otp = client.post("/api/store/otp/request", json={"phone": phone})
        assert otp.status_code == 202, otp.text
        code = otp.json()["debug_code"]
        assert code
        resp = client.post(
            "/api/store/session", json={"phone": phone, "code": code}
        )
        assert resp.status_code == 200, resp.text
        return client

    return _login


@pytest.fixture()
def login(api_client: TestClient, otp_login):
    def _login(phone: str = "13800000001", client: TestClient | None = None) -> TestClient:
        target = client or api_client
        return otp_login(target, phone)

    return _login


@pytest.fixture()
def admin_login(api_client: TestClient):
    def _login(
        email: str = "owner@test.local",
        password: str | None = None,
    ) -> TestClient:
        resp = api_client.post(
            "/api/admin/session",
            json={"email": email, "password": password or settings.demo_owner_password},
        )
        assert resp.status_code == 200, resp.text
        return api_client

    return _login
