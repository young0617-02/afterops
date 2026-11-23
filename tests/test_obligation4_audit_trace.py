"""义务④（F-011）：留痕可溯 + 幂等 —— 对应验收 A11。

重放同一请求 3 次：结论一致（走的是同一条审计记录，不是「再判一次」），
并且能从 request_key 反查到规则版本、条文版本与工具证据。
"""
from __future__ import annotations

import json

import pytest


@pytest.mark.obligation
@pytest.mark.acceptance
def test_A11_replay_three_times_gives_same_conclusion(agent_factory):
    text = "订单 ORD-2026-000101 实付多少钱"
    a1, a2, a3 = agent_factory(), agent_factory(), agent_factory()
    first = a1.handle(text, session_id="a11")
    assert first.replayed is False

    # 新实例 = 新进程语义（同一审计库文件）
    second = a2.handle(text, session_id="a11")
    third = a3.handle(text, session_id="a11")
    assert second.replayed and third.replayed
    assert second.reply == first.reply == third.reply
    assert [d.gate for d in second.decisions] == [d.gate for d in first.decisions]
    assert second.confidence == first.confidence

    rows = a3.audit.rows("a11")
    assert len(rows) == 1, f"重放不应新增审计行，实际 {len(rows)} 行"
    assert rows[0]["replay_count"] == 3, rows[0]["replay_count"]


@pytest.mark.obligation
@pytest.mark.acceptance
def test_A11_trace_reverse_lookup_rule_version_and_evidence(agent):
    text = "订单 ORD-2026-000103 退款进度怎样"
    resp = agent.handle(text, session_id="a11b")
    trace = agent.audit.trace(resp.request_key)

    assert trace["found"]
    assert trace["ruleset_version"] == agent.rules.version
    assert trace["intent_code"] == resp.intent.code
    assert trace["reply"] == resp.reply

    kb_versions = trace["kb_versions"]
    assert kb_versions and all({"entry_id", "version", "valid_from", "valid_until"} <= set(v)
                                for v in kb_versions), "承诺性输出必须可反查规则版本"
    tool_calls = trace["tool_calls"]
    assert tool_calls and tool_calls[0]["status"] == "ok"
    assert any("query_order" == t["tool"] for t in tool_calls)

    evidence = trace["evidence"]
    assert any(e["kind"] == "tool" for e in evidence)
    assert any(e["kind"] == "kb" and e.get("source") for e in evidence)


@pytest.mark.obligation
def test_audit_hash_chain_detects_tampering(agent):
    for t in ["订单 ORD-2026-000101 实付多少钱", "退款多久到账", "转人工"]:
        agent.handle(t, session_id="a11c")
    ok, where = agent.audit.verify_chain()
    assert (ok, where) == (True, "ok")

    agent.audit._conn.execute("UPDATE audit_turns SET reply = '被改写' WHERE seq = 2")  # 人为篡改
    agent.audit._conn.commit()
    ok, where = agent.audit.verify_chain()
    assert ok is False and where == 2, f"篡改未被发现：{ok} {where}"


@pytest.mark.obligation
def test_gate_decisions_are_serialised_in_audit(agent):
    resp = agent.handle("你能保证全额退款吗", session_id="a11d")
    row = agent.audit.trace(resp.request_key)
    gates = [d["gate"] for d in row["decisions"]]
    assert any(g.startswith("G-010") for g in gates)
    assert any(g.startswith("G-008") for g in gates)
    assert "block_commit" in row["gate_actions"]
    assert json.loads(json.dumps(row["handoff"]))["available"] is True


@pytest.mark.obligation
def test_idempotency_key_depends_on_session_and_text(agent):
    from src.audit import request_key_of

    a = request_key_of("s1", "订单 ORD-2026-000101 实付多少钱")
    b = request_key_of("s1", "订单 ORD-2026-000101 实付多少钱")
    c = request_key_of("s2", "订单 ORD-2026-000101 实付多少钱")
    d = request_key_of("s1", "订单 ORD-2026-000101 实付多少钱", "req-001")
    assert a == b
    assert a != c
    assert a != d
