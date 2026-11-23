"""启动入口：`python -m src.cli`（交互）/ `--ask`（单轮）/ `--demo`（固定演示）/ `--audit`（查留痕）。"""
from __future__ import annotations

import argparse
import json
import sys

from src.agent import Agent
from src.shop.db import content_hash
from src.shop.seed import main as seed_main


def _print(resp, show_tools: bool = True) -> None:
    print(f"\n用户 ▶ {resp.reply[:600]}")
    print(f"   意图={resp.intent.code}({resp.intent.l1}) 置信={resp.confidence} "
          f"门禁={[a.value for a in resp.gate_actions]} 延迟={resp.latency_ms}ms")
    print(f"   人工出口={resp.handoff.channel} 紧急={resp.handoff.urgent} 工单={resp.handoff.ticket_ref} "
          f"{'⟨本轮已自动转接⟩' if resp.handoff.auto_transferred else ''}"
          f"{' ⟨重放命中，结论一致⟩' if resp.replayed else ''}")
    if resp.citations:
        print(f"   依据={resp.citations}")
    if show_tools and resp.tool_calls:
        print("   工具=" + ", ".join(f"{t.tool}[{t.status.value} {t.elapsed_ms}ms]" for t in resp.tool_calls))


DEMO_TURNS = [
    ("demo-1", "订单 ORD-2026-000101 实付多少钱？"),
    ("demo-1", "这个订单能退吗"),
    ("demo-2", "你能保证全额退款吗"),
    ("demo-2", "那我不要退款了，给我赔偿 100 元行不行"),
    ("demo-3", "ORD-2026-000111 物流到哪了"),
    ("demo-3", "这都延误了，我要投诉，你们得赔"),
    ("demo-4", "退款多久到账"),
    ("demo-4", "转人工"),
    ("demo-5", "SKU-FAN-Z9 华南有货吗"),
    ("demo-5", "那就给我留一件，我明天买"),
    ("demo-6", "订单 ORD-2026-000101 实付多少钱？"),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="电商导购/客服 Agent（合规门禁原型）")
    ap.add_argument("--ask", help="单轮提问，直接返回")
    ap.add_argument("--session", default="cli", help="会话 id（同一 id 才有上下文与幂等重放）")
    ap.add_argument("--demo", action="store_true", help="跑固定演示脚本（docs/演示脚本.md）")
    ap.add_argument("--audit", metavar="SESSION_ID", help="导出某会话的审计记录")
    ap.add_argument("--rebuild", action="store_true", help="先重建合成店铺库再启动")
    ap.add_argument("--fresh-audit", action="store_true", help="清空审计库后再跑（演示首轮判定用）")
    args = ap.parse_args(argv)

    if args.rebuild:
        seed_main([])
    if args.fresh_audit:
        from src.config import settings as _s
        path = _s().path(_s().audit_db_path)
        if path.exists():
            path.unlink()
    agent = Agent()
    if args.audit:
        rows = agent.audit.rows(args.audit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"合成店铺数据（种子）: content_sha256={content_hash(agent.db_path)}  条目={len(agent.kb.entries)}")
    print(f"意图体系: {agent.router.coverage()}")
    print(f"生成器: {getattr(agent.provider, 'name', '?')}  门禁规则版本: {agent.rules.version}")
    print("输入问题开始会话；输入 /exit 退出，/audit 查看本轮留痕，/switch 转人工。\n")

    if args.ask:
        _print(agent.handle(args.ask, session_id=args.session))
        agent.close()
        return 0

    if args.demo:
        for sid, text in DEMO_TURNS:
            print("\n" + "=" * 78)
            print(f"会话 {sid} ｜ 用户 ▶ {text}")
            _print(agent.handle(text, session_id=sid))
        agent.close()
        return 0

    while True:
        try:
            text = input("用户 ▶ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit", "exit"):
            break
        if text == "/audit":
            print(json.dumps(agent.audit.rows(args.session), ensure_ascii=False, indent=2)[:4000])
            continue
        resp = agent.handle(text, session_id=args.session)
        _print(resp)
        if text.strip() in ("/switch", "转人工"):
            print(f"   已提交转人工请求：工单 {resp.handoff.ticket_ref}，队列 {resp.handoff.channel}（离线演示，不外发）")
    agent.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
