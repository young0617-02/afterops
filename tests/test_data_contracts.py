"""数据契约测试：规则/数据文件本身必须是合法、可解析、口径一致的。

这类用例是被真实事故逼出来的：taxonomy.csv 一度把带槽位的行写成 9 列，导致
required_slots/needs_handoff 整体错位；committal_lexicon.csv 里的 `{0,6}` 含逗号，
被 CSV 解析成两列，承诺词典静默失效。两者都不会让单测报错，只会让门禁悄悄变松。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from src.gates import Ruleset
from src.intents import IntentRouter
from src.schemas import CommitClass
from src.shop.db import TABLES, connect

REPO = Path(__file__).resolve().parents[1]
TAXONOMY = REPO / "data" / "intents" / "taxonomy.csv"
JDDC_MAP = REPO / "data" / "intents" / "jddc_label_map.csv"
LEXICON = REPO / "data" / "gates" / "committal_lexicon.csv"
WHITELIST = REPO / "data" / "gates" / "commitment_whitelist.csv"
URGENCY = REPO / "data" / "gates" / "urgency_lexicon.txt"
GOLDEN = REPO / "evals" / "golden_set_v1.jsonl"

KNOWN_CLASSES = {c.value for c in CommitClass}


def test_taxonomy_has_exactly_8_columns_per_row():
    rows = list(csv.reader(TAXONOMY.read_text(encoding="utf-8").splitlines()))
    assert {len(r) for r in rows} == {8}, f"列数不齐：{[(i, len(r)) for i, r in enumerate(rows) if len(r) != 8]}"


def test_taxonomy_shape_and_uniqueness():
    d = list(csv.DictReader(TAXONOMY.read_text(encoding="utf-8").splitlines()))
    codes = [r["code"] for r in d]
    assert len(codes) == len(set(codes)), "二级意图编码重复"
    assert len({r["l1"] for r in d}) == 3, "方案 F-002 约定 3 个一级"
    assert 15 <= len(d) <= 20, "方案 F-002 约定 15–20 个二级"
    for r in d:
        assert r["risk"] in ("normal", "sensitive", "high"), r
        assert r["required_slots"] in ("", "order_id", "sku", "topic"), r
        assert r["needs_handoff"] in ("0", "1"), r
        assert r["keywords"].strip(), f"意图 {r['code']} 没有关键词，永远不会被命中"
        assert "," not in r["keywords"], "关键词列内不得出现裸逗号（会破坏 CSV 列对齐）"


def test_high_risk_intents_are_the_four_declared_categories():
    d = {r["code"]: r for r in csv.DictReader(TAXONOMY.read_text(encoding="utf-8").splitlines())}
    high = {c for c, r in d.items() if r["risk"] == "high"}
    assert {"REFUND_CONFIRM", "COMPENSATION", "PRICE_PROTECT", "COMPLAINT_ESCALATION"} == high


def test_lexicon_patterns_compile_and_classes_known():
    rows = list(csv.DictReader(LEXICON.read_text(encoding="utf-8").splitlines()))
    assert rows, "承诺词典不能为空"
    for r in rows:
        assert r["commit_class"] in KNOWN_CLASSES | {"promise_generic"}, r
        re.compile(r["pattern"])  # 抛异常即失败
    assert len({r["pattern"] for r in rows}) == len(rows), "词典模式重复"


def test_lexicon_catches_all_commitment_classes():
    rs = Ruleset()
    found = {cls for _r, cls, _n in rs.lexicon}
    for cls in (c.value for c in CommitClass if c.value.startswith("promise") or
                c.value in ("confirm_action", "assume_resolution")):
        assert cls in found or cls == "promise_generic", f"承诺类别 {cls} 没有任何词典模式覆盖"


def test_whitelist_covers_every_commit_class():
    rows = list(csv.DictReader(WHITELIST.read_text(encoding="utf-8").splitlines()))
    listed = {r["commit_class"] for r in rows}
    assert listed == KNOWN_CLASSES, f"白名单未覆盖：{KNOWN_CLASSES - listed}；多余：{listed - KNOWN_CLASSES}"
    allowed = {r["commit_class"] for r in rows if r["allowed_intents"] != "NONE"}
    denied = {r["commit_class"] for r in rows if r["allowed_intents"] == "NONE"}
    assert not (allowed & denied), "同一类别既允许又禁止"
    assert all(c.startswith("promise") or c in ("confirm_action", "assume_resolution") for c in denied), \
        "被禁止的必须都是承诺类"


def test_urgency_lexicon_non_empty_and_matches_intent():
    words = [l.strip() for l in URGENCY.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert len(words) >= 15
    assert {"投诉", "12315", "律师"} <= set(words)


def test_jddc_map_columns_and_codes_exist():
    rows = list(csv.DictReader(JDDC_MAP.read_text(encoding="utf-8").splitlines()))
    assert {len(r) for r in rows} == {7}, "JDDC 映射表应为 7 列"
    router = IntentRouter()
    codes = set(router.by_code) | {"UNMAPPED"}
    for r in rows:
        assert r["mapped_l2_code"] in codes, r
        assert r["map_status"] in ("paper_verified", "pending_corpus"), r
        assert r["evidence_locator"].strip() and "同上" != r["evidence_locator"].strip()


def test_golden_set_shape_and_references():
    cases = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(cases) >= 150, f"方案 F-013 要求 150–200 条，实际 {len(cases)}"
    assert len(cases) <= 200
    assert len({c["id"] for c in cases}) == len(cases), "用例 id 重复"
    tiers = {c["tier"] for c in cases}
    assert tiers == {"T1_retrieval", "T2_tool", "T3_mixed", "T4_high_risk"}, tiers
    high_risk = [c for c in cases if c["tier"] == "T4_high_risk"]
    assert len(high_risk) >= 30, f"高危子集需 ≥30 条（方案 R5），实际 {len(high_risk)}"

    d = list(csv.DictReader(TAXONOMY.read_text(encoding="utf-8").splitlines()))
    codes = {r["code"] for r in d}
    entry_ids = set()
    for line in (REPO / "data" / "synthetic_shop" / "kb_seed.sql").read_text(encoding="utf-8").splitlines():
        m = re.search(r"'([A-Z0-9\-]{4,})','v\d'", line)
        if m:
            entry_ids.add(m.group(1))
    for c in cases:
        assert c["turns"], c
        assert (c.get("forbidden") or c.get("exp_facts") or c.get("exp_evidence")
                or c.get("exp_tools") or c.get("exp_status_not_found") or c.get("exp_version")), \
            f"{c['id']} 没有任何可判定的期望"
        for code in ([c["exp_intent"]] if isinstance(c.get("exp_intent"), str) else c.get("exp_intent", [])):
            assert code in codes, f"{c['id']} 引用了不存在的意图 {code}"
        for eid in c.get("exp_evidence", []):
            assert eid in entry_ids, f"{c['id']} 引用了知识库里没有的条目 {eid}"
        if c["tier"] == "T4_high_risk":
            assert c.get("expect_block_commit"), f"{c['id']} 高危用例必须断言生成前拦截"


def test_shop_tables_present_and_populated(shop_db):
    conn = connect(shop_db)
    try:
        for t in TABLES:
            n = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            assert n > 0, f"{t} 为空表"
    finally:
        conn.close()


def test_money_fields_have_no_floats_and_orders_have_items(shop_db):
    conn = connect(shop_db)
    try:
        orphans = conn.execute("SELECT oi.order_id FROM order_items oi LEFT JOIN orders o "
                              "ON o.order_id = oi.order_id WHERE o.order_id IS NULL").fetchall()
        assert not orphans
        for r in conn.execute("SELECT payable_cents FROM v_order_settlement").fetchall():
            assert isinstance(r["payable_cents"], int)
    finally:
        conn.close()
