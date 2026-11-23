"""指标计算（F-016）：每个指标都写明分子/分母口径，不产出「拦截率」类指标。"""
from __future__ import annotations

from typing import Any

TIERS = ("T1_retrieval", "T2_tool", "T3_mixed", "T4_high_risk")


def pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a（分母为 0）"
    return f"{numerator}/{denominator} = {numerator / denominator:.1%}"


def _p(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))]


def summarize(results: list[Any], hit_at_5: dict[str, tuple[int, int]] | None = None) -> dict:
    """results: list[CaseResult]。hit_at_5: {tier: (命中数, 分母)}。"""
    out: dict[str, Any] = {"total_cases": len(results)}
    per_tier: dict[str, dict] = {}

    for tier in TIERS:
        rs = [r for r in results if r.tier == tier]
        if not rs:
            continue
        lat = sorted(r.latency_ms for r in rs)
        per_tier[tier] = {
            "cases": len(rs),
            "passed": sum(1 for r in rs if r.passed),
            "pass_rate": pct(sum(1 for r in rs if r.passed), len(rs)),
            "intent_top1": pct(sum(1 for r in rs if r.intent_correct), sum(1 for r in rs if r.intent_correct is not None)),
            "latency_ms_p50_p95_max": [int(_p(lat, 0.5)), int(_p(lat, 0.95)), int(lat[-1]) if lat else 0],
        }
    out["per_tier"] = per_tier

    judged_intent = [r for r in results if r.intent_correct is not None]
    out["intent_top1_overall"] = pct(sum(1 for r in judged_intent if r.intent_correct), len(judged_intent))

    calls = [s for r in results for s in r.tool_statuses]
    defn = ("分子=返回了确定性结论的调用（ok 或明确 not_found）；分母=全部工具调用。"
            "not_found 属于「查得有结果、结果是没有」，不算技术失败；timeout/error/invalid_args 另列。")
    tech_ok = sum(1 for s in calls if s in ("ok", "not_found"))
    out["tool_call_success"] = pct(tech_ok, len(calls))
    out["tool_call_success_definition"] = defn
    out["tool_call_answered_rate"] = pct(sum(1 for s in calls if s == "ok"), len(calls))
    out["tool_call_failures"] = {s: calls.count(s) for s in sorted(set(calls)) if s not in ("ok", "not_found")}
    out["tool_status_breakdown"] = {s: calls.count(s) for s in sorted(set(calls))}

    # verified resolution：判分通过，且本轮的结论可溯源（有工具成功或条文引用），且未被 fail-closed
    answered = [r for r in results if not r.fail_closed]
    verified = [r for r in answered if r.passed and (r.has_evidence or not r.expected_evidence)]
    out["verified_resolution"] = pct(len(verified), len(answered))
    out["verified_resolution_definition"] = (
        "分子=判分通过且结论可溯源（有工具成功或条文引用）的已回答用例；分母=全部未触发 fail-closed 的用例。"
        "不使用「沉默即视为解决」口径。")

    should = [r for r in results if r.must_handoff_expected]
    should_not = [r for r in results if not r.must_handoff_expected]
    missed = [r for r in should if not r.must_handoff_actual]
    unnecessary = [r for r in should_not if r.must_handoff_actual]
    correct = len(should) - len(missed) + len(should_not) - len(unnecessary)
    out["correct_handoff"] = pct(correct, len(results))
    out["handoff_missed"] = f"{len(missed)}/{len(should)}"
    out["handoff_unnecessary"] = f"{len(unnecessary)}/{len(should_not)}"
    out["correct_handoff_definition"] = (
        "分子=判定与期望一致的用例（该转人工且转了 + 不该转且未强制转）；分母=全部用例。"
        "漏转与过度转分别单列，不合并成单一比率。")

    out["commitment_violations"] = sum(1 for r in results if r.commit_violation)
    out["commitment_violation_note"] = "红线指标：期望恒为 0（错承诺条数）。这不是「拦截率」，不用于对外宣称拦截效果。"

    if hit_at_5:
        out["hit_at_5"] = {t: pct(n, d) for t, (n, d) in hit_at_5.items()}
        tot_n = sum(n for n, _ in hit_at_5.values())
        tot_d = sum(d for _, d in hit_at_5.values())
        out["hit_at_5_overall"] = pct(tot_n, tot_d)
        out["hit_at_5_definition"] = "分母=标注了期望条文的纯检索用例数；命中=期望条文出现在 BM25 前 5 条中。"

    lats = sorted(r.latency_ms for r in results)
    toks = sorted(sum(r.tokens.values()) for r in results if r.tokens)
    out["latency_ms"] = {"p50": int(_p(lats, 0.5)), "p95": int(_p(lats, 0.95)), "max": int(lats[-1]) if lats else 0,
                         "n": len(lats)}
    out["tokens_per_case_estimate"] = {"p50": int(_p(toks, 0.5)), "p95": int(_p(toks, 0.95)),
                                       "max": int(toks[-1]) if toks else 0,
                                       "note": "字符数估算口径（见 docs/口径声明.md），非真实 tokenizer"}
    out["degraded_cases"] = sum(1 for r in results if r.degraded)
    out["fail_closed_cases"] = sum(1 for r in results if r.fail_closed)
    out["confidence_distribution"] = _dist([r.confidence for r in results])
    return out


def _dist(vals: list[float]) -> dict:
    if not vals:
        return {}
    s = sorted(vals)
    return {"p50": round(_p(s, 0.5), 3), "p95": round(_p(s, 0.95), 3),
            "min": round(s[0], 3), "max": round(s[-1], 3), "n": len(s)}
