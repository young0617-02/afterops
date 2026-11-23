"""F-006：双时态知识库、版本链、TTL 与 stale 监控（含验收 A10）。"""
from __future__ import annotations

from datetime import date

import pytest

from src.kb import KnowledgeBase, tokenize
from src.schemas import today


@pytest.fixture()
def kb(shop_db):
    return KnowledgeBase(shop_db, as_of="2026-10-06")


def test_bitemporal_version_selection(kb):
    """同一 entry_id 的不同版本按查询时点选择：旧时点命中旧版，现行时点命中新版。"""
    old = kb.get_current(topic="refund_time", as_of="2026-03-01")
    new = kb.get_current(topic="refund_time", as_of="2026-10-06")
    assert (old.version, new.version) == ("v1", "v2")
    assert "1-10" in old.body and "1-15" in new.body
    assert new.supersedes == "v1"


def test_expired_entry_not_retrieved(kb):
    """618 延长价保活动在 2026-08-31 之后不应出现在检索结果里。"""
    hits = kb.search("价保 延长 60 天", as_of="2026-10-06")
    assert all(h.entry.entry_id != "PROMO-618-PRICEPROTECT" for h in hits)
    hits_july = kb.search("价保 延长 60 天", as_of="2026-07-15")
    assert any(h.entry.entry_id == "PROMO-618-PRICEPROTECT" for h in hits_july)


def test_A10_changing_valid_until_hides_old_answer(kb):
    """改一条政策的 valid_until → 旧答案不再出现，且有 stale 记录。"""
    assert kb.get_current(topic="return") is not None
    kb.set_entry_validity("POLICY-RETURN", "v2", "2026-01-01")
    assert kb.get_current(topic="return") is None, "已失效条款仍被当成现行规则"
    hits = kb.search("7 天无理由退货", as_of="2026-10-06")
    assert all(h.entry.version != "v2" or h.entry.entry_id != "POLICY-RETURN" for h in hits)
    assert kb.stale_events is not None


def test_A10_stale_retrieval_is_recorded_on_cache(kb):
    """缓存写入之后条款才失效 → 命中缓存时必须剔除，并留下 stale 记录。"""
    from dataclasses import replace
    from datetime import timedelta

    kb.ttl = 10_000
    first = kb.search("7 天无理由退货")
    cached = next(h for h in first if h.entry.entry_id == "POLICY-RETURN")
    expired = replace(cached.entry, valid_until=date.today() - timedelta(days=1))
    kb.entries[("POLICY-RETURN", "v2")] = expired  # 故意不重建索引，模拟「缓存后失效」

    second = kb.search("7 天无理由退货")
    assert all(h.entry.entry_id != "POLICY-RETURN" for h in second), "缓存里的过期条款未被剔除"
    assert any(e.kind == "stale_retrieval" for e in kb.stale_events), [e.kind for e in kb.stale_events]


def test_ttl_expiry_forces_refresh(kb):
    kb.search("退款到账")
    assert len(kb._cache) == 1
    kb.ttl = -1
    kb.search("退款到账")
    assert any(e.kind == "ttl_expired" for e in kb.stale_events)


def test_historical_only_flag_when_as_of_in_past(kb):
    hits = kb.search("618 价保 延长", as_of="2026-06-01")
    tagged = [h for h in hits if h.entry.entry_id == "PROMO-618-PRICEPROTECT"]
    assert tagged and tagged[0].reason == "historical_only"
    assert any(e.kind == "historical_only" for e in kb.stale_events)


def test_vector_backend_degrades_honestly(kb):
    """请求了不可用的向量后端：必须显式记录降级原因，不允许静默。"""
    kb2 = KnowledgeBase(kb.db_path, embedding="bge-m3")
    assert kb2.backend == "bm25"
    assert "降级" in kb2.backend_note
    assert any(e.kind == "backend_degraded" for e in kb2.stale_events)


def test_stats_shape(kb):
    s = kb.stats()
    assert s["entries"] == 16 and s["backend"] == "bm25"
    assert "stale_events" in s


def test_tokenizer_handles_mixed_text():
    toks = tokenize("SKU-NCM-001 降噪耳机 45dB")
    assert "sku" in toks and "ncm" in toks and "降噪" in toks and "45db" in toks


def test_today_helper_uses_as_of():
    assert today("2026-10-06") == date(2026, 10, 6)
    assert today(None) == date.today()
