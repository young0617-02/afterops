"""F-002：意图体系、高危意图集与 JDDC 映射留痕。"""
from __future__ import annotations

import pytest

from src.intents import IntentRouter

EXPECTED_L1 = 3
EXPECTED_L2_RANGE = (15, 20)

CASES = [
    ("订单 ORD-2026-000101 现在什么状态", "ORDER_STATUS"),
    ("这个快递单号轨迹查一下", "LOGISTICS_TRACK"),
    ("我的包裹一直没收到", "LOGISTICS_ISSUE"),
    ("要改收货地址", "ORDER_MODIFY"),
    ("支持七天无理由吗", "RETURN_POLICY"),
    ("怎么申请换货", "RETURN_POLICY"),
    ("退款进度查一下", "REFUND_STATUS"),
    ("退款多久到账", "REFUND_STATUS"),
    ("给我退款", "REFUND_CONFIRM"),
    ("必须全额退", "REFUND_CONFIRM"),
    ("我要赔偿", "COMPENSATION"),
    ("延迟发货要赔付", "COMPENSATION"),
    ("降价了要价保补差", "PRICE_PROTECT"),
    ("我要投诉你们", "COMPLAINT_ESCALATION"),
    ("转人工", "HUMAN_REQUEST"),
    ("开票抬头怎么改", "INVOICE"),
    ("保修多久", "WARRANTY"),
    ("这款耳机参数怎么样", "PROD_SPEC"),
    ("有货吗现在", "STOCK_SHIP"),
    ("多少钱", "PRICE_PROMO"),
    ("有没有推荐的", "PROD_RECOMMEND"),
    ("几天能到", "SHIP_ETA"),
]


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


@pytest.mark.parametrize("text,expected", CASES, ids=[c[1] for c in CASES])
def test_intent_routing(router, text, expected):
    assert router.classify(text).code == expected, router.classify(text).matched


def test_taxonomy_shape(router):
    assert router.coverage()["l1"] == EXPECTED_L1
    lo, hi = EXPECTED_L2_RANGE
    assert lo <= router.coverage()["l2"] <= hi, "方案 F-002 约定 3 一级 / 15-20 二级"


def test_high_risk_set_covers_the_four_categories(router):
    """方案 F-002：高危意图集 = 退款、赔偿、价格承诺、投诉。"""
    hr = router.high_risk_codes
    assert {"REFUND_CONFIRM", "COMPENSATION", "PRICE_PROTECT", "COMPLAINT_ESCALATION"} <= hr
    assert "ORDER_STATUS" not in hr and "RETURN_POLICY" not in hr


@pytest.mark.parametrize("text", [
    "你能保证全额退吗",
    "你替我确认一下退款吧",
    "给个准话行不行",
    "你就说能退",
    "算不算你们的责任",
])
def test_promise_inducement_detected(router, text):
    assert router.detect_promise_inducement(text), text


def test_inducement_upgrades_risk(router):
    p = router.classify("这个价你能保证是最低的吗")
    assert p.is_high_risk, "价格承诺诱导应升级为高危"


def test_jddc_mapping_rows_are_evidenced(router):
    """映射表逐条留痕：每条 paper_verified 都要给出可核对的定位。"""
    for row in router.jddc_rows:
        assert row["evidence_locator"].strip(), row
        assert row["map_status"] in ("paper_verified", "pending_corpus"), row
        if row["map_status"] == "paper_verified":
            assert "LREC 2020" in row["evidence_locator"] or "JDDC Corpus" in row["evidence_locator"]


def test_jddc_mapping_coverage_is_honest(router):
    cov = router.coverage()
    assert cov["paper_verified"] == 10, "论文正文/示例可核对的映射共 10 条"
    assert cov["pending_corpus"] == 1, "其余 JDDC 标签必须以「待语料核对」单列，不得硬凑（R3）"
    assert any(r["map_status"] == "pending_corpus" for r in router.jddc_rows)


def test_mapped_codes_exist_in_taxonomy(router):
    codes = set(router.by_code)
    for row in router.jddc_rows:
        code = row["mapped_l2_code"]
        if code != "UNMAPPED":
            assert code in codes, f"映射指向了不存在的二级意图：{code}"


def test_unknown_input_falls_back_low_confidence(router):
    p = router.classify("嗯嗯那个啥")
    assert p.code in ("GENERAL_CHAT", "ORDER_STATUS", "PROD_SPEC")
    assert p.score <= 0.35
