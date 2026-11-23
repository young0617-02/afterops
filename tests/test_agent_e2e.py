"""端到端与 A9：含金额/库存的回答，数字必须可溯源到 SQL 工具或条文原文。"""
from __future__ import annotations

import re

import pytest

from src.gates import NUM_CLAIM_RE
from src.schemas import GateAction

AMOUNT_CASES = [
    "订单 ORD-2026-000101 实付多少钱",
    "订单 ORD-2026-000102 这单一共付了多少",
    "订单 ORD-2026-000103 实付金额",
    "订单 ORD-2026-000105 付了多少",
    "订单 ORD-2026-000107 一共多少钱",
    "订单 ORD-2026-000109 实付多少钱",
    "订单 ORD-2026-000110 支付金额",
    "订单 ORD-2026-000111 实付多少",
    "订单 ORD-2026-000112 多少钱",
    "订单 ORD-2026-000113 实付金额是多少",
    "订单 ORD-2026-000114 付了多少",
    "订单 ORD-2026-000104 实付多少",
    "SKU-NCM-001 多少钱",
    "降噪耳机价格",
    "移动电源多少钱",
    "机械键盘多少钱",
    "SKU-FAN-Z9 华南还有几件",
    "SKU-SSD-1T 华东库存几件",
    "SKU-WCH-X50 有货吗",
    "SKU-CBL-100 库存多少",
]


@pytest.mark.acceptance
@pytest.mark.parametrize("utterance", AMOUNT_CASES, ids=[f"A9-{i:02d}" for i in range(1, len(AMOUNT_CASES) + 1)])
def test_A9_every_numeric_claim_is_traceable(agent, utterance):
    resp = agent.handle(utterance, session_id="a9-" + utterance[:6])
    tool_numbers = {n for t in resp.tool_calls for n in t.numbers}
    kb_numbers: set[str] = set()
    for cite in resp.citations:
        m = re.match(r"\[(?P<eid>[A-Z0-9\-]+)@(?P<ver>\w+)\]", cite or "")
        if not m:
            continue
        entry = agent.kb.entries.get((m.group("eid"), m.group("ver")))
        if entry:
            kb_numbers |= set(re.findall(r"\d+(?:\.\d+)?", entry.body))

    claims = [m.group(1) for m in NUM_CLAIM_RE.finditer(resp.reply)]
    assert claims, f"该用例应产出含数字的回答：{utterance} -> {resp.reply}"
    untraced = [c for c in claims if c not in tool_numbers and c not in kb_numbers]
    assert untraced == [], f"数字无法溯源（工具/条文之外）：{untraced} <- {resp.reply}"


@pytest.mark.acceptance
def test_A9_amounts_equal_sql_view_output(agent, shop_db):
    """把回答里的金额与 SQLite 视图逐一对齐（证明模型没有做任何计算）。"""
    from src.shop.db import connect

    with connect(shop_db) as conn:
        rows = {r["order_id"]: r["payable_cents"] for r in conn.execute(
            "SELECT order_id, payable_cents FROM v_order_settlement")}
    for oid, cents in list(rows.items())[:6]:
        resp = agent.handle(f"订单 {oid} 实付多少钱", session_id="a9-sql-" + oid[-3:])
        assert f"{cents / 100:.2f}" in resp.reply, f"{oid}: 期望 {cents} 分，回复 {resp.reply}"
        assert f"{cents} 分" in resp.reply, f"{oid}: 回复里的分值与 SQL 视图不一致：{resp.reply}"


def test_multi_turn_coreference_e2e(agent):
    agent.handle("订单 ORD-2026-000102 物流到哪了", session_id="mt")
    resp = agent.handle("这个订单能退吗", session_id="mt")
    assert resp.intent.code == "RETURN_POLICY"
    assert any(("POLICY-RETURN" in c or "POLICY-EXCHANGE" in c) for c in resp.citations), resp.citations


def test_order_lookup_then_refund_progress_keeps_slot(agent):
    agent.handle("SKU-NCM-001 多少钱", session_id="mt2")
    resp = agent.handle("订单 ORD-2026-000103 退款进度", session_id="mt2")
    assert "1289.00" in resp.reply
    assert resp.tool_calls and resp.tool_calls[0].tool == "query_order"


def test_urgent_complaint_e2e_routes_to_complaint_queue(agent):
    resp = agent.handle("耳机充电时冒烟了，我要投诉，订单 ORD-2026-000101", session_id="urgent")
    assert resp.handoff.channel == "complaint"
    assert resp.handoff.auto_transferred
    assert GateAction.FORCE_HANDOFF in resp.gate_actions


def test_reply_is_never_empty_and_always_carries_handoff(agent):
    for t in ["你好", "?", "订单", "随便说点什么吧", "……"]:
        resp = agent.handle(t, session_id="never-empty-" + t)
        assert resp.reply.strip()
        assert "人工" in resp.reply, f"{t} -> {resp.reply}"


def test_latency_is_recorded_for_each_turn(agent):
    resps = [agent.handle(f"订单 ORD-2026-00010{i} 实付多少钱", session_id=f"perf-{i}") for i in range(1, 6)]
    assert all(r.latency_ms >= 0 for r in resps)
    assert max(r.latency_ms for r in resps) < 6000, "本地端到端阈值 6s（设计值，实测见 reports）"


def test_audit_rows_match_turn_count(agent):
    n = 7
    for i in range(n):
        agent.handle(f"订单 ORD-2026-000101 实付多少钱 变体{i}", session_id="count")
    assert len(agent.audit.rows("count")) == n


@pytest.mark.acceptance
def test_replay_does_not_recompute(agent_factory):
    a1, a2 = agent_factory(), agent_factory()
    r1 = a1.handle("退款多久到账", session_id="replay")
    r2 = a2.handle("退款多久到账", session_id="replay")
    assert r1.reply == r2.reply and r2.replayed
    calls_before = len(a2.tools.calls)
    a2.handle("退款多久到账", session_id="replay")
    assert len(a2.tools.calls) == calls_before, "重放不应再打工具"


def test_tool_calls_are_logged_with_status(agent):
    resp = agent.handle("ORD-2026-000111 物流轨迹", session_id="log")
    assert {t.tool for t in resp.tool_calls} >= {"query_logistics"}
    assert all(t.status.value == "ok" for t in resp.tool_calls)
    assert all(t.result_ref for t in resp.tool_calls)


def test_kb_backend_reported_honestly(agent):
    """默认必须是可复现的离线检索；请求了向量后端则必须记录降级原因。"""
    st = agent.kb.stats()
    assert st["backend"] in ("bm25", "vector")
    assert st["backend_note"]
