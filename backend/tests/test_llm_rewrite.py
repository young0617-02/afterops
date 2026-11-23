from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.chat import handle_chat
from app.ai.providers import (
    LLMResult,
    ProviderError,
    TemplateProvider,
    get_provider,
)
from app.domain.models import AuditEvent, Customer
from app.settings import Settings


class FakeProvider:
    name = "fake"

    def __init__(self, text: str | None = None, error: str | None = None):
        self.text = text
        self.error = error
        self.calls = 0

    def generate(self, req) -> LLMResult:
        self.calls += 1
        if self.error:
            raise ProviderError(self.error)
        return LLMResult(text=self.text or req.approved_text, provider=self.name)


@pytest.fixture()
def chat_customer(db_session, shop_data) -> Customer:
    customer = Customer(
        merchant_id=shop_data["merchant"].id,
        phone="13800000001",
        name="测试顾客",
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def test_template_provider_leaves_reply_unchanged(
    db_session, chat_customer
) -> None:
    result = handle_chat(
        db_session,
        chat_customer,
        "SKU-EAR-001 多少钱",
        provider=TemplateProvider(),
    )
    assert result.provider == "template"
    assert result.degraded is False
    assert "899.00" in result.reply


def test_safe_rewrite_is_used(db_session, chat_customer) -> None:
    rewritten = (
        "这款降噪耳机 Pro（SKU-EAR-001）页面价 899.00 元，当前可售 15 件；"
        "库存为查询时点快照，价格以提交订单时页面展示为准。如需人工，回复「转人工」。"
    )
    provider = FakeProvider(rewritten)
    result = handle_chat(
        db_session, chat_customer, "SKU-EAR-001 多少钱", provider=provider
    )
    assert result.reply == rewritten
    assert result.provider == "fake"
    assert result.degraded is False
    assert provider.calls == 1

    event = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "chat_answered")
    ).scalar_one()
    assert event.payload_json["llm_provider"] == "fake"
    assert event.payload_json["degraded"] is False


def test_commitment_rewrite_falls_back(db_session, chat_customer) -> None:
    provider = FakeProvider("已退款，保证到账。如需人工，回复「转人工」。")
    result = handle_chat(
        db_session, chat_customer, "SKU-EAR-001 多少钱", provider=provider
    )
    assert result.provider == "fake"
    assert result.degraded is True
    assert result.fallback_reason == "commitment_leak"
    assert "保证" not in result.reply
    assert "已退款" not in result.reply
    assert "899.00" in result.reply


def test_unapproved_number_falls_back(db_session, chat_customer) -> None:
    provider = FakeProvider(
        "这款降噪耳机 Pro（SKU-EAR-001）页面价 899.00 元，当前可售 99 件。"
        "如需人工，回复「转人工」。"
    )
    result = handle_chat(
        db_session, chat_customer, "SKU-EAR-001 多少钱", provider=provider
    )
    assert result.degraded is True
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("unapproved_numbers:")
    assert "可售 99" not in result.reply


def test_missing_handoff_falls_back(db_session, chat_customer) -> None:
    provider = FakeProvider(
        "这款降噪耳机 Pro（SKU-EAR-001）页面价 899.00 元，当前可售 15 件。"
    )
    result = handle_chat(
        db_session, chat_customer, "SKU-EAR-001 多少钱", provider=provider
    )
    assert result.degraded is True
    assert result.fallback_reason == "missing_handoff"
    assert "转人工" in result.reply


def test_missing_approved_number_falls_back(db_session, chat_customer) -> None:
    provider = FakeProvider(
        "这款降噪耳机 Pro（SKU-EAR-001）页面价 899.00 元。"
        "如需人工，回复「转人工」。"
    )
    result = handle_chat(
        db_session, chat_customer, "SKU-EAR-001 多少钱", provider=provider
    )
    assert result.degraded is True
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("missing_numbers:")
    assert "当前可售" in result.reply


def test_weakened_handoff_falls_back(db_session, chat_customer) -> None:
    provider = FakeProvider(
        "这款降噪耳机 Pro（SKU-EAR-001）页面价 899.00 元，当前可售 15 件。"
        "已记录您的转人工请求。"
    )
    result = handle_chat(
        db_session, chat_customer, "SKU-EAR-001 多少钱", provider=provider
    )
    assert result.degraded is True
    assert result.fallback_reason == "missing_handoff"
    assert "回复「转人工」" in result.reply


def test_provider_error_falls_back(db_session, chat_customer) -> None:
    provider = FakeProvider(error="timeout")
    result = handle_chat(
        db_session, chat_customer, "SKU-EAR-001 多少钱", provider=provider
    )
    assert result.provider == "fake"
    assert result.degraded is True
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("provider_error:")
    assert "899.00" in result.reply


def test_default_provider_is_template() -> None:
    assert get_provider().name == "template"


def test_provider_configuration_requires_model() -> None:
    cfg = Settings(llm_provider="ollama", ollama_model="")
    with pytest.raises(ProviderError):
        get_provider(cfg)

    cfg = Settings(llm_provider="openai", openai_base_url="", openai_api_key="", openai_model="")
    with pytest.raises(ProviderError):
        get_provider(cfg)
