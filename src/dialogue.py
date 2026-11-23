"""多轮对话层（F-007）：槽位跟踪、指代消解、上下文预算控制。

Token 口径：本模块用「汉字数 + 英文词数×1.3」估算，**不是**任何模型的真实 tokenizer；
在 docs/口径声明.md 里标为「估算」，不用于对外宣称成本。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from src.config import settings
from src.schemas import DialogueSlots

ORDER_RE = re.compile(r"\b(ORD-\d{4}-\d{6,8})\b", re.I)
ORDER_LOOSE_RE = re.compile(r"订单(?:号|编号)?\s*[:：=]?\s*([A-Za-z0-9\-]{6,})")
PHONE_RE = re.compile(r"(1[3-9]\d{9})")
SKU_RE = re.compile(r"\b(SKU-[A-Z]{2,6}-[A-Z0-9]{1,4})\b", re.I)
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
REGION_MAP = {
    "上海": "华东", "江苏": "华东", "浙江": "华东", "杭州": "华东", "南京": "华东", "苏州": "华东", "华东": "华东",
    "广东": "华南", "深圳": "华南", "广州": "华南", "东莞": "华南", "华南": "华南",
    "北京": "华北", "天津": "华北", "河北": "华北", "华北": "华北",
    "四川": "西南", "成都": "西南", "西安": "西北", "郑州": "华中", "武汉": "华中", "长沙": "华中",
}
REF_ORDER_RE = re.compile(r"(这个|那个|这支|这笔|那笔|刚那|刚才的|上面的|先前的)?订单|该订单|这单|那单")
REF_PRODUCT_RE = re.compile(r"(这个|那个|这款|那款|它|他|她)(商品|耳机|手表|键盘|音箱|电源|摄像头|硬盘|数据线|风扇)?")
REF_ABOVE_RE = re.compile(r"(上面|刚才|之前)(说|提|问)的")


def estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[一-鿿]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return int(cjk + words * 1.3) + 1


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


@dataclass
class Turn:
    role: str
    text: str
    tokens: int


@dataclass
class SessionState:
    session_id: str
    slots: DialogueSlots = field(default_factory=DialogueSlots)
    history: list[Turn] = field(default_factory=list)
    last_intent: str = "UNKNOWN"
    turn_no: int = 0
    dropped_turns: int = 0
    resolved_refs: list[str] = field(default_factory=list)

    @property
    def context_tokens(self) -> int:
        return sum(t.tokens for t in self.history)


@dataclass
class UpdateReport:
    filled: list[str] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)
    dropped: int = 0
    context_tokens: int = 0


class DialogueManager:
    def __init__(self, token_budget: int | None = None, max_recent_turns: int | None = None):
        s = settings()
        self.token_budget = token_budget or s.context_token_budget
        self.max_recent_turns = max_recent_turns or s.max_recent_turns

    # ------------------------------------------------------------------ #
    def extract_slots(self, text: str, slots: DialogueSlots) -> list[str]:
        filled: list[str] = []
        oid = ORDER_RE.search(text) or ORDER_LOOSE_RE.search(text)
        if oid:
            v = oid.group(1).upper()
            if v.isdigit() and len(v) == 11:  # 松散规则误抓手机号时丢弃
                v = ""
            if v:
                slots.order_id = v
                if v not in slots.focus_order_ids:
                    slots.focus_order_ids.append(v)
                slots.focus_order_ids = slots.focus_order_ids[-5:]
                filled.append(f"order_id={v}")
        ph = PHONE_RE.search(text)
        if ph and not slots.order_id:
            filled.append(f"phone={ph.group(1)}")
        sku = SKU_RE.search(text)
        if sku:
            slots.sku = sku.group(1).upper()
            if slots.sku not in slots.focus_skus:
                slots.focus_skus.append(slots.sku)
            slots.focus_skus = slots.focus_skus[-5:]
            filled.append(f"sku={slots.sku}")
        for k, v in REGION_MAP.items():
            if k in text:
                slots.region = v
                filled.append(f"region={v}")
                break
        d = DATE_RE.search(text)
        if d:
            slots.as_of = d.group(1)
            filled.append(f"as_of={d.group(1)}")
        return filled

    def resolve_references(self, text: str, slots: DialogueSlots) -> tuple[str, dict[str, str]]:
        """把「这个订单/它」等指代改写为具体标识，并把消解结果写进文本供检索使用。"""
        resolved: dict[str, str] = {}
        out = text
        if REF_ORDER_RE.search(out):
            cand = slots.order_id or (slots.focus_order_ids[-1] if slots.focus_order_ids else None)
            if cand and cand not in out:
                resolved["order_id"] = cand
                out = f"{out}（指代：订单 {cand}）"
        if REF_PRODUCT_RE.search(out) or REF_ABOVE_RE.search(out):
            cand = slots.sku or (slots.focus_skus[-1] if slots.focus_skus else None)
            if cand and cand not in out:
                resolved["sku"] = cand
                out = f"{out}（指代：商品 {cand}）"
        return out, resolved

    # ------------------------------------------------------------------ #
    def begin_turn(self, state: SessionState, user_text: str) -> tuple[str, UpdateReport]:
        text = normalize(user_text)
        report = UpdateReport()
        report.filled = self.extract_slots(text, state.slots)
        rewritten, refs = self.resolve_references(text, state.slots)
        report.resolved = refs
        state.history.append(Turn("user", text, estimate_tokens(text)))
        self._trim(state, report)
        report.context_tokens = state.context_tokens
        return rewritten, report

    def end_turn(self, state: SessionState, assistant_text: str, intent_code: str) -> None:
        state.history.append(Turn("assistant", assistant_text, estimate_tokens(assistant_text)))
        state.last_intent = intent_code
        state.turn_no += 1
        self._trim(state, UpdateReport())

    def _trim(self, state: SessionState, report: UpdateReport) -> None:
        """上下文预算控制：优先保近期轮次，超预算/超轮数时从最早一轮丢弃并计数（可审计）。"""
        dropped = 0
        while len(state.history) > self.max_recent_turns * 2:
            state.history.pop(0)
            dropped += 1
        while state.context_tokens > self.token_budget and len(state.history) > 2:
            state.history.pop(0)
            dropped += 1
        state.dropped_turns += dropped
        report.dropped = dropped

    def context_block(self, state: SessionState) -> str:
        return "\n".join(f"{t.role}: {t.text}" for t in state.history[-8:])
