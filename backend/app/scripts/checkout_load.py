"""Lightweight concurrent checkout probe (not a capacity benchmark).

Creates real dev customers, adds one item, checks out and pays through the
running HTTP API, then checks inventory decreased by exactly the successful
order count. Single-process local dev only.

Run:
    uv run python -m app.scripts.checkout_load --requests 20 --concurrency 5
"""
from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx

from app.scripts._csrf import CsrfClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SKU = "SKU-NCM-001"


@dataclass
class Customer:
    phone: str
    client: httpx.Client


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(percentile * (len(ordered) - 1))))
    return ordered[index]


def _admin_client(base_url: str) -> httpx.Client:
    client = CsrfClient(base_url=base_url, timeout=20.0)
    response = client.post(
        "/api/admin/session",
        json={"email": "owner@afterops.local", "password": "owner123456"},
    )
    if response.status_code != 200:
        client.close()
        raise RuntimeError(f"管理员登录失败：HTTP {response.status_code} {response.text}")
    return client


def _set_stock(admin: httpx.Client, qty: int) -> tuple[str, int]:
    detail = admin.get(f"/api/admin/products/{SKU}")
    if detail.status_code != 200:
        raise RuntimeError(f"读取商品失败：HTTP {detail.status_code}")
    inventory = detail.json()["inventory"]
    warehouse_id = inventory[0]["warehouse_id"] if inventory else "default"
    warehouse_name = inventory[0]["warehouse_name"] if inventory else "默认仓"
    response = admin.put(
        f"/api/admin/products/{SKU}/inventory/{warehouse_id}",
        json={"qty_available": qty, "warehouse_name": warehouse_name},
    )
    if response.status_code != 200:
        raise RuntimeError(f"补库存失败：HTTP {response.status_code} {response.text}")
    before = sum(row["qty_available"] for row in response.json()["inventory"])
    return warehouse_id, before


def _customers(base_url: str, count: int) -> list[Customer]:
    seed = int(time.time() * 1000) % 100_000_000
    result: list[Customer] = []
    for offset in range(count):
        phone = f"136{(seed + offset * 7919) % 100_000_000:08d}"
        client = CsrfClient(base_url=base_url, timeout=20.0)
        otp = client.post("/api/store/otp/request", json={"phone": phone})
        if otp.status_code != 202:
            client.close()
            raise RuntimeError(f"OTP 请求失败：HTTP {otp.status_code}")
        code = otp.json().get("debug_code")
        if not code:
            client.close()
            raise RuntimeError("OTP 未返回 debug_code（需要 dev 环境）")
        session = client.post(
            "/api/store/session", json={"phone": phone, "code": code}
        )
        if session.status_code != 200:
            client.close()
            raise RuntimeError(f"会话创建失败：HTTP {session.status_code}")
        result.append(Customer(phone=phone, client=client))
    return result


def _purchase(customer: Customer) -> tuple[bool, float, str]:
    started = time.monotonic()
    try:
        add = customer.client.post(
            "/api/store/cart/items", json={"sku": SKU, "qty": 1}
        )
        if add.status_code != 200:
            return False, (time.monotonic() - started) * 1000, f"add:{add.status_code}"
        checkout = customer.client.post(
            "/api/store/checkout",
            headers={"Idempotency-Key": f"checkout-load-{uuid4().hex}"},
            json={
                "address": {
                    "recipient_name": "并发顾客",
                    "recipient_phone": customer.phone,
                    "region": "华东",
                    "city": "上海",
                    "detail": "并发测试路 1 号",
                }
            },
        )
        if checkout.status_code != 201:
            return False, (time.monotonic() - started) * 1000, f"checkout:{checkout.status_code}"
        payment_id = checkout.json()["payment_id"]
        payment = customer.client.post(
            f"/api/store/payments/{payment_id}/mock-confirm"
        )
        if payment.status_code != 200:
            return False, (time.monotonic() - started) * 1000, f"pay:{payment.status_code}"
        return True, (time.monotonic() - started) * 1000, ""
    except (httpx.HTTPError, OSError, KeyError, ValueError) as exc:
        return False, (time.monotonic() - started) * 1000, str(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AfterOps 并发结算探测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument(
        "--report", default=str(REPO_ROOT / "reports" / "checkout-load.md")
    )
    args = parser.parse_args(argv)

    admin = _admin_client(args.base_url)
    customers: list[Customer] = []
    try:
        _warehouse, before = _set_stock(
            admin, args.requests + 50
        )
        customers = _customers(args.base_url, args.requests)
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(_purchase, customers))
    finally:
        for customer in customers:
            customer.client.close()
        admin.close()

    successes = sum(1 for ok, _elapsed, _error in results if ok)
    failures = [
        error for ok, _elapsed, error in results if not ok and error
    ]
    latencies = [elapsed for _ok, elapsed, _error in results]
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)

    admin = _admin_client(args.base_url)
    try:
        detail = admin.get(f"/api/admin/products/{SKU}")
        inventory = detail.json()["inventory"]
        after = sum(row["qty_available"] for row in inventory)
    finally:
        admin.close()
    expected = before - successes
    oversold = after != expected or after < 0
    passed = len(results) == args.requests and not failures and not oversold

    lines = [
        "# 结算并发探测",
        "",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- API：`{args.base_url}`",
        f"- 请求：{args.requests}，并发：{args.concurrency}，SKU：`{SKU}` × 1",
        f"- 完成：{len(results)}，成功：{successes}，失败：{len(failures)}",
        f"- 延迟（含加购/结算/支付）：p50 {p50:.1f}ms / p95 {p95:.1f}ms / p99 {p99:.1f}ms",
        f"- 库存：初始 {before}，成功订单 {successes}，理论剩余 {expected}，实际剩余 {after}",
        f"- 结果：**{'PASS' if passed else 'FAIL'}**",
    ]
    if failures:
        lines += ["", "## 失败样本", ""] + [f"- `{error}`" for error in failures[:10]]
    lines += [
        "",
        "> 单机、单 uvicorn 进程、dev 数据；每单先加购、结算、mock 支付。",
        "> 会留下评测顾客/订单/库存与审计数据；不构成生产容量、吞吐或 SLA 结论。",
    ]
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"[checkout-load] {'PASS' if passed else 'FAIL'} {len(results)}/{args.requests}，"
        f"成功 {successes}，失败 {len(failures)}，库存 {before}->{after} → {report}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
