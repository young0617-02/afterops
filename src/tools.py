"""确定性工具层（F-005）：schema 约束 + Pydantic 校验 + 超时与降级。

红线：所有金额、库存、订单状态、物流节点一律由 SQL 计算/读取后原样返回，
模型不得参与计算（验收 A9 的结构性落点）。
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from src.config import settings
from src.kb import KnowledgeBase
from src.schemas import (
    LogisticsQueryArgs,
    OrderQueryArgs,
    PolicyQueryArgs,
    ProductLookupArgs,
    StockCheckArgs,
    ToolStatus,
    ToolResult,
)
from src.shop.db import connect, ensure_shop_db, money

_CENTS_KEY_RE = re.compile(r"cents$")


def collect_numbers(data: Any, prefix: str = "") -> list[str]:
    """抽取工具结果里的确定性数值字符串，作为「承诺白名单」的合法数字来源。"""
    out: list[str] = []

    def add(v: Any) -> None:
        s = str(v)
        if s and s not in out:
            out.append(s)

    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                out.extend(collect_numbers(v, k))
            elif isinstance(v, bool) or v is None:
                continue
            elif isinstance(v, (int, float)):
                add(v)
                if _CENTS_KEY_RE.search(str(k)) and isinstance(v, int):
                    add(money(v))
                    add(f"{v / 100:.2f}")
            else:
                for m in re.finditer(r"\d+(?:\.\d+)?", str(v)):
                    add(m.group())
    elif isinstance(data, list):
        for item in data:
            out.extend(collect_numbers(item, prefix))
    return out


def _result_digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


class ToolHub:
    def __init__(self, db_path=None, kb: KnowledgeBase | None = None, timeout_ms: int | None = None,
                 sleep_on_timeout: bool = False):
        self.db_path = ensure_shop_db(db_path or settings().path(settings().shop_db_path))
        self.kb = kb or KnowledgeBase(self.db_path)
        self.timeout_ms = timeout_ms if timeout_ms is not None else settings().tool_timeout_ms
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")
        self.calls: list[ToolResult] = []
        self._registry: dict[str, tuple[type[BaseModel], Callable[..., dict], str]] = {
            "query_order": (OrderQueryArgs, self._query_order, "查询订单状态与实付/可退金额（SQL 计算）"),
            "query_logistics": (LogisticsQueryArgs, self._query_logistics, "查询物流轨迹与时效"),
            "query_policy": (PolicyQueryArgs, self._query_policy, "查询当前生效的政策条款（双时态，含出处与版本）"),
            "check_stock": (StockCheckArgs, self._check_stock, "查询指定区域实时库存"),
            "lookup_product": (ProductLookupArgs, self._lookup_product, "按关键词/SKU 查询商品与页面价格"),
        }

    # ------------------------------------------------------------------ #
    @property
    def catalog(self) -> dict[str, str]:
        return {name: desc for name, (_, _, desc) in self._registry.items()}

    def call(self, tool: str, args: dict[str, Any] | None = None) -> ToolResult:
        args = dict(args or {})
        t0 = time.monotonic()
        if tool not in self._registry:
            return self._log(ToolResult(tool=tool, status=ToolStatus.ERROR, error="未知工具", elapsed_ms=0))
        model, fn, _ = self._registry[tool]
        try:
            validated = model(**args)
        except ValidationError as exc:
            return self._log(ToolResult(tool=tool, status=ToolStatus.INVALID_ARGS,
                                        error=f"入参校验失败: {exc.errors()[0].get('msg', exc)}",
                                        elapsed_ms=self._ms(t0)))
        except TypeError as exc:  # 多余字段等
            return self._log(ToolResult(tool=tool, status=ToolStatus.INVALID_ARGS,
                                        error=f"入参不合法: {exc}", elapsed_ms=self._ms(t0)))

        try:
            fut = self._pool.submit(fn, validated)
            data = fut.result(timeout=self.timeout_ms / 1000.0)
        except FutTimeout:
            fut.cancel()
            return self._log(ToolResult(
                tool=tool, status=ToolStatus.TIMEOUT, degraded=True, elapsed_ms=self._ms(t0),
                error=f"工具执行超过 {self.timeout_ms} ms，已降级：不输出任何未经确认的数据",
                data={"fallback_hint": "数据源暂时不可用，只能说明流程并转人工"},
            ))
        except Exception as exc:  # sqlite 错误、编码问题等
            return self._log(ToolResult(tool=tool, status=ToolStatus.ERROR, degraded=True,
                                        elapsed_ms=self._ms(t0),
                                        error=f"{type(exc).__name__}: {exc}"))
        status = ToolStatus.OK if data else ToolStatus.NOT_FOUND
        nums = collect_numbers(data)
        return self._log(ToolResult(tool=tool, status=status, data=data or {}, numbers=nums,
                                    elapsed_ms=self._ms(t0),
                                    error=None if data else "未匹配到记录"))

    def _log(self, r: ToolResult) -> ToolResult:
        self.calls.append(r)
        return r

    @staticmethod
    def _ms(t0: float) -> int:
        return int((time.monotonic() - t0) * 1000)

    # ------------------------------------------------------------------ #
    # 具体实现：全部走参数化 SQL
    # ------------------------------------------------------------------ #
    def _query_order(self, a: OrderQueryArgs) -> dict:
        with closing(connect(self.db_path)) as conn:
            if a.order_id:
                rows = conn.execute("SELECT * FROM v_order_settlement WHERE order_id = ?",
                                    (a.order_id.strip(),)).fetchall()
            elif a.customer_phone:
                rows = conn.execute(
                    "SELECT * FROM v_order_settlement WHERE customer_phone = ? ORDER BY order_id DESC LIMIT 5",
                    (a.customer_phone.strip(),)).fetchall()
            else:
                return {}
            if not rows:
                return {}
            cap = conn.execute("SELECT refundable_max_cents FROM v_refund_cap WHERE order_id = ?",
                               (rows[0]["order_id"],)).fetchone()
            items = conn.execute(
                "SELECT sku, qty, unit_price_cents FROM order_items WHERE order_id = ? ORDER BY sku",
                (rows[0]["order_id"],)).fetchall()
            o = conn.execute("SELECT placed_at, refund_reason, applied_at, address_city, delivery_city "
                             "FROM orders WHERE order_id = ?", (rows[0]["order_id"],)).fetchone()
            r = dict(rows[0])
            return {
                "order_id": r["order_id"],
                "status": r["status"],
                "refund_status": r["refund_status"],
                "pay_channel": r["pay_channel"],
                "item_total_cents": r["item_total_cents"],
                "freight_cents": r["freight_cents"],
                "coupon_cents": r["coupon_cents"],
                "payable_cents": r["payable_cents"],
                "payable_display": money(r["payable_cents"]),
                "refundable_max_cents": cap["refundable_max_cents"] if cap else 0,
                "refundable_max_display": money(cap["refundable_max_cents"]) if cap else "",
                "items": [dict(i) for i in items],
                "placed_at": o["placed_at"],
                "refund_reason": o["refund_reason"],
                "refund_applied_at": o["applied_at"],
                "address_city": o["address_city"],
                "delivery_city": o["delivery_city"],
                "result_ref": _result_digest(r),
            }

    def _query_logistics(self, a: LogisticsQueryArgs) -> dict:
        with closing(connect(self.db_path)) as conn:
            s = conn.execute("SELECT * FROM shipments WHERE order_id = ?", (a.order_id.strip(),)).fetchone()
            if not s:
                return {}
            events = conn.execute(
                "SELECT happened_at, node, description FROM shipment_events WHERE order_id = ? "
                "ORDER BY happened_at", (a.order_id.strip(),)).fetchall()
            d = dict(s)
            return {
                "order_id": d["order_id"],
                "carrier": d["carrier"],
                "tracking_no": d["tracking_no"],
                "shipment_status": d["status"],
                "current_node": d["current_node"],
                "shipped_at": d["shipped_at"],
                "delivered_at": d["delivered_at"],
                "eta_days": d["eta_days"],
                "events": [dict(e) for e in events],
                "result_ref": _result_digest(d),
            }

    def _query_policy(self, a: PolicyQueryArgs) -> dict:
        entry = self.kb.get_current(topic=a.topic.strip(), as_of=a.as_of)
        if not entry:
            entry = self.kb.search(a.topic, as_of=a.as_of, top_k=1, use_cache=False)
            if not entry:
                return {}
            entry = entry[0].entry
        return {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "title": entry.title,
            "body": entry.body,
            "source": entry.source,
            "valid_from": entry.valid_from.isoformat(),
            "valid_until": entry.valid_until.isoformat() if entry.valid_until else "长期有效",
            "as_of": (a.as_of or self.kb.default_as_of.isoformat()),
            "citation": entry.citation(),
            "result_ref": _result_digest(vars(entry)),
        }

    def _check_stock(self, a: StockCheckArgs) -> dict:
        with closing(connect(self.db_path)) as conn:
            if a.region:
                row = conn.execute("SELECT * FROM inventory WHERE sku = ? AND region = ?",
                                   (a.sku.strip(), a.region.strip())).fetchone()
            else:
                row = conn.execute(
                    "SELECT sku, SUM(qty_available) AS qty_available, GROUP_CONCAT(region) AS regions "
                    "FROM inventory WHERE sku = ? GROUP BY sku", (a.sku.strip(),)).fetchone()
            if not row:
                return {}
            d = dict(row)
            p = conn.execute("SELECT title, price_cents FROM products WHERE sku = ?", (d["sku"],)).fetchone()
            return {
                "sku": d["sku"],
                "region": a.region or "全部区域",
                "qty_available": d["qty_available"],
                "in_stock": bool(d["qty_available"]),
                "regions": d.get("regions"),
                "title": p["title"] if p else None,
                "updated_at_note": "库存为提交订单时点快照",
                "result_ref": _result_digest(d),
            }

    def _lookup_product(self, a: ProductLookupArgs) -> dict:
        clauses, params = [], []
        if a.sku:
            clauses.append("sku = ?")
            params.append(a.sku.strip())
        if a.keyword:
            like = f"%{a.keyword.strip()}%"
            clauses.append("(title LIKE ? OR category LIKE ? OR brand LIKE ? OR specs LIKE ?)")
            params += [like, like, like, like]
        if a.max_price_cents is not None:
            clauses.append("price_cents <= ?")
            params.append(a.max_price_cents)
        if not clauses:
            return {}
        sql = ("SELECT sku, title, category, brand, specs, price_cents, support_7d_no_reason "
               f"FROM products WHERE {' AND '.join(clauses)} ORDER BY price_cents LIMIT ?")
        with closing(connect(self.db_path)) as conn:
            rows = [dict(r) for r in conn.execute(sql, (*params, a.limit)).fetchall()]
        if not rows:
            return {}
        for r in rows:
            r["price_display"] = money(r["price_cents"])
            r["support_7d_no_reason"] = bool(r["support_7d_no_reason"])
        return {"count": len(rows), "products": rows, "result_ref": _result_digest(rows)}
