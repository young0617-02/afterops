"""Lightweight read-path load probe (not a capacity benchmark).

Runs a fixed number of concurrent reads against the running API and reports
p50/p95/p99 latency and error count. Single-process local dev only.

Run:
    uv run python -m app.scripts.load_test --requests 200 --concurrency 20
"""
from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(percentile * (len(ordered) - 1))))
    return ordered[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AfterOps 轻量读路径压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument(
        "--report", default=str(REPO_ROOT / "reports" / "load-test.md")
    )
    args = parser.parse_args(argv)

    urls = [
        "/api/store/products?limit=20",
        "/api/store/policies",
        "/health",
    ]
    latencies: list[float] = []
    errors = 0
    statuses: dict[int, int] = {}

    def one(index: int) -> None:
        nonlocal errors
        started = time.monotonic()
        try:
            response = client.get(urls[index % len(urls)])
            elapsed = (time.monotonic() - started) * 1000
            latencies.append(elapsed)
            statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
            if response.status_code >= 500:
                errors += 1
        except (httpx.HTTPError, OSError):
            errors += 1

    wall_start = time.monotonic()
    with (
        httpx.Client(base_url=args.base_url.rstrip("/"), timeout=10.0) as client,
        ThreadPoolExecutor(max_workers=args.concurrency) as pool,
    ):
        list(pool.map(one, range(args.requests)))
    wall_seconds = time.monotonic() - wall_start

    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)
    rps = len(latencies) / wall_seconds if wall_seconds else 0.0

    lines = [
        "# 轻量读路径压测",
        "",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- API：`{args.base_url}`",
        f"- 请求：{args.requests}，并发：{args.concurrency}",
        f"- 完成：{len(latencies)}，错误：{errors}，状态分布：{statuses}",
        f"- 总耗时：{wall_seconds * 1000:.1f}ms，RPS：{rps:.1f}",
        f"- 延迟：p50 {p50:.1f}ms / p95 {p95:.1f}ms / p99 {p99:.1f}ms",
        "",
        "> 单机、单 uvicorn 进程、读路径、本地 dev 数据。",
        "> 不构成容量、吞吐、线上可用性或生产 SLA 结论；不做 QPS 宣称。",
    ]
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"[load] {len(latencies)}/{args.requests} 完成，错误 {errors}，"
        f"p50 {p50:.1f}ms p95 {p95:.1f}ms p99 {p99:.1f}ms → {report}"
    )
    return 0 if errors == 0 and len(latencies) == args.requests else 1


if __name__ == "__main__":
    raise SystemExit(main())
