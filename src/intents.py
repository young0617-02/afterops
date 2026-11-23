"""意图体系与路由（F-002）。

自有体系：3 个一级 / 19 个二级（data/intents/taxonomy.csv），其中高危意图集单独标注。
JDDC 侧映射：data/intents/jddc_label_map.csv 逐条留痕（论文可核对项标 paper_verified，
其余标 pending_corpus 并计入未覆盖清单，不硬凑——方案 R3）。

分类器是**规则 + 关键词权重**的确定性实现：不依赖模型即可复跑，且让「意图判定」这一步
本身可被测试与变异（若换成 LLM 判定，红线用例将无法稳定复现）。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.config import REPO_ROOT
from src.schemas import DialogueSlots, IntentDef, IntentPrediction, RiskLevel

TAXONOMY_PATH = REPO_ROOT / "data" / "intents" / "taxonomy.csv"
JDDC_MAP_PATH = REPO_ROOT / "data" / "intents" / "jddc_label_map.csv"

RISK = {"normal": RiskLevel.NORMAL, "sensitive": RiskLevel.SENSITIVE, "high": RiskLevel.HIGH}

# 通用词（几乎每句话都会碰到）命中时降权，避免「订单/价格」这类词把具体问题吃掉
GENERIC_KEYWORDS = {"订单", "状态", "物流", "价格", "多少钱", "什么", "怎么", "如何"}

# 「要 AI 替我确认/保证」类诱导句式：无论命中的业务意图是什么，一律升级为高危处理
PROMISE_INDUCEMENT = [
    r"你能(不能)?保证",
    r"你敢保证",
    r"你能承诺",
    r"你(替|帮)我确认",
    r"确认(一下)?(?:吧|呗)",
    r"给个准话",
    r"你就说(能|可以|行)",
    r"算不算你们?的责任",
    r"是不是(应该|必须)(退|赔)",
    r"直接(退|赔|给我退)了吧",
    r"(全额|全部)(退|赔)给(我|您)吧",
]

@dataclass
class IntentRouter:
    taxonomy_path: Path = TAXONOMY_PATH
    jddc_map_path: Path = JDDC_MAP_PATH
    intents: list[IntentDef] = field(default_factory=list)
    jddc_rows: list[dict] = field(default_factory=list)
    _patterns: list = field(default_factory=list)
    _inducements: list = field(default_factory=lambda: [re.compile(p) for p in PROMISE_INDUCEMENT])

    def __post_init__(self):
        self.load_taxonomy()
        self.load_jddc_map()

    # ------------------------------------------------------------------ #
    def load_taxonomy(self) -> None:
        self.intents = []
        with open(self.taxonomy_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                kws = [k for k in row["keywords"].split("|") if k]
                self.intents.append(IntentDef(
                    l1=row["l1"], l2=row["l2"], code=row["code"], risk=RISK[row["risk"]],
                    description=row.get("note", "") or row["l2"],
                    required_slots=tuple(s for s in row["required_slots"].split("+") if s),
                    needs_handoff=row["needs_handoff"] == "1",
                ))
                for kw in kws:
                    self._patterns.append((kw, self.intents[-1]))
        self._patterns.sort(key=lambda x: len(x[0]), reverse=True)

    def load_jddc_map(self) -> None:
        with open(self.jddc_map_path, encoding="utf-8") as f:
            self.jddc_rows = list(csv.DictReader(f))

    # ------------------------------------------------------------------ #
    @property
    def by_code(self) -> dict[str, IntentDef]:
        return {i.code: i for i in self.intents}

    @property
    def high_risk_codes(self) -> set[str]:
        return {i.code for i in self.intents if i.risk is RiskLevel.HIGH}

    @property
    def l1_names(self) -> list[str]:
        seen: list[str] = []
        for i in self.intents:
            if i.l1 not in seen:
                seen.append(i.l1)
        return seen

    def is_high_risk(self, code: str) -> bool:
        d = self.by_code.get(code)
        return bool(d and d.risk is RiskLevel.HIGH)

    def coverage(self) -> dict:
        verified = [r for r in self.jddc_rows if r["map_status"] == "paper_verified"]
        pending = [r for r in self.jddc_rows if r["map_status"] != "paper_verified"]
        unmapped = [r["mapped_l2_code"] for r in verified if r["mapped_l2_code"] == "UNMAPPED"]
        return {
            "jddc_rows_total": len(self.jddc_rows),
            "paper_verified": len(verified),
            "pending_corpus": len(pending),
            "unmapped_verified": len(unmapped),
            "l1": len(self.l1_names),
            "l2": len(self.intents),
            "high_risk": len(self.high_risk_codes),
        }

    # ------------------------------------------------------------------ #
    def detect_promise_inducement(self, text: str) -> list[str]:
        """返回命中的诱导句式（留痕用，供 F-008/F-010 升级为高危）。"""
        return [p.pattern for p in self._inducements if p.search(text)]

    def classify(self, text: str, slots: DialogueSlots | None = None) -> IntentPrediction:
        t = (text or "").strip().lower()
        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}
        for kw, d in self._patterns:
            if kw.lower() in t:
                w = 1.0 + (len(kw) / 4.0) * (0.4 if kw in GENERIC_KEYWORDS else 1.0)
                scores[d.code] = scores.get(d.code, 0.0) + w
                matched.setdefault(d.code, []).append(kw)

        inducements = self.detect_promise_inducement(t)
        # 有订单指向 + 问的是「这一单付了多少」→ 归订单查询，而不是价格咨询（避免把订单金额当页面价）
        order_ref = bool(re.search(r"订单|ord-\d", t)) or bool(slots and slots.order_id)
        if order_ref and re.search(r"实付|付了|支付|一共|总共|金额|多少钱", t) and not re.search(r"退款|退货|赔付|差价", t):
            scores["ORDER_STATUS"] = scores.get("ORDER_STATUS", 0.0) + 2.4
            matched.setdefault("ORDER_STATUS", []).append("order_amount_disambiguation")

        if not scores:
            # 无关键词命中：用槽位推断兜底意图，置信度压低（F-012 会拦）
            if slots and slots.product_keyword:
                code = "PROD_SPEC"
            elif slots and slots.order_id:
                code = "ORDER_STATUS"
            else:
                code = "GENERAL_CHAT"
            return IntentPrediction(code=code, l1=self.by_code[code].l1, score=0.30,
                                    matched=[f"slot_fallback:{code}"],
                                    is_high_risk=self.is_high_risk(code) or bool(inducements),
                                    alternatives=sorted(scores.items(), key=lambda x: -x[1])[:3])

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], self._risk_rank(kv[0])))
        best_code, best_raw = ranked[0][0], ranked[0][1]
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        # 高危优先：若同句里同时命中高危意图与非高危意图，取高危（宁可多转人工）
        high_in_sentence = [c for c, _ in ranked if self.is_high_risk(c)]
        if high_in_sentence and not self.is_high_risk(best_code):
            best_code = high_in_sentence[0]
            best_raw = scores[best_code]
        margin = best_raw - runner
        score = min(0.99, 0.45 + 0.12 * min(4, best_raw) + (0.12 if margin > 1.5 else 0.0))
        if inducements:
            score = min(0.99, max(score, 0.6))
        return IntentPrediction(
            code=best_code,
            l1=self.by_code[best_code].l1,
            score=round(score, 3),
            matched=sorted(set(matched.get(best_code, [])), key=len, reverse=True)[:8],
            is_high_risk=self.is_high_risk(best_code) or bool(inducements),
            alternatives=[(c, round(s, 2)) for c, s in ranked[:3]],
        )

    def _risk_rank(self, code: str) -> int:
        order = {RiskLevel.HIGH: 0, RiskLevel.SENSITIVE: 1, RiskLevel.NORMAL: 2}
        return order[self.by_code[code].risk]
