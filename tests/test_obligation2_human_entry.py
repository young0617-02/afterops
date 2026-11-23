"""义务②（F-009）：人工入口不得隐藏 —— 对应验收 A3（★一票否决）。

A3 的判据是「遍历 50 轮对话，0 轮缺失人工出口」，本文件把 50 轮做成可跑的遍历，
并单独覆盖紧急场景自动转接与「人工入口不构成障碍」两条。
"""
from __future__ import annotations

import pytest

from src.schemas import GateAction

SESSION_SCRIPT = [
    "订单 ORD-2026-000101 实付多少钱",
    "这个订单能退吗",
    "退款多久到账",
    "SKU-NCM-001 华东有货吗",
    "降噪耳机多少钱",
    "ORD-2026-000111 物流到哪了",
    "我要改地址到安徽",
    "降价了，能价保吗",
    "怎么申请换货",
    "随便逛逛，有什么推荐",
]


@pytest.mark.obligation
@pytest.mark.acceptance
def test_A3_handoff_present_in_all_50_turns(agent_factory):
    """10 个话题 × 5 轮 = 50 轮遍历：每轮都必须带人工出口。"""
    a = agent_factory()
    missing, urgent_seen = [], 0
    turns = 0
    for round_no in range(5):
        for i, text in enumerate(SESSION_SCRIPT):
            resp = a.handle(f"{text}（第{round_no + 1}轮）", session_id=f"a3-{i}")
            turns += 1
            if not resp.handoff.available or "人工" not in resp.reply:
                missing.append((round_no, text, resp.reply))
            urgent_seen += 1 if resp.handoff.urgent else 0
    assert turns == 50, f"遍历轮数应为 50，实际 {turns}"
    assert missing == [], f"{len(missing)}/50 轮缺失人工出口：{missing[:2]}"
    assert a.audit.rows(limit=200) and len(a.audit.rows(limit=200)) == 50


@pytest.mark.obligation
@pytest.mark.acceptance
@pytest.mark.parametrize("phrase", ["我要投诉你们", "再不理我就打 12315 举报", "耳机充电时冒烟了要起火",
                                    "这属于虚假宣传，我要曝光", "快递员态度太差，找律师"],
                         ids=["A3-urgent-1", "A3-urgent-2", "A3-urgent-3", "A3-urgent-4", "A3-urgent-5"])
def test_A3_urgent_auto_transfer(agent, phrase):
    resp = agent.handle(phrase, session_id="a3-urgent")
    assert resp.handoff.urgent, f"紧急场景未识别：{phrase}"
    assert resp.handoff.auto_transferred, f"紧急场景未自动转接：{phrase}"
    assert GateAction.FORCE_HANDOFF in resp.gate_actions


@pytest.mark.obligation
def test_A3_human_request_is_granted_not_blocked(agent):
    resp = agent.handle("转人工", session_id="a3-h" + "1")
    assert resp.intent.code == "HUMAN_REQUEST"
    assert GateAction.FORCE_HANDOFF in resp.gate_actions
    assert resp.handoff.available and resp.handoff.ticket_ref
    for obstacle in ("无法转接", "不支持人工", "请先完成", "只有付费用户"):
        assert obstacle not in resp.reply


@pytest.mark.obligation
def test_A3_handoff_is_actionable(agent):
    """入口不得设计成障碍：给得出队列、工单号和明确动作。"""
    resp = agent.handle("订单 ORD-2026-000103 退款进度", session_id="a3-h2")
    assert resp.handoff.channel in ("refund", "logistics", "complaint", "presale")
    assert resp.handoff.ticket_ref.startswith("TK-")
    assert "转人工" in resp.handoff.prompt
    assert "队列" in resp.handoff.queue_hint


@pytest.mark.obligation
def test_A3_post_check_repairs_missing_handoff(env):
    """后置断言：生成器给出没有人工出口的文本时，必须补齐（而不是原样发出去）。"""
    from src.agent import Agent
    from src.llm import LLMResult

    class NoHandoffProvider:
        name = "no-handoff"

        def generate(self, req, timeout_ms: int = 0):
            return LLMResult(text="这一单的规则我已经说明完毕。", provider=self.name,
                             prompt_tokens=10, completion_tokens=10)

    a = Agent(db_path=env["shop"], provider=NoHandoffProvider())
    try:
        resp = a.handle("订单 ORD-2026-000101 实付多少钱", session_id="a3-repair")
        assert "人工" in resp.reply, f"补齐失败：{resp.reply}"
        assert any(d.gate == "G-009-post-check" for d in resp.decisions), "补齐动作未留痕"
    finally:
        a.close()
