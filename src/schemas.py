"""核心数据结构与接口定义（对外契约集中在本文件，模块间只传这些对象）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --------------------------------------------------------------------------- #
# 时间口径：双时态检索需要「查询时点」，允许固定以便复现（A10/A11）
# --------------------------------------------------------------------------- #
def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[: len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return None


def today(as_of: str | None = None) -> date:
    return parse_date(as_of) or date.today()


# --------------------------------------------------------------------------- #
# 意图体系（F-002）
# --------------------------------------------------------------------------- #
class RiskLevel(str, Enum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"  # 需引用条文，不得口述规则
    HIGH = "high"  # 重大事项：只解释不确认（F-008）


@dataclass(frozen=True)
class IntentDef:
    l1: str
    l2: str
    code: str
    risk: RiskLevel
    description: str = ""
    required_slots: tuple[str, ...] = ()
    needs_handoff: bool = False


@dataclass
class IntentPrediction:
    code: str  # 二级意图编码，未命中为 "UNKNOWN"
    l1: str
    score: float  # 0~1 规则置信度
    matched: list[str] = field(default_factory=list)  # 命中的关键词/规则，留痕用
    is_high_risk: bool = False
    alternatives: list[tuple[str, float]] = field(default_factory=list)

    @property
    def unknown(self) -> bool:
        return self.code == "UNKNOWN"


# --------------------------------------------------------------------------- #
# 工具层（F-005）：入参/出参 schema
# --------------------------------------------------------------------------- #
class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrderQueryArgs(ToolArgs):
    order_id: str | None = None
    customer_phone: str | None = None

    @model_validator(mode="after")
    def _need_one(self) -> "OrderQueryArgs":
        if not (self.order_id or self.customer_phone):
            raise ValueError("order_id 与 customer_phone 至少提供一个")
        return self


class LogisticsQueryArgs(ToolArgs):
    order_id: str = Field(min_length=4, max_length=64)


class PolicyQueryArgs(ToolArgs):
    topic: str = Field(min_length=1, max_length=64)
    category: str | None = None
    as_of: str | None = None  # YYYY-MM-DD，双时态查询时点


class StockCheckArgs(ToolArgs):
    sku: str = Field(min_length=1, max_length=64)
    region: str | None = None


class ProductLookupArgs(ToolArgs):
    keyword: str | None = None
    sku: str | None = None
    max_price_cents: int | None = Field(default=None, ge=0)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def _need_one(self) -> "ProductLookupArgs":
        if not (self.keyword or self.sku):
            raise ValueError("keyword 与 sku 至少提供一个")
        return self


class ToolStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class ToolResult:
    tool: str
    status: ToolStatus
    data: dict[str, Any] = field(default_factory=dict)
    numbers: list[str] = field(default_factory=list)  # 本结果里的确定性数值（承诺白名单溯源用）
    error: str | None = None
    elapsed_ms: int = 0
    degraded: bool = False  # 超时/异常后的降级回答

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.OK


# --------------------------------------------------------------------------- #
# 知识库（F-006）：双时态条目
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class KBEntry:
    entry_id: str
    title: str
    body: str
    category: str
    topic: str
    version: str
    source: str
    valid_from: date
    valid_until: date | None
    published_at: date
    supersedes: str | None = None

    def is_valid_on(self, day: date) -> bool:
        if day < self.valid_from:
            return False
        return self.valid_until is None or day < self.valid_until

    def citation(self) -> str:
        return f"[{self.entry_id}@{self.version}]"


@dataclass
class RetrievalHit:
    entry: KBEntry
    score: float
    reason: str = "bm25"


# --------------------------------------------------------------------------- #
# 门禁（F-008–F-012）
# --------------------------------------------------------------------------- #
class CommitClass(str, Enum):
    """承诺类别。白名单外的任何一项出现在输出前都必须被拦截（F-010）。"""

    # 非承诺类（白名单内，允许输出）
    POLICY_CITE = "policy_cite"  # 引用条文原文并标注出处
    TOOL_FACT = "tool_fact"  # 工具返回的确定性事实（金额/状态/库存）
    STATUS_INFO = "status_info"  # 状态描述（非承诺）
    PROCEDURE = "procedure"  # 流程说明（「你可以在 X 申请」）
    HANDOFF = "handoff"  # 转人工出口
    LIMITATION = "limitation"  # 能力边界声明
    APOLOGY = "apology"  # 中性致歉，不含任何承诺

    # 承诺类（一律禁止 AI 单方输出）
    PROMISE_REFUND_OUTCOME = "promise_refund_outcome"
    PROMISE_COMPENSATION = "promise_compensation"
    PROMISE_PRICE = "promise_price"
    PROMISE_DEADLINE = "promise_deadline"
    PROMISE_STOCK = "promise_stock"
    CONFIRM_ACTION = "confirm_action"  # 「已为您退款/已确认」
    ASSUME_RESOLUTION = "assume_resolution"  # 红线：本项目不使用「沉默即视为解决」口径


COMMITTAL_CLASSES = {
    CommitClass.PROMISE_REFUND_OUTCOME,
    CommitClass.PROMISE_COMPENSATION,
    CommitClass.PROMISE_PRICE,
    CommitClass.PROMISE_DEADLINE,
    CommitClass.PROMISE_STOCK,
    CommitClass.CONFIRM_ACTION,
    CommitClass.ASSUME_RESOLUTION,
}


@dataclass
class UtteranceAtom:
    kind: CommitClass
    text: str
    evidence_ids: list[str] = field(default_factory=list)  # KB entry_id 或 tool 调用 id
    numbers: list[str] = field(default_factory=list)  # 该句中被批准的数值（溯源）


@dataclass
class GenerationPlan:
    """门禁产出后、生成前的唯一输入。生成器只能落地这里的原子句，不能自造内容。"""

    atoms: list[UtteranceAtom] = field(default_factory=list)
    allowed_numbers: list[str] = field(default_factory=list)
    must_handoff: bool = False
    confidence: float = 0.0
    intent_code: str = "UNKNOWN"
    blocked_commits: list[str] = field(default_factory=list)  # 被白名单拒绝的承诺类别


class GateAction(str, Enum):
    PASS = "pass"
    EXPLAIN_ONLY = "explain_only"  # 只解释不确认（F-008）
    BLOCK_COMMIT = "block_commit"  # 承诺拦截（F-010）
    FORCE_HANDOFF = "force_handoff"  # 强制/自动转人工（F-009）
    SOFT_ANSWER = "soft_answer"  # 低置信：只解释 + 主动转人工（F-012）
    REQUEST_MISSING_SLOT = "request_missing_slot"  # 关键槽位缺失：先要数据再答（F-007）
    FAIL_CLOSED = "fail_closed"  # 异常/缺证据：宁可不答


@dataclass
class GateDecision:
    gate: str
    action: GateAction
    reason: str
    stage: str = "pre_generation"  # pre_generation | output_scan（红线要求前者优先）
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandoffOffer:
    """人工入口（F-009：每轮必须存在，不得设计成障碍）。"""

    available: bool
    prompt: str
    channel: str = "human_agent_queue"
    urgent: bool = False
    auto_transferred: bool = False
    queue_hint: str = ""
    ticket_ref: str | None = None


# --------------------------------------------------------------------------- #
# 会话与响应
# --------------------------------------------------------------------------- #
@dataclass
class DialogueSlots:
    order_id: str | None = None
    sku: str | None = None
    product_keyword: str | None = None
    topic: str | None = None
    region: str | None = None
    as_of: str | None = None
    refund_reason: str | None = None
    focus_order_ids: list[str] = field(default_factory=list)  # 指代消解用
    focus_skus: list[str] = field(default_factory=list)


@dataclass
class AgentResponse:
    """Agent 对外唯一响应体。"""

    session_id: str
    turn_id: str
    request_key: str
    reply: str
    intent: IntentPrediction
    gate_actions: list[GateAction]
    decisions: list[GateDecision]
    handoff: HandoffOffer
    citations: list[str]
    tool_calls: list["ToolCallLog"]
    confidence: float
    must_handoff: bool = False
    replayed: bool = False
    degraded: bool = False
    answerable: bool = True
    latency_ms: int = 0
    token_estimate: dict[str, int] = field(default_factory=dict)
    atoms: list[UtteranceAtom] = field(default_factory=list)


@dataclass
class ToolCallLog:
    tool: str
    args: dict[str, Any]
    status: ToolStatus
    elapsed_ms: int
    result_ref: str  # 结果哈希，审计反查用
    numbers: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class EvidenceRef:
    kind: str  # kb | tool
    ref_id: str
    version: str | None
    numbers: list[str] = field(default_factory=list)
