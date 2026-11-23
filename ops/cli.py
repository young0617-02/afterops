"""服务编排命令行入口（一键启动 / 一键停止）。

    python -m ops.cli check                 # 只预检，不动任何进程
    python -m ops.cli start                 # 一键启动（已在运行 → 先全停再按序启动）
    python -m ops.cli start --smoke         # 启动后自动打一发 /v1/chat 验证真的能答
    python -m ops.cli stop                  # 一键优雅停止（顺序=依赖逆序；不释放端口不算完成）
    python -m ops.cli stop --force          # 优雅超时后强杀整棵进程树
    python -m ops.cli restart / status / logs api -n 50

服务清单与端口都来自 .env（OPS_SERVICES / APP_HOST / APP_PORT / OLLAMA_TAKEOVER），不写死在代码里。
退出码：0 成功；1 有失败项；2 用法或配置错误。
"""
from __future__ import annotations

import argparse
import json
import sys

from ops.manager import Manager, Result
from ops.services import http_json
from src.config import load_settings, reset_settings


def _print(results: list[Result], as_json: bool, title: str) -> int:
    ok = all(r.ok for r in results)
    if as_json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(f"\n—— {title} ——")
    for r in results:
        print(r.render())
    print(f"\n结果：{'全部成功' if ok else '存在失败项'}"
          f"（{sum(1 for r in results if r.ok)}/{len(results)} 项通过）")
    return 0 if ok else 1


def _make_manager(args) -> Manager:
    cfg = reset_settings(load_settings(args.env))
    if args.port:
        cfg = _override_port(cfg, args.port, args.host)
    if args.only:
        return Manager(cfg, selection=[s.strip() for s in args.only.split(",") if s.strip()],
                       start_timeout=args.timeout, stop_timeout=args.stop_timeout)
    return Manager(cfg, start_timeout=args.timeout, stop_timeout=args.stop_timeout)


def _override_port(cfg, port: int, host: str | None):
    from dataclasses import replace

    return replace(cfg, app_port=port, app_host=host or cfg.app_host)


def _smoke(m: Manager) -> list[Result]:
    """启动后真跑一发：/health 与 /v1/chat，确认服务不是「进程在但答不出」。"""
    api = m.registry.get("api")
    out: list[Result] = []
    if api is None or not hasattr(api, "url"):
        return [Result("smoke", "skip", True, "本次未编排 api，跳过冒烟检查")]
    health = http_json(api.url("/health"), timeout=5.0)
    out.append(Result("api", "smoke", bool(health and health[0] == 200),
                      f"GET /health → {health[0] if health else '无响应'}"))
    probe = http_json(api.url("/v1/chat"), method="POST",
                      body={"text": "订单 ORD-2026-000101 实付多少钱", "session_id": "smoke"}, timeout=15.0)
    if probe and probe[0] == 200 and isinstance(probe[1], dict):
        payload = probe[1]
        out.append(Result("api", "smoke", True,
                          f"POST /v1/chat → 200｜意图={payload['intent']['code']}"
                          f"｜门禁={payload['gate_actions']}｜人工出口={payload['handoff']['available']}"
                          f"｜引用={payload['citations'][:2]}"))
        trace = http_json(api.url(f"/v1/audit/{payload['request_key']}"), timeout=5.0)
        rule_ver = trace[1].get("ruleset_version", "?") if trace and isinstance(trace[1], dict) else "?"
        out.append(Result("api", "smoke", bool(trace and trace[0] == 200),
                          f"GET /v1/audit/<key> → {trace[0] if trace else '无响应'}"
                          f"｜反查到规则版本 {rule_ver}"))
    else:
        out.append(Result("api", "smoke", False, f"POST /v1/chat 失败：{probe}",
                          advice=f"看日志：{m.logs('api', 20)}"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ops.cli", description="一键启动 / 一键停止应用服务")
    ap.add_argument("command", choices=["check", "start", "stop", "restart", "status", "logs"])
    ap.add_argument("--env", default=None, help="指定 .env 路径（默认仓库根 .env）")
    ap.add_argument("--only", default=None, help="逗号分隔的服务名，覆盖 OPS_SERVICES")
    ap.add_argument("--port", type=int, default=None, help="覆盖 APP_PORT（本次运行）")
    ap.add_argument("--host", default=None, help="覆盖 APP_HOST（本次运行）")
    ap.add_argument("--timeout", type=float, default=30.0, help="单服务启动后等待健康的秒数")
    ap.add_argument("--stop-timeout", type=float, default=10.0, help="停止时等待进程退出的秒数")
    ap.add_argument("--force", action="store_true", help="停止时直接强杀进程树")
    ap.add_argument("--keep-running", action="store_true",
                    help="已在运行时不重启（默认：先全停再按序启动）")
    ap.add_argument("--smoke", action="store_true", help="启动后打一发 /health 与 /v1/chat")
    ap.add_argument("--json", action="store_true", dest="as_json", help="机器可读输出")
    ap.add_argument("log_service", nargs="?", default="api", help="logs 子命令的服务名")
    ap.add_argument("-n", "--lines", type=int, default=40, help="logs 输出行数")
    args = ap.parse_args(argv)

    try:
        m = _make_manager(args)
    except KeyError as exc:
        print(f"配置错误：{exc}\n建议动作：检查 OPS_SERVICES / --only 里的服务名；"
              f"可用清单见 `python -m ops.cli status`", file=sys.stderr)
        return 2

    if args.command == "check":
        results = m.preflight_all() + m.status()
        return _print(results, args.as_json, "预检与当前状态")
    if args.command == "status":
        return _print(m.status(), args.as_json, "服务状态")
    if args.command == "logs":
        print(m.logs(args.log_service, args.lines))
        return 0
    if args.command == "stop":
        results = m.stop(force=args.force)
        return _print(results, args.as_json, "停止服务（逆序 + 端口/孤儿校验）")
    if args.command == "restart":
        stop_results = m.stop(force=args.force)
        start_results = m.start(skip_if_running=False)
        return _print(stop_results + start_results, args.as_json, "重启服务（先停后起）")

    # start：默认「检测到已运行 → 全停 → 按序起」
    results = m.start(skip_if_running=args.keep_running)
    if args.smoke and all(r.ok for r in results):
        results += _smoke(m)
    return _print(results, args.as_json, "启动服务（预检 → 全停 → 按序启动 → 健康检查）")


if __name__ == "__main__":
    sys.exit(main())
