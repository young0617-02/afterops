"""合规门禁层（F-008–F-012）——本项目的招牌，全部可测试。

设计要点（对应 GB/T 47746—2026 四条义务的落点）：

1. **生成前拦截**（F-010 红线，验收 A2）：门禁先在「生成计划」阶段决定哪些句子可以落地。
   承诺类别（promise_* / confirm_action / assume_resolution）一律不在白名单内，
   因此根本不会进入生成器的输入——不是「模型先说出来、事后道歉」。
2. **输出侧复核**（第二层）：真实模型可能不守指令，所以对最终文本再做一次
   承诺词典 + 数字溯源扫描；违规文本被替换为白名单原子的确定性渲染并留痕。
3. **只解释不确认**（F-008）：高危意图只允许 POLICY_CITE / PROCEDURE / LIMITATION / HANDOFF。
4. **人工入口不得隐藏**（F-009）：HandoffOffer 是响应的必需字段；紧急词命中即自动转接。
5. **低置信不硬答**（F-012）：综合置信度低于阈值 → 只解释 + 主动转人工。
6. **失效即不答**（fail-closed）：高危意图检索不到现行条文时，不编造规则，只给转人工。
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.config import REPO_ROOT, settings
from src.shop.db import money
from src.schemas import (
    COMMITTAL_CLASSES,
    CommitClass,
    DialogueSlots,
    GenerationPlan,
    GateAction,
    GateDecision,
    IntentPrediction,
    KBEntry,
    RetrievalHit,
    ToolResult,
    UtteranceAtom,
)

GATES_DIR = REPO_ROOT / "data" / "gates"
WHITELIST_CSV = GATES_DIR / "commitment_whitelist.csv"
COMMITTAL_LEXICON_CSV = GATES_DIR / "committal_lexicon.csv"
URGENCY_LEXICON_TXT = GATES_DIR / "urgency_lexicon.txt"
TAXONOMY_CSV = REPO_ROOT / "data" / "intents" / "taxonomy.csv"

SENT_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
NUM_CLAIM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:元|块钱|块|折|%|个百分点|个工作日|工作日|小时|天|个月|月|年|件|台|副)")
COMMITTAL_VALUES = {c.value for c in COMMITTAL_CLASSES} | {"promise_generic"}

# 意图 → 需要的证据类型（决定 fail-closed 与降级口径）
TOOL_REQUIRED_BY_INTENT = {
    "ORDER_STATUS": "query_order",
    "REFUND_STATUS": "query_order",
    "REFUND_CONFIRM": "query_order",
    "LOGISTICS_TRACK": "query_logistics",
    "LOGISTICS_ISSUE": "query_logistics",
    "ORDER_MODIFY": "query_order",
    "PRICE_PROTECT": "query_order",
    "COMPENSATION": "query_logistics",
    "PROD_SPEC": "lookup_product",
    "PROD_RECOMMEND": "lookup_product",
    "PRICE_PROMO": "lookup_product",
    "STOCK_SHIP": "check_stock",
}
POLICY_TOPIC_BY_INTENT = {
    "RETURN_POLICY": ("return", "exchange"),
    "REFUND_STATUS": ("refund_time", "procedure"),
    "REFUND_CONFIRM": ("procedure", "return"),
    "COMPENSATION": ("compensation",),
    "PRICE_PROTECT": ("price_protect",),
    "ORDER_MODIFY": ("address_change",),
    "WARRANTY": ("warranty",),
    "INVOICE": ("invoice",),
    "FREIGHT": ("freight",),
    "SHIP_ETA": ("stock",),
    "STOCK_SHIP": ("stock", "freight"),
    "PRICE_PROMO": ("recommend",),
    "PROD_RECOMMEND": ("recommend",),
    "PROD_SPEC": ("recommend", "warranty"),
    "COMPLAINT_ESCALATION": ("human",),
    "HUMAN_REQUEST": ("human",),
}

LIMITATION_TEXT = {
    "high": "这条规则我可以解释，但退款、赔付、补差等结论由人工坐席核定，我不代为确认。",
    "soft": "根据目前掌握的信息，我不能给出确定结论，下面只说明规则与可办理的入口。",
    "degraded": "这一单的具体数据我这边暂时查不到（数据源未返回），为避免说错，我只说明规则并交给人工处理。",
}
HANDOFF_TEXT = "需要人工受理，回复「转人工」即可；我会把本轮的问题、依据条文版本和查询到的订单数据一并移交坐席队列。"


def _norm(sent: str) -> str:
    return re.sub(r"\s+", "", sent.strip().strip("，,。.!！?？;；:：")).lower()


@dataclass
class ScanFinding:
    commit_class: str
    pattern: str
    excerpt: str
    stage: str = "output_scan"


@dataclass
class ScanResult:
    blocked: bool
    findings: list[ScanFinding] = field(default_factory=list)
    safe_text: str = ""
    unapproved_numbers: list[str] = field(default_factory=list)


class Ruleset:
    """承诺白名单 + 词典 + 紧急词表，带版本指纹（审计可反查规则版本，F-011/A11）。"""

    def __init__(self, whitelist_csv=WHITELIST_CSV, lexicon_csv=COMMITTAL_LEXICON_CSV,
                 urgency_txt=URGENCY_LEXICON_TXT, taxonomy_csv=TAXONOMY_CSV):
        self.paths = [Path(whitelist_csv), Path(lexicon_csv), Path(urgency_txt), Path(taxonomy_csv)]
        self.allowed: dict[str, dict] = {}
        self.denied: set[str] = set()
        for row in self._read_csv(whitelist_csv):
            cls = row["commit_class"]
            if row["allowed_intents"] == "NONE":
                self.denied.add(cls)
            else:
                self.allowed[cls] = row
        self.lexicon = [(re.compile(r["pattern"]), r["commit_class"], r["note"])
                        for r in self._read_csv(lexicon_csv)]
        self.urgency = [l.strip() for l in Path(urgency_txt).read_text(encoding="utf-8").splitlines()
                        if l.strip() and not l.strip().startswith("#")]
        self.enabled = True  # 变异测试用开关（默认开启）

    @staticmethod
    def _read_csv(p) -> list[dict]:
        with open(p, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    @property
    def version(self) -> str:
        h = hashlib.sha256()
        for p in self.paths:
            if p.exists():
                h.update(p.read_bytes())
        return h.hexdigest()[:12]

    def is_allowed(self, commit_class: CommitClass) -> bool:
        """白名单判定：类别必须在允许表内，且不属于任何承诺类别。"""
        if not self.enabled:
            return False
        v = commit_class.value if isinstance(commit_class, CommitClass) else str(commit_class)
        return v in self.allowed and v not in COMMITTAL_VALUES

    def classify_text(self, text: str) -> list[ScanFinding]:
        if not self.lexicon:
            return []
        findings = []
        for rx, cls, _note in self.lexicon:
            for m in rx.finditer(text):
                start = max(0, m.start() - 6)
                findings.append(ScanFinding(cls if cls != "promise_generic" else "promise_refund_outcome",
                                            rx.pattern, text[start:m.end()].strip()))
        return findings


class GateEngine:
    def __init__(self, ruleset: Ruleset | None = None, confidence_threshold: float | None = None):
        self.rules = ruleset or Ruleset()
        self.confidence_threshold = (settings().confidence_threshold
                                     if confidence_threshold is None else confidence_threshold)

    # ------------------------------------------------------------------ #
    def urgency_hits(self, text: str) -> list[str]:
        return [w for w in self.rules.urgency if w in text]

    def confidence(self, intent: IntentPrediction, hits: list[RetrievalHit],
                   tools: dict[str, ToolResult], policy_needed: bool) -> float:
        """综合置信度：意图 0.5 + 检索 0.3 + 工具 0.2（权重写死，便于测试与复现）。"""
        s = 0.5 * intent.score
        if policy_needed:
            # 只有「主题对齐的现行条文」才算检索证据；BM25 总能凑出弱命中，不给分
            topic_hit = self._aligned_hit(hits, POLICY_TOPIC_BY_INTENT.get(intent.code, ()))
            s += 0.3 * (topic_hit.score if topic_hit else 0.0)
        else:
            s += 0.3 * 0.6
        req = TOOL_REQUIRED_BY_INTENT.get(intent.code)
        if req:
            tr = tools.get(req)
            s += 0.2 * (1.0 if (tr and tr.ok) else 0.1)
        else:
            s += 0.2 * 0.6
        return round(min(1.0, max(0.0, s)), 3)

    # ------------------------------------------------------------------ #
    def pre_generation(self, intent: IntentPrediction, text: str, hits: list[RetrievalHit],
                       tools: dict[str, ToolResult], slots: DialogueSlots,
                       urgency: list[str], inducements: list[str] | None = None,
                       missing_slot: bool = False
                       ) -> tuple[GenerationPlan, list[GateDecision]]:
        decisions: list[GateDecision] = []
        policy_topics = POLICY_TOPIC_BY_INTENT.get(intent.code, ())
        policy_needed = bool(policy_topics)
        conf = self.confidence(intent, hits, tools, policy_needed)

        # --- F-010 承诺拦截（生成前）---
        asked = self.rules.classify_text(text)
        blocked_classes = sorted({f.commit_class for f in asked})
        inducements = inducements or []
        commit_requested = bool(blocked_classes) or bool(inducements) or intent.is_high_risk
        plan_atoms: list[UtteranceAtom] = []
        allowed_numbers: list[str] = []
        must_handoff = False

        if commit_requested:
            must_handoff = True
            decisions.append(GateDecision(
                gate="G-010-commitment-whitelist", action=GateAction.BLOCK_COMMIT,
                reason=f"白名单外承诺类别已在生成前剔除：{blocked_classes or [f'诱导句式 {len(inducements)} 条' if inducements else '按高危意图推定']}",
                stage="pre_generation",
                details={"asked": [f.pattern for f in asked], "inducements": inducements,
                         "intent": intent.code}))
        else:
            decisions.append(GateDecision(gate="G-010-commitment-whitelist", action=GateAction.PASS,
                                          reason="本轮索取的信息在白名单内（规则解释/数据查询）"))

        # --- F-008 重大事项只解释不确认 ---
        if intent.is_high_risk:
            must_handoff = True
            decisions.append(GateDecision(
                gate="G-008-explain-only", action=GateAction.EXPLAIN_ONLY,
                reason=f"高危意图 {intent.code}：只输出规则解释与依据，确认动作强制转人工",
                stage="pre_generation"))

        # --- F-009 紧急自动转接 ---
        if urgency:
            must_handoff = True
            decisions.append(GateDecision(
                gate="G-009-urgency-transfer", action=GateAction.FORCE_HANDOFF,
                reason=f"命中紧急词 {urgency}：自动转接人工", stage="pre_generation",
                details={"urgency_hits": urgency}))
        if intent.code == "HUMAN_REQUEST":
            must_handoff = True
            decisions.append(GateDecision(gate="G-009-human-request", action=GateAction.FORCE_HANDOFF,
                                          reason="用户明确要求人工：立即转接"))

        # --- F-012 置信度门禁 ---
        soft = conf < self.confidence_threshold
        if soft:
            must_handoff = True
            decisions.append(GateDecision(
                gate="G-012-confidence", action=GateAction.SOFT_ANSWER,
                reason=f"综合置信度 {conf} < 阈值 {self.confidence_threshold}：只解释，不硬答",
                details={"confidence": conf, "intent_score": intent.score,
                         "top_hit": hits[0].entry.entry_id if hits else None}))

        # --- 关键槽位缺失（F-007）：先要数据，不猜订单 ---
        if missing_slot:
            plan_atoms.append(UtteranceAtom(
                CommitClass.PROCEDURE,
                "请先提供订单号（形如 ORD-2026-000101）或下单手机号，我才能核对这一单的实际状态；"
                "在拿到数据之前我不对这一单下结论。"))
            decisions.append(GateDecision(gate="G-007-slot-tracking", action=GateAction.REQUEST_MISSING_SLOT,
                                          reason="必要槽位（订单号）缺失：先索取数据，不猜测、不硬答"))

        # 主题对齐的现行条文（没有它就退化成「引用无关条款」，不允许）
        topic_hit = self._aligned_hit(hits, policy_topics)

        # --- fail-closed：高危却无现行条文 ---
        if intent.is_high_risk and policy_needed and topic_hit is None:
            decisions.append(GateDecision(
                gate="G-008-fail-closed", action=GateAction.FAIL_CLOSED,
                reason=f"高危意图需要条文依据，但未检索到主题 {policy_topics} 的现行有效条目：不编造规则",
                stage="pre_generation"))
            return self._finish_plan(plan_atoms, allowed_numbers, decisions, must_handoff=True,
                                     conf=conf, intent=intent, blocked=blocked_classes, fail_closed=True), decisions

        # --- 组装允许落地的原子句 ---
        tr_name = TOOL_REQUIRED_BY_INTENT.get(intent.code)
        tr = tools.get(tr_name) if tr_name else None
        if tr is not None and not tr.ok:
            must_handoff = True
            plan_atoms.append(UtteranceAtom(CommitClass.LIMITATION, LIMITATION_TEXT["degraded"]))
            decisions.append(GateDecision(gate="G-005-tool-degradation", action=GateAction.SOFT_ANSWER,
                                          reason=f"工具 {tr_name} 未返回可用数据（{tr.status.value}）：只说明规则并转人工"))
        for name, res in tools.items():
            if name == "query_policy" or not res.ok:
                continue
            atom = self._fact_atom(intent, res)
            if atom and atom.text not in {p.text for p in plan_atoms}:
                plan_atoms.append(atom)
                allowed_numbers.extend(atom.numbers)

        if topic_hit is not None:
            cite = self._policy_atom(topic_hit, intent)
            plan_atoms.append(cite)
            allowed_numbers.extend(cite.numbers)
            # 条文本身写明「由坐席核定/受理」→ 结论不在 AI 权限内，本轮必须转人工（F-008 延伸）
            needs_adjudication = any(w in cite.text for w in ("核定", "坐席", "人工受理", "由人工"))
            if needs_adjudication and (intent.is_high_risk or commit_requested or slots.order_id):
                must_handoff = True
                decisions.append(GateDecision(gate="G-008-adjudication-required", action=GateAction.FORCE_HANDOFF,
                                              reason=f"所引条文 {cite.evidence_ids[0]} 把核定/受理动作划归人工，"
                                                     f"本轮结论不由 AI 给出", details={"entry": cite.evidence_ids}))
            if intent.is_high_risk or soft:
                plan_atoms.append(UtteranceAtom(
                    CommitClass.PROCEDURE,
                    "申请入口与流程：在订单详情页提交相应申请，上传凭证后由坐席受理，质检与核定完成后才会发起退款。",
                    evidence_ids=["FAQ-REFUND-PROCEDURE"]))
                plan_atoms.append(UtteranceAtom(
                    CommitClass.LIMITATION,
                    LIMITATION_TEXT["high"] if intent.is_high_risk else LIMITATION_TEXT["soft"]))

        if soft and not any(a.kind is CommitClass.LIMITATION for a in plan_atoms):
            plan_atoms.append(UtteranceAtom(CommitClass.LIMITATION, LIMITATION_TEXT["soft"]))
        if len(plan_atoms) == 0:
            plan_atoms.append(UtteranceAtom(
                CommitClass.PROCEDURE,
                "我可以帮你查订单状态与实付金额、物流轨迹、退换货与价保规则、商品价格与库存；"
                "把具体的单号或需求告诉我就行。"))
        plan_atoms.append(UtteranceAtom(CommitClass.HANDOFF, HANDOFF_TEXT))
        return self._finish_plan(plan_atoms, allowed_numbers, decisions, must_handoff,
                                 conf, intent, blocked_classes), decisions

    def _finish_plan(self, atoms, numbers, decisions, must_handoff, conf, intent, blocked,
                     fail_closed: bool = False) -> GenerationPlan:
        uniq_numbers: list[str] = []
        for n in numbers:
            if n not in uniq_numbers:
                uniq_numbers.append(n)
        if fail_closed:
            atoms = [UtteranceAtom(CommitClass.LIMITATION, LIMITATION_TEXT["high"]),
                     UtteranceAtom(CommitClass.HANDOFF, HANDOFF_TEXT)]
        plan = GenerationPlan(atoms=atoms, allowed_numbers=uniq_numbers, must_handoff=must_handoff,
                              confidence=conf, intent_code=intent.code, blocked_commits=blocked)
        if any(d.action is GateAction.EXPLAIN_ONLY for d in decisions) and not must_handoff:
            plan.must_handoff = True
        return plan

    # ------------------------------------------------------------------ #
    @staticmethod
    def _aligned_hit(hits: list[RetrievalHit], topics: tuple[str, ...]) -> RetrievalHit | None:
        """只允许引用与本轮意图主题对齐的条文，取其中最相关的一条。"""
        aligned = [h for h in hits if h.entry.topic in topics]
        return max(aligned, key=lambda h: h.score) if aligned else None

    def _fact_atom(self, intent: IntentPrediction, tr: ToolResult) -> UtteranceAtom | None:
        d = tr.data
        ref = [f"tool:{tr.tool}#{d.get('result_ref', '')}"]
        if tr.tool == "query_order":
            freight_disp, coupon_disp = money(d["freight_cents"]), money(d["coupon_cents"])
            txt = (f"订单 {d['order_id']} 当前状态：{d['status']}；退款状态：{d['refund_status']}；"
                   f"实付 {d['payable_display']}（{d['payable_cents']} 分 = 商品 {money(d['item_total_cents'])}"
                   f" + 运费 {freight_disp} − 优惠券 {coupon_disp}），可退上限 {d['refundable_max_display']}；"
                   f"支付方式 {d['pay_channel']}。")
            nums = [d["order_id"], str(d["payable_cents"]), d["payable_display"],
                    d["payable_display"].replace("元", ""), str(d["refundable_max_cents"]),
                    d["refundable_max_display"], d["refundable_max_display"].replace("元", ""),
                    str(d["item_total_cents"]), money(d["item_total_cents"]),
                    str(d["freight_cents"]), freight_disp, freight_disp.replace("元", ""),
                    str(d["coupon_cents"]), coupon_disp, coupon_disp.replace("元", "")]
            return UtteranceAtom(CommitClass.TOOL_FACT, txt, evidence_ids=ref, numbers=nums)
        if tr.tool == "query_logistics":
            txt = (f"物流：{d['carrier']}，运单 {d['tracking_no']}，当前状态 {d['shipment_status']}，"
                   f"最近节点「{d['current_node']}」。")
            if d.get("eta_days") is not None:
                txt += f" 经验时效 {d['eta_days']} 天（页面区间，非承诺）。"
            nums = [d["tracking_no"], d["order_id"]] + ([str(d["eta_days"])] if d.get("eta_days") is not None else [])
            return UtteranceAtom(CommitClass.STATUS_INFO, txt, evidence_ids=ref, numbers=nums)
        if tr.tool == "lookup_product":
            ps = d.get("products") or []
            if not ps:
                return None
            first = ps[0]
            txt = (f"页面信息：{first['title']}（{first['sku']}），{first['specs']}，"
                   f"当前页面价 {first['price_display']}；该商品"
                   f"{'标注支持' if first['support_7d_no_reason'] else '标注不支持'}无理由退货（期限以《售后政策》为准）。"
                   f"价格以提交订单时页面展示为准。")
            if len(ps) > 1:
                txt += " 同类可选：" + "、".join(f"{p['sku']} {p['price_display']}" for p in ps[1:4]) + "。"
            nums: list[str] = []
            for p in ps:
                nums += [str(p["price_cents"]), f"{p['price_cents'] / 100:.2f}", p["price_display"], p["sku"]]
            return UtteranceAtom(CommitClass.TOOL_FACT, txt, evidence_ids=ref, numbers=nums)
        if tr.tool == "check_stock":
            txt = (f"库存快照：{d['sku']} 在 {d['region']} 当前可售 {d['qty_available']} 件"
                   f"（{d['updated_at_note']}）。"
                   + ("已无可用库存，补货时间我无法确认。" if not d["in_stock"] else ""))
            return UtteranceAtom(CommitClass.TOOL_FACT, txt, evidence_ids=ref,
                                 numbers=[str(d["qty_available"]), d["sku"]])
        return None

    def _policy_atom(self, hit: RetrievalHit, intent: IntentPrediction) -> UtteranceAtom:
        e: KBEntry = hit.entry
        body = e.body
        # 引用条文原句：只截取与问题最相关的前两句，并附出处与版本
        parts = [sen for sen in SENT_SPLIT_RE.split(body) if sen.strip()][:3]
        text = "规则依据：" + "。".join(parts) + f"。（{e.source}，{e.entry_id}@{e.version}，" \
            f"有效期 {e.valid_from} 至 {e.valid_until or '长期'}）"
        nums = [m.group(1) for m in NUM_CLAIM_RE.finditer(text)]
        return UtteranceAtom(CommitClass.POLICY_CITE, text, evidence_ids=[e.citation()], numbers=nums)

    # ------------------------------------------------------------------ #
    def render_plan(self, plan: GenerationPlan) -> str:
        return "".join(a.text for a in plan.atoms)

    def output_scan(self, plan: GenerationPlan, text: str) -> ScanResult:
        """第二层：对最终文本做承诺与数字溯源复核（防模型不守指令）。"""
        if not self.rules.enabled:
            return ScanResult(blocked=False)
        approved = [_norm(a.text) for a in plan.atoms]
        findings: list[ScanFinding] = []
        bad_numbers: list[str] = []
        allowed = set(plan.allowed_numbers)
        for sent in SENT_SPLIT_RE.split(text):
            if not sent.strip():
                continue
            n = _norm(sent)
            if n and any(n in a for a in approved):
                continue
            findings.extend([f for f in self.rules.classify_text(sent)
                             if f.commit_class in COMMITTAL_VALUES])
            for m in NUM_CLAIM_RE.finditer(sent):
                if m.group(1) not in allowed:
                    bad_numbers.append(m.group(0).strip())
        blocked = bool(findings) or bool(bad_numbers)
        return ScanResult(blocked=blocked, findings=findings, unapproved_numbers=bad_numbers,
                          safe_text=self.render_plan(plan))
