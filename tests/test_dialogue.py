"""F-007：槽位跟踪、指代消解与上下文预算。"""
from __future__ import annotations

from src.dialogue import DialogueManager, SessionState, estimate_tokens
from src.schemas import DialogueSlots


def make(budget=200, max_turns=4):
    return DialogueManager(token_budget=budget, max_recent_turns=max_turns)


def test_order_slot_extraction():
    dm = make()
    st = SessionState(session_id="s")
    dm.begin_turn(st, "订单 ORD-2026-000101 的物流")
    assert st.slots.order_id == "ORD-2026-000101"
    assert st.slots.focus_order_ids == ["ORD-2026-000101"]


def test_loose_order_regex_does_not_swallow_phone():
    dm = make()
    st = SessionState(session_id="s")
    dm.begin_turn(st, "订单号 13800000001 帮我查下")
    assert st.slots.order_id is None


def test_sku_and_region_slots():
    dm = make()
    st = SessionState(session_id="s")
    dm.begin_turn(st, "SKU-FAN-Z9 华南仓有货吗")
    assert st.slots.sku == "SKU-FAN-Z9"
    assert st.slots.region == "华南"


def test_coreference_resolves_to_last_order():
    dm = make()
    st = SessionState(session_id="s")
    dm.begin_turn(st, "订单 ORD-2026-000102 到哪了")
    rewritten, report = dm.begin_turn(st, "这个订单能退吗")
    assert "ORD-2026-000102" in rewritten
    assert report.resolved.get("order_id") == "ORD-2026-000102"


def test_coreference_resolves_product_pronoun():
    dm = make()
    st = SessionState(session_id="s")
    dm.begin_turn(st, "SKU-SSD-1T 怎么样")
    rewritten, report = dm.begin_turn(st, "它支持保修多久")
    assert "SKU-SSD-1T" in rewritten
    assert report.resolved.get("sku") == "SKU-SSD-1T"


def test_context_budget_trims_oldest_and_counts():
    dm = make(budget=60, max_turns=3)
    st = SessionState(session_id="s")
    for i in range(8):
        dm.begin_turn(st, f"第{i}轮问题：订单 ORD-2026-0001{i} 的物流到哪里了什么时候能到")
        dm.end_turn(st, "已为您查询", "ORDER_STATUS")
    assert st.context_tokens <= 60 + 40, st.context_tokens
    assert st.dropped_turns > 0, "超预算必须丢弃最早轮次并留痕"
    assert len(st.history) <= 3 * 2


def test_focus_list_is_bounded():
    dm = make()
    st = SessionState(session_id="s", slots=DialogueSlots())
    for i in range(9):
        dm.begin_turn(st, f"查订单 ORD-2026-00020{i}")
    assert len(st.slots.focus_order_ids) <= 5


def test_token_estimate_grows_with_length():
    assert estimate_tokens("你好") < estimate_tokens("你好，我想查询订单物流与退款进度")
    assert estimate_tokens("") >= 1


def test_normalize_fullwidth_and_whitespace():
    dm = make()
    st = SessionState(session_id="s")
    rewritten, _ = dm.begin_turn(st, "  订单 ＯＲＤ-2026-000101   到哪  ")
    assert "ORD-2026-000101" in rewritten
    assert "  " not in rewritten
