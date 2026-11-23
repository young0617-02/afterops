"""分段延迟实测（支撑 docs/口径声明.md 的三态口径）。

只测**本机可复现的离线链路**：意图判定 / 工具调用 / 检索 / 门禁 / 模板生成 / 审计落库，
端到端 p50/p95/max 逐段给出。云模型链路（ollama/openai）需要你自己起服务或给密钥，
本脚本默认不测，报告里标「未测」。流式首包也不测（本项目默认不流式）。
"""
from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path

import sys
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def probe(n_rounds: int, db_dir: str) -> dict:
    os.environ["AUDIT_DB_PATH"] = str(Path(db_dir) / "perf-audit.db")
    os.environ["LLM_PROVIDER"] = "template"
    from src.agent import Agent

    agent = Agent()
    turns = ["订单 ORD-2026-000101 实付多少钱", "退款多久到账", "SKU-NCM-001 多少钱",
             "ORD-2026-000111 物流到哪了", "你能保证全额退款吗", "我要投诉你们",
             "SKU-FAN-Z9 华南有货吗", "怎么申请换货", "价保规则是什么", "转人工"]
    stages: dict[str, list[float]] = {k: [] for k in ("intent", "tool", "retrieval", "gate", "total", "audit")}

    for i in range(n_rounds):
        text = turns[i % len(turns)]
        slots = _slots(agent, text)
        ta = time.perf_counter()
        intent = agent.router.classify(text, slots)
        stages["intent"].append((time.perf_counter() - ta) * 1000)

        tb = time.perf_counter()
        tools = {}
        for name, args in agent._plan_tools(intent.code, slots, text):
            tools[name] = agent.tools.call(name, args)
        stages["tool"].append((time.perf_counter() - tb) * 1000)

        tc = time.perf_counter()
        topics = _topics(intent.code)
        hits = agent.kb.current_hits_for(topics, text) if topics else []
        hits += agent.kb.search(text)
        stages["retrieval"].append((time.perf_counter() - tc) * 1000)

        td = time.perf_counter()
        plan, decisions = agent.gates.pre_generation(intent, text, hits, tools, slots,
                                                    agent.gates.urgency_hits(text))
        stages["gate"].append((time.perf_counter() - td) * 1000)

        te = time.perf_counter()
        resp = agent.handle(text, session_id="perf")
        stages["total"].append((time.perf_counter() - te) * 1000)
        stages["audit"].append(resp.latency_ms)
    return {k: _stats(v) for k, v in stages.items()}


def _slots(agent, text):
    from src.dialogue import DialogueManager
    from src.schemas import DialogueSlots
    dm = DialogueManager()
    slots = DialogueSlots()
    dm.extract_slots(text, slots)
    return slots


def _topics(code):
    from src.gates import POLICY_TOPIC_BY_INTENT
    return POLICY_TOPIC_BY_INTENT.get(code, ())


def _stats(vals: list[float]) -> dict:
    vs = sorted(vals)
    return {"n": len(vs), "p50_ms": round(vs[len(vs) // 2], 2),
            "p95_ms": round(vs[min(len(vs) - 1, int(0.95 * len(vs)))], 2),
            "max_ms": round(vs[-1], 2), "mean_ms": round(sum(vs) / len(vs), 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description="分段延迟实测")
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--db-dir", default=tempfile.mkdtemp(prefix="perf-"))
    ap.add_argument("--out", default=str(REPO / "reports" / "延迟实测.json"))
    args = ap.parse_args()

    res = probe(args.rounds, args.db_dir)
    summary = {"rounds": args.rounds, "provider": "template（离线确定性，不含外部模型调用）",
               "stages": res, "note": "端到端为单轮完整链路（含审计落库）；分阶段之和可能大于端到端，"
                                      "因为分阶段计时是另一次独立调用。"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(__import__("json").dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in res.items():
        print(f"{k:<10} p50={v['p50_ms']:>7}ms p95={v['p95_ms']:>7}ms max={v['max_ms']:>7}ms")
    print(f"[perf] → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
