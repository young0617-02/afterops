"""F-004/F-005：合成店铺数据与工具层（含 A9 金额来源、超时降级）。"""
from __future__ import annotations

import sqlite3

import pytest

from src.schemas import ToolStatus
from src.shop.db import build_shop_db, connect, content_hash
from src.tools import collect_numbers


def test_seed_is_deterministic(tmp_path):
    p1 = build_shop_db(tmp_path / "a.db")
    p2 = build_shop_db(tmp_path / "b.db")
    assert content_hash(p1) == content_hash(p2), "同种子必须产出同数据"


def test_amounts_are_computed_by_sql_view(shop_db):
    """实付 = 商品总价 + 运费 - 优惠券，全部由视图计算，不在任何 Python/模型里手算。"""
    with connect(shop_db) as conn:
        row = conn.execute("SELECT * FROM v_order_settlement WHERE order_id='ORD-2026-000101'").fetchone()
    assert row["item_total_cents"] == 89900 + 2 * 5900
    assert row["payable_cents"] == row["item_total_cents"] + row["freight_cents"] - row["coupon_cents"]
    assert row["payable_cents"] == 98700


def test_refund_cap_is_zero_for_unpaid_order(shop_db):
    with connect(shop_db) as conn:
        r = conn.execute("SELECT refundable_max_cents FROM v_refund_cap WHERE order_id='ORD-2026-000106'").fetchone()
        ok = conn.execute("SELECT refundable_max_cents FROM v_refund_cap WHERE order_id='ORD-2026-000101'").fetchone()
    assert r["refundable_max_cents"] == 0
    assert ok["refundable_max_cents"] == 98700


def test_all_money_columns_are_integer_cents(shop_db):
    with connect(shop_db) as conn:
        for table, col in [("products", "price_cents"), ("orders", "freight_cents"), ("orders", "coupon_cents"),
                           ("order_items", "unit_price_cents")]:
            for r in conn.execute(f"SELECT {col} AS v FROM {table}").fetchall():
                assert isinstance(r["v"], int) and r["v"] >= 0, f"{table}.{col} 必须是整数分"


def test_query_order_returns_display_and_numbers(agent):
    res = agent.tools.call("query_order", {"order_id": "ORD-2026-000101"})
    assert res.status is ToolStatus.OK
    assert res.data["payable_display"] == "987.00元"
    assert "98700" in res.numbers and "987.00" in res.numbers


def test_query_order_by_phone_lists_recent_orders(agent):
    res = agent.tools.call("query_order", {"customer_phone": "13800000001"})
    assert res.status is ToolStatus.OK
    assert res.data["order_id"].startswith("ORD-")


@pytest.mark.parametrize("tool,args", [
    ("query_order", {}),
    ("query_logistics", {"order_id": ""}),
    ("check_stock", {"sku": ""}),
    ("lookup_product", {}),
    ("query_policy", {"topic": ""}),
    ("check_stock", {"sku": "X", "unknown_field": 1}),
])
def test_invalid_arguments_are_rejected(agent, tool, args):
    res = agent.tools.call(tool, args)
    assert res.status is ToolStatus.INVALID_ARGS, f"{tool} 入参校验未生效"
    assert res.numbers == []


def test_unknown_tool(agent):
    assert agent.tools.call("delete_order", {}).status is ToolStatus.ERROR


def test_not_found_does_not_fabricate(agent):
    res = agent.tools.call("query_order", {"order_id": "ORD-1999-000000"})
    assert res.status is ToolStatus.NOT_FOUND
    assert res.data == {} and res.numbers == []


def test_timeout_degrades_without_numbers(agent, monkeypatch):
    """工具超过 3s：返回降级结果，且**不得**带任何数字（模型拿不到数据就不能编）。"""
    import time as _t

    monkeypatch.setitem(agent.tools._registry, "query_order",
                        (agent.tools._registry["query_order"][0],
                         lambda a: (_t.sleep(0.35), {})[1], "slow"))
    monkeypatch.setattr(agent.tools, "timeout_ms", 100)
    res = agent.tools.call("query_order", {"order_id": "ORD-2026-000101"})
    assert res.status is ToolStatus.TIMEOUT
    assert res.degraded and res.numbers == []


def test_db_error_degrades(agent, monkeypatch):
    def boom(args):
        raise sqlite3.OperationalError("no such table: v_order_settlement")

    monkeypatch.setitem(agent.tools._registry, "query_order",
                        (agent.tools._registry["query_order"][0], boom, "broken"))
    res = agent.tools.call("query_order", {"order_id": "ORD-2026-000101"})
    assert res.status is ToolStatus.ERROR and res.degraded


def test_sql_injection_attempt_is_treated_as_data(agent):
    evil = "ORD-2026-000101' OR '1'='1"
    res = agent.tools.call("query_order", {"order_id": evil})
    assert res.status is ToolStatus.NOT_FOUND, "参数化查询应把它当作不存在的单号"


def test_collect_numbers_only_reads_money_and_digits():
    data = {"payable_cents": 98700, "payable_display": "987.00元", "status": "已签收", "items": [{"qty": 2}]}
    nums = collect_numbers(data)
    assert "98700" in nums and "987.00" in nums and "2" in nums
    assert "已签收" not in nums


def test_stock_snapshot_zero_is_reported_honestly(agent):
    res = agent.tools.call("check_stock", {"sku": "SKU-FAN-Z9", "region": "华南"})
    assert res.status is ToolStatus.OK
    assert res.data["qty_available"] == 0 and res.data["in_stock"] is False
