"""F-012：置信度门禁——低置信不硬答，缺依据不编造规则。"""
from __future__ import annotations

import pytest

from src.schemas import CommitClass, GateAction


def test_low_confidence_triggers_soft_answer(agent):
    resp = agent.handle("嗯？那个什么来着", session_id="f12-low")
    assert resp.confidence < agent.gates.confidence_threshold
    assert GateAction.SOFT_ANSWER in resp.gate_actions
    assert any(a.kind is CommitClass.LIMITATION for a in resp.atoms), "低置信必须声明能力边界"
    assert "人工" in resp.reply


def test_confident_answer_does_not_trigger_soft(agent):
    resp = agent.handle("订单 ORD-2026-000101 实付多少钱", session_id="f12-high")
    assert resp.confidence > agent.gates.confidence_threshold
    assert GateAction.SOFT_ANSWER not in gate_actions_of(resp)


def gate_actions_of(resp):
    return resp.gate_actions


def test_threshold_is_configurable(env):
    from src.agent import Agent
    from src.gates import GateEngine

    assert GateEngine(confidence_threshold=0.99).confidence_threshold == 0.99
    a = Agent(db_path=env["shop"])
    assert a.gates.confidence_threshold == pytest.approx(0.55)
    a.close()


def test_fail_closed_when_high_risk_has_no_current_policy(agent):
    """现行条文被下线后，高危问题不能靠模型编规则：只转人工。"""
    agent.kb.set_entry_validity("POLICY-COMPENSATION", "v1", "2026-01-01")
    resp = agent.handle("快递延误，你给我赔偿确认一下，订单 ORD-2026-000104", session_id="f12-fail")
    assert GateAction.FAIL_CLOSED in resp.gate_actions
    assert resp.answerable is False
    assert "规则依据" not in resp.reply, "无现行条文时不得输出规则内容"
    assert "人工" in resp.reply


def test_degraded_tool_never_yields_numbers(env, monkeypatch):
    """工具超时降级：回答里不出现任何金额/单号，并声明查不到 + 转人工。"""
    import time as _t

    from src.agent import Agent

    a = Agent(db_path=env["shop"])
    a.tools.timeout_ms = 50
    original = a.tools._registry["query_order"]

    def slow(args):
        _t.sleep(0.2)
        return original[1](args)

    a.tools._registry["query_order"] = (original[0], slow, original[2])
    try:
        resp = a.handle("订单 ORD-2026-000101 实付多少钱", session_id="f12-degraded")
        assert resp.degraded is True
        assert "987" not in resp.reply and "元" not in resp.reply
        assert "查不到" in resp.reply or "暂时" in resp.reply
        assert "人工" in resp.reply
    finally:
        a.close()


def test_confidence_weights_are_documented_and_bounded(agent):
    c = agent.gates.confidence(agent.router.classify("转人工"), [], {}, policy_needed=False)
    assert 0.0 <= c <= 1.0
