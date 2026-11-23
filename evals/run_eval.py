"""评测运行器：`python -m evals.run_eval`

产出：
  evals/results.jsonl            每条用例的判分明细
  evals/metrics.json             指标汇总（含分母口径）
  evals/human_sampling_5pct.csv  人工抽检样本（分层抽样，机判结果已填，人工列待填）
  reports/评测结果.md             带真实数字的评测报告
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.config import REPO_ROOT, reset_settings
from src.shop.db import content_hash

from evals.judge import CaseResult, Judge, load_golden
from evals.metrics import summarize

EVALS = REPO_ROOT / "evals"
REPORTS = REPO_ROOT / "reports"
TIERS = ("T1_retrieval", "T2_tool", "T3_mixed", "T4_high_risk")


def measure_hit_at_5(agent, cases: list[dict], k: int = 5) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in cases:
        if case["tier"] != "T1_retrieval" or not case.get("exp_evidence"):
            continue
        out[case["tier"]][1] += 1
        hits = agent.kb.search(case["turns"][-1], top_k=k, use_cache=False)
        ids = {h.entry.entry_id for h in hits}
        if any(e in ids for e in case["exp_evidence"]):
            out[case["tier"]][0] += 1
    return {t: (v[0], v[1]) for t, v in out.items()}


def write_human_sampling(results: list[CaseResult], inputs: dict[str, str], pct: float, seed: int,
                         path: Path) -> tuple[int, int]:
    """分层抽样（按 tier 分配名额，四舍五入后保证每层至少 1 条），机判列填好，人工列留空。"""
    by_tier: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_tier[r.tier].append(r)
    total = len(results)
    target = max(1, math.ceil(total * pct))
    rng = random.Random(seed)
    picked: list[CaseResult] = []
    for tier in TIERS:
        pool = by_tier.get(tier, [])
        if not pool:
            continue
        quota = max(1, round(target * len(pool) / total))
        picked.extend(rng.sample(pool, min(quota, len(pool))))
    picked.sort(key=lambda r: r.case_id)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "tier", "user_input", "agent_reply", "machine_verdict",
                    "machine_failed_checks", "human_verdict", "human_note", "annotator", "annotated_at"])
        for r in picked:
            failed = ";".join(c.name for c in r.checks if not c.ok)
            w.writerow([r.case_id, r.tier, inputs.get(r.case_id, ""), r.reply,
                        "pass" if r.passed else "fail", failed, "", "", "", ""])
    return len(picked), total


def render_report(results: list[CaseResult], metrics: dict, meta: dict) -> str:
    lines = [
        "# 评测结果（离线、合成店铺、公开语料口径）",
        "",
        f"- 运行时间：{meta['run_at']}",
        f"- 生成器 provider：`{meta['provider']}`（{'确定性模板，零外部调用' if meta['provider'] == 'template' else '外部模型，见未验证清单'}）",
        f"- 检索后端：`{meta['kb_backend']}` — {meta['kb_note']}",
        f"- 门禁规则版本：`{meta['ruleset_version']}`；合成店铺数据指纹：`{meta['shop_hash'][:16]}…`",
        f"- 用例总数：{metrics['total_cases']}（四档：T1 纯检索 / T2 工具调用 / T3 混合 / T4 高危拒答）",
        f"- 人工抽检样本：{meta['sample_size']}/{meta['sample_total']} 条（分层抽样，种子 {meta['seed']}）→ "
        f"`evals/human_sampling_5pct.csv`；**kappa 需人工填完 human_verdict 列后由 `python -m evals.kappa` 计算**",
        "",
        "> 口径提醒：以下全部数字来自**离线评测 + 合成单店数据**，没有真实店铺流量；"
        "本表不产出「拦截率」类指标，也不使用「沉默即视为解决」口径。",
        "",
        "## 一、逐档结果",
        "",
        "| 档位 | 用例数 | 判分通过 | 通过率 | 意图 Top-1 | 延迟 p50/p95/max (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for tier, m in metrics["per_tier"].items():
        p50, p95, mx = m["latency_ms_p50_p95_max"]
        lines.append(f"| {tier} | {m['cases']} | {m['passed']} | {m['pass_rate']} | "
                     f"{m['intent_top1']} | {p50}/{p95}/{mx} |")
    lines += [
        "",
        "## 二、指标（含分母定义）",
        "",
        f"- 意图 Top-1（总体）：**{metrics['intent_top1_overall']}**",
        f"- Hit@5（纯检索档）：{metrics.get('hit_at_5_overall', 'n/a')} — {metrics.get('hit_at_5_definition', '')}",
        f"- 工具调用成功率（含查无记录）：**{metrics['tool_call_success']}** — {metrics['tool_call_success_definition']}",
        f"- 其中真正查到数据（ok）：{metrics['tool_call_answered_rate']}；技术失败 "
        f"{metrics['tool_call_failures']}（超时/异常/入参非法）；完整状态分布 {metrics['tool_status_breakdown']}",
        f"- Verified resolution：**{metrics['verified_resolution']}** — {metrics['verified_resolution_definition']}",
        f"- Correct handoff：**{metrics['correct_handoff']}**（漏转 {metrics['handoff_missed']}，"
        f"多余转 {metrics['handoff_unnecessary']}）— {metrics['correct_handoff_definition']}",
        f"- **错承诺条数（红线，期望恒为 0）：{metrics['commitment_violations']}** — {metrics['commitment_violation_note']}",
        f"- Fail-closed 触发用例：{metrics['fail_closed_cases']}；降级用例：{metrics['degraded_cases']}",
        f"- 单轮延迟（实测，{meta['provider']} 模式）：p50={metrics['latency_ms']['p50']}ms "
        f"p95={metrics['latency_ms']['p95']}ms max={metrics['latency_ms']['max']}ms，n={metrics['latency_ms']['n']}",
        f"- 每用例 token（估算口径）：{metrics['tokens_per_case_estimate']}",
        f"- 置信度分布：{metrics['confidence_distribution']}",
        "",
        "## 三、失败明细",
        "",
    ]
    failed = [r for r in results if not r.passed]
    if not failed:
        lines.append("（本轮无失败用例）")
    for r in failed:
        bad = [c for c in r.checks if not c.ok]
        lines.append(f"- `{r.case_id}`（{r.tier}，意图判为 {r.intent_actual}，期望 {'/'.join(r.intent_expected)}）")
        for c in bad:
            lines.append(f"  - 检查项 `{c.name}` 未通过；{c.detail}")
        lines.append(f"  - 回复：{r.reply[:180]}")
    lines += [
        "",
        "## 四、明确未覆盖 / 未验证",
        "",
        "- **真实流量指标**：无。数据来自合成单店（`data/synthetic_shop`），延迟与通过率不代表任何线上表现。",
        "- **JDDC 原始语料上的评测**：未执行。语料获取受闸门 R1 阻断（见 `reports/P0-语料闸门.md`），"
        "本评测集是自标 golden set，不是 JDDC 真值。",
        "- **外部模型（ollama/openai）链路**：本轮用 `template` provider，未验证；"
        "承诺白名单的第二道锁由不受控输出用例覆盖，但真实模型的拒答率未测。",
        "- **BGE-M3 向量检索**：未验证（依赖未安装，已按设计降级为 BM25 并留痕）。",
        "- **LLM judge 表述质量维度**：本轮未启用（离线模板模式下无意义）；kappa 见人工抽检文件。",
        "- **K 值行业区间**（verified resolution ≥85%、correct handoff ≥98%）是从业者模板，非行业标准。",
        "",
        "## 五、复跑命令",
        "",
        "```bash",
        "python -m src.shop.seed            # 重建合成店铺库（可选）",
        "python -m evals.run_eval           # 跑全量评测并生成本报告",
        "python -m evals.kappa --selftest   # 校验 kappa 计算器",
        "```",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跑 golden set 评测")
    ap.add_argument("--golden", default=str(EVALS / "golden_set_v1.jsonl"))
    ap.add_argument("--provider", default=None, help="覆盖 LLM_PROVIDER（默认强制 template 以保证可复现）")
    ap.add_argument("--results-out", default=str(EVALS / "results.jsonl"))
    ap.add_argument("--metrics-out", default=str(EVALS / "metrics.json"))
    ap.add_argument("--sampling-out", default=str(EVALS / "human_sampling_5pct.csv"))
    ap.add_argument("--report", default=str(REPORTS / "评测结果.md"))
    ap.add_argument("--sample-pct", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    os.environ["LLM_PROVIDER"] = args.provider or "template"
    os.environ["AUDIT_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="eval-audit-"), "audit.db")
    cfg = reset_settings()

    from src.agent import Agent

    cases = load_golden(args.golden)
    if args.limit:
        cases = cases[:args.limit]
    agent = Agent()
    judge = Judge(agent, args.golden)
    results = [judge.run_case(c) for c in cases]
    hit5 = measure_hit_at_5(agent, cases)
    metrics = summarize(results, hit5)

    inputs = {c["id"]: " → ".join(c["turns"]) for c in cases}
    sample_n, sample_total = write_human_sampling(results, inputs, args.sample_pct, args.seed,
                                                  Path(args.sampling_out))
    meta = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "provider": cfg.llm_provider,
            "kb_backend": agent.kb.backend, "kb_note": agent.kb.backend_note,
            "ruleset_version": agent.rules.version, "shop_hash": content_hash(agent.db_path),
            "sample_size": sample_n, "sample_total": sample_total, "seed": args.seed}

    Path(args.results_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.results_out).write_text("\n".join(json.dumps(r.to_json(), ensure_ascii=False) for r in results) + "\n",
                                      encoding="utf-8")
    Path(args.metrics_out).write_text(json.dumps({"meta": meta, "metrics": metrics}, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(render_report(results, metrics, meta), encoding="utf-8")

    passed = sum(1 for r in results if r.passed)
    print(f"[eval] 用例 {len(results)}，判分通过 {passed}，错承诺条数 {metrics['commitment_violations']}")
    print(f"[eval] 意图 Top-1 {metrics['intent_top1_overall']}；工具成功率 {metrics['tool_call_success']}")
    print(f"[eval] correct handoff {metrics['correct_handoff']}（漏转 {metrics['handoff_missed']}）")
    print(f"[eval] Hit@5 {metrics.get('hit_at_5_overall')}")
    print(f"[eval] 抽检样本 {sample_n}/{sample_total} → {args.sampling_out}")
    print(f"[eval] 报告 → {args.report}")
    agent.close()
    return 0 if metrics["commitment_violations"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
