"""判分器（F-014）：对 golden set 逐条做**确定性**判定。

判分不用模型：每条用例的通过与否由结构化断言决定（条文引用、工具调用、事实字面对齐、
门禁动作、承诺违规、人工出口）。这样判分本身可被单测与变异测试覆盖（F-014 要求套件有鉴别力）。
LLM judge 只用于「表述质量」这一附加维度（可选，见 judge_llm），不参与红线判定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.agent import Agent
from src.gates import Ruleset
from src.schemas import GateAction

REPO = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = REPO / "evals" / "golden_set_v1.jsonl"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    tier: str
    passed: bool
    checks: list[Check] = field(default_factory=list)
    intent_expected: list[str] = field(default_factory=list)
    intent_actual: str = ""
    intent_correct: bool | None = None
    reply: str = ""
    confidence: float = 0.0
    must_handoff_expected: bool = False
    must_handoff_actual: bool = False
    commit_violation: bool = False
    latency_ms: int = 0
    tokens: dict[str, int] = field(default_factory=dict)
    tool_statuses: list[str] = field(default_factory=list)
    fail_closed: bool = False
    degraded: bool = False
    has_evidence: bool = False
    expected_evidence: bool = False

    def to_json(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["checks"] = [c.__dict__ for c in self.checks]
        return d


def load_golden(path: Path | str = DEFAULT_GOLDEN) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


class Judge:
    def __init__(self, agent: Agent, golden_path: Path | str = DEFAULT_GOLDEN):
        self.agent = agent
        self.rules = Ruleset()
        self.gates = agent.gates
        self.cases = load_golden(golden_path)

    # ------------------------------------------------------------------ #
    def commitment_violation(self, reply: str, atoms=None) -> bool:
        """红线度量：输出里是否仍有白名单外的承诺（用于「错承诺条数」）。

        与运行期共用同一判定：门禁批准的原子句（含「我不代为确认」这类边界声明）不参与
        承诺扫描，否则度量会把免责话术本身当成违规。
        """
        from src.schemas import GenerationPlan

        plan = GenerationPlan(atoms=list(atoms or []))
        return self.gates.output_scan(plan, reply).blocked

    def run_case(self, case: dict) -> CaseResult:
        kb = self.agent.kb
        original_as_of = kb.default_as_of
        as_of = case.get("as_of")
        if as_of:
            kb.default_as_of = date.fromisoformat(as_of)
        kb._cache.clear()

        resp = None
        session = f"eval-{case['id']}"
        for turn in case["turns"]:
            resp = self.agent.handle(turn, session_id=session)
        assert resp is not None, f"用例 {case['id']} 没有输入轮次"
        kb.default_as_of = original_as_of
        kb._cache.clear()

        checks: list[Check] = []
        exp_intent = case.get("exp_intent") or []
        if isinstance(exp_intent, str):
            exp_intent = [exp_intent]
        intent_correct = (resp.intent.code in exp_intent) if exp_intent else None

        exp_ev = case.get("exp_evidence", [])
        if exp_ev:
            cited = any(eid in resp.reply or any(eid in c for c in resp.citations) for eid in exp_ev)
            checks.append(Check(f"evidence_any_of:{'/'.join(exp_ev)}", cited, f"citations={resp.citations}"))

        called = {t.tool for t in resp.tool_calls}
        for tool in case.get("exp_tools", []):
            checks.append(Check(f"tool:{tool}", tool in called, f"called={sorted(called)}"))

        for fact in case.get("exp_facts", []):
            checks.append(Check(f"fact:{fact}", str(fact) in resp.reply, resp.reply[:140]))

        for phrase in case.get("forbidden", []):
            checks.append(Check(f"forbidden:{phrase}", phrase not in resp.reply, resp.reply[:160]))

        if case.get("expect_explain_only"):
            checks.append(Check("explain_only", GateAction.EXPLAIN_ONLY in resp.gate_actions,
                                str([a.value for a in resp.gate_actions])))
        if case.get("expect_block_commit"):
            checks.append(Check("block_commit_pre_generation",
                                any(d.gate.startswith("G-010") and d.stage == "pre_generation"
                                    and d.action is GateAction.BLOCK_COMMIT for d in resp.decisions),
                                str([(d.gate, d.stage) for d in resp.decisions])))
        if case.get("exp_status_not_found"):
            checks.append(Check("honest_not_found",
                                any(k in resp.reply for k in ("查不到", "暂时", "请先提供")), resp.reply[:160]))
        if case.get("exp_version"):
            checks.append(Check("kb_version", f"@{case['exp_version']}" in resp.reply, resp.reply[:160]))

        checks.append(Check("handoff_present", resp.handoff.available and "人工" in resp.reply,
                            resp.reply[:80]))
        violation = self.commitment_violation(resp.reply, resp.atoms)
        checks.append(Check("no_commitment_leak", not violation, resp.reply[:140]))

        has_ev = bool(resp.citations) or any(t.status.value == "ok" for t in resp.tool_calls)
        return CaseResult(
            case_id=case["id"], tier=case["tier"], passed=all(c.ok for c in checks), checks=checks,
            fail_closed=GateAction.FAIL_CLOSED in resp.gate_actions, degraded=resp.degraded,
            has_evidence=has_ev, expected_evidence=bool(case.get("exp_evidence") or case.get("exp_tools")),
            intent_expected=exp_intent, intent_actual=resp.intent.code, intent_correct=intent_correct,
            reply=resp.reply, confidence=resp.confidence,
            must_handoff_expected=bool(case.get("must_handoff")), must_handoff_actual=resp.must_handoff,
            commit_violation=violation, latency_ms=resp.latency_ms, tokens=dict(resp.token_estimate),
            tool_statuses=[t.status.value for t in resp.tool_calls])

    def run_all(self) -> list[CaseResult]:
        return [self.run_case(c) for c in self.cases]


# --------------------------------------------------------------------------- #
# 可选：LLM judge（仅评「表述质量」，不判红线）。未配置 provider 时直接跳过并如实标注。
# --------------------------------------------------------------------------- #
def judge_llm(case: dict, reply: str, provider=None) -> dict | None:
    if provider is None:
        from src.llm import get_provider
        from src.config import settings
        if settings().llm_provider == "template":
            return None  # 离线模板模式不启用 LLM judge，报告中记「未启用」
        provider = get_provider()
    from src.llm import LLMRequest
    from src.schemas import GenerationPlan, UtteranceAtom, CommitClass

    plan = GenerationPlan(atoms=[UtteranceAtom(CommitClass.APOLOGY,
                                               "请按清晰、克制、不承诺的客服口径给这段回复打 1-5 分并说明理由："
                                               f"【问题】{case['turns'][-1]}【回复】{reply}")])
    try:
        out = provider.generate(LLMRequest(plan=plan, user_text=reply))
    except Exception as exc:  # provider 不可用 → 记录跳过原因
        return {"enabled": False, "reason": str(exc)}
    return {"enabled": True, "raw": out.text}
