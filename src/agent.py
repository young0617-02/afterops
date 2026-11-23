"""编排层：意图 → 工具 → 检索 → 门禁 → 生成 → 复核 → 留痕（自研轻量编排，不用 LangChain）。

编排顺序刻意把「门禁」放在「生成」之前：生成器只接收门禁批准的原子句，
承诺类内容根本不会出现在它的输入里（F-010 要求「输出前拦截」而非事后道歉）。
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from src.audit import AuditLog, request_key_of
from src.config import settings
from src.dialogue import DialogueManager, SessionState, estimate_tokens, normalize
from src.gates import GateEngine, Ruleset
from src.intents import IntentRouter
from src.kb import KnowledgeBase
from src.llm import LLMRequest, ProviderError, get_provider
from src.schemas import (
    AgentResponse,
    CommitClass,
    GenerationPlan,
    DialogueSlots,
    GateAction,
    GateDecision,
    HandoffOffer,
    RetrievalHit,
    ToolCallLog,
    ToolResult,
    ToolStatus,
    UtteranceAtom,
)
from src.gates import POLICY_TOPIC_BY_INTENT
from src.shop.db import connect, ensure_shop_db
from src.tools import ToolHub
from src.dialogue import SKU_RE

from contextlib import closing

INTENT_QUEUE = {
    "COMPLAINT_ESCALATION": "complaint",
    "REFUND_CONFIRM": "refund",
    "REFUND_STATUS": "refund",
    "COMPENSATION": "refund",
    "PRICE_PROTECT": "refund",
    "RETURN_POLICY": "refund",
    "LOGISTICS_TRACK": "logistics",
    "LOGISTICS_ISSUE": "logistics",
    "ORDER_MODIFY": "logistics",
}


class HandoffMissing(Exception):
    """人工入口缺失属于国标义务违反：宁可抛错也不发出去（fail-closed）。"""


class Agent:
    def __init__(self, db_path: Path | str | None = None, kb: KnowledgeBase | None = None,
                 audit: AuditLog | None = None, ruleset: Ruleset | None = None,
                 provider=None, router: IntentRouter | None = None,
                 dialogue: DialogueManager | None = None, close_audit: bool = True):
        self.settings = settings()
        self.db_path = ensure_shop_db(db_path or self.settings.path(self.settings.shop_db_path))
        self.router = router or IntentRouter()
        self.kb = kb or KnowledgeBase(self.db_path)
        self.tools = ToolHub(self.db_path, kb=self.kb)
        self.rules = ruleset or Ruleset()
        self.gates = GateEngine(self.rules)
        self.dialogue = dialogue or DialogueManager()
        self.provider = provider or get_provider(self.settings)
        self.audit = audit or AuditLog(self.settings.path(self.settings.audit_db_path))
        self._close_audit = close_audit
        self.sessions: dict[str, SessionState] = {}

    def close(self) -> None:
        if self._close_audit:
            self.audit.close()

    # ------------------------------------------------------------------ #
    def _session(self, session_id: str | None) -> tuple[str, SessionState]:
        sid = session_id or f"sess-{uuid.uuid4().hex[:10]}"
        st = self.sessions.get(sid)
        if st is None:
            st = SessionState(session_id=sid, slots=DialogueSlots())
            self.sessions[sid] = st
        return sid, st

    def _link_product(self, text: str) -> str | None:
        """把用户口语里的商品名对齐到 SKU（确定性：用商品表做包含匹配，不靠模型猜）。"""
        with closing(connect(self.db_path)) as conn:
            for row in conn.execute("SELECT sku, title, brand, category FROM products").fetchall():
                keys = [row["title"][:6], row["brand"], row["category"], row["sku"]]
                if any(k and normalize(k) in normalize(text) for k in keys):
                    return row["sku"]
        m = SKU_RE.search(text)
        return m.group(1).upper() if m else None

    def _plan_tools(self, code: str, slots: DialogueSlots, text: str) -> list[tuple[str, dict]]:
        calls: list[tuple[str, dict]] = []
        order_needed = code in {"ORDER_STATUS", "REFUND_STATUS", "REFUND_CONFIRM", "ORDER_MODIFY",
                                "PRICE_PROTECT", "COMPLAINT_ESCALATION", "RETURN_POLICY", "COMPENSATION"}
        logi_needed = code in {"LOGISTICS_TRACK", "LOGISTICS_ISSUE", "COMPENSATION"}
        oid = slots.order_id
        mentions_order = bool(re.search(r"订单|这单|该单|那单|ord-", text))
        mentions_logistics = bool(re.search(r"物流|快递|派送|运单|轨迹|签收|发货|到货", text))
        if oid and (order_needed or mentions_order):
            calls.append(("query_order", {"order_id": oid}))
        if oid and (logi_needed or mentions_logistics):
            calls.append(("query_logistics", {"order_id": oid}))
        if code in {"PROD_SPEC", "PROD_RECOMMEND", "PRICE_PROMO", "STOCK_SHIP", "WARRANTY"}:
            sku = slots.sku or self._link_product(text)
            if sku:
                slots.sku = sku
                slots.product_keyword = slots.product_keyword or sku
                calls.append(("lookup_product", {"sku": sku}))
            else:
                kw = next((k for k in ("耳机", "手表", "键盘", "音箱", "电源", "摄像头", "固态", "数据线", "风扇")
                           if k in text), None)
                if kw:
                    slots.product_keyword = kw
                    calls.append(("lookup_product", {"keyword": kw}))
        if code in {"STOCK_SHIP", "SHIP_ETA"} and slots.sku:
            calls.append(("check_stock", {"sku": slots.sku, "region": slots.region}
                          if slots.region else {"sku": slots.sku}))
        return calls

    # ------------------------------------------------------------------ #
    def handle(self, user_text: str, session_id: str | None = None,
               client_request_id: str | None = None) -> AgentResponse:
        t0 = time.monotonic()
        sid, state = self._session(session_id)
        text = normalize(user_text)
        rkey = request_key_of(sid, text, client_request_id)

        replayed = self.audit.existing_response(rkey)
        if replayed:
            return self._replay_response(rkey, replayed, sid, state)

        turn_id = f"turn-{state.turn_no + 1}"
        rewritten, _report = self.dialogue.begin_turn(state, text)
        intent = self.router.classify(rewritten, state.slots)
        defn = self.router.by_code.get(intent.code)
        urgency = self.gates.urgency_hits(rewritten)
        inducements = self.router.detect_promise_inducement(rewritten)
        missing_slot = bool(defn and "order_id" in defn.required_slots and not state.slots.order_id)

        # 工具调用
        tools: dict[str, ToolResult] = {}
        executed: list[tuple[str, dict, ToolResult]] = []
        for name, args in self._plan_tools(intent.code, state.slots, rewritten):
            res = self.tools.call(name, args)
            tools[name] = res
            executed.append((name, args, res))

        # 检索：主题内择优的现行条文（保证可引）+ BM25 全库排序（供 Hit@5 度量与补充证据）
        # 检索式只放用户原话与命中的业务词，不放主题代码（exchange/return 这类 ASCII 词会稀释排序）
        hits: list[RetrievalHit] = []
        topics = POLICY_TOPIC_BY_INTENT.get(intent.code, ())
        state.slots.topic = topics[0] if topics else state.slots.topic
        search_q = f"{rewritten} {' '.join(intent.matched)}".strip()
        if topics:
            called = self.tools.call("query_policy", {"topic": topics[0], "as_of": state.slots.as_of})
            tools["query_policy"] = called
            executed.append(("query_policy", {"topic": topics[0]}, called))
            hits.extend(self.kb.current_hits_for(topics, search_q, as_of=state.slots.as_of))
        ranked = [h for h in self.kb.search(search_q, as_of=state.slots.as_of)
                  if h.entry.citation() not in {x.entry.citation() for x in hits}]
        hits.extend(ranked)

        plan, decisions = self.gates.pre_generation(
            intent, rewritten, hits, {k: v for k, v in tools.items()}, state.slots, urgency,
            inducements=inducements, missing_slot=missing_slot)

        reply, provider_err = self._generate(plan, rewritten, state)

        scan = self.gates.output_scan(plan, reply)
        if scan.blocked:
            decisions.append(GateDecision(
                gate="G-010-output-scan", action=GateAction.BLOCK_COMMIT,
                reason=f"模型输出含白名单外承诺 {len(scan.findings)} 处、未溯源数字 "
                       f"{len(scan.unapproved_numbers)} 处，已替换为白名单原子句",
                stage="output_scan",
                details={"findings": [(f.commit_class, f.excerpt) for f in scan.findings],
                         "numbers": scan.unapproved_numbers[:10]}))
            reply = scan.safe_text

        handoff = self._handoff(state, intent.code, urgency, plan, rkey)
        reply = self._ensure_handoff(reply, handoff, decisions)

        self.dialogue.end_turn(state, reply, intent.code)
        latency = int((time.monotonic() - t0) * 1000)
        flat_cites = sorted({e for a in plan.atoms for e in a.evidence_ids})
        tokens = {"prompt_estimate": estimate_tokens(search_q + reply), "reply_estimate": estimate_tokens(reply)}

        resp = AgentResponse(
            session_id=sid, turn_id=turn_id, request_key=rkey, reply=reply, intent=intent,
            gate_actions=list(dict.fromkeys(d.action for d in decisions)), decisions=decisions, handoff=handoff,
            citations=flat_cites,
            tool_calls=[ToolCallLog(tool=n, args=a, status=r.status, elapsed_ms=r.elapsed_ms,
                                    result_ref=str(r.data.get("result_ref", "")),
                                    numbers=r.numbers, error=r.error) for n, a, r in executed],
            confidence=plan.confidence, must_handoff=plan.must_handoff, degraded=any(r.degraded for _, _, r in executed) or bool(provider_err),
            answerable=not any(d.action is GateAction.FAIL_CLOSED for d in decisions),
            latency_ms=latency, token_estimate=tokens, atoms=plan.atoms)

        self.audit.record(
            request_key=rkey, session_id=sid, turn_id=turn_id, user_text=text, response=resp,
            ruleset_version=self.rules.version,
            evidence=[{"kind": "tool", "ref_id": t.result_ref, "tool": t.tool, "numbers": t.numbers}
                      for t in resp.tool_calls] +
                     [{"kind": "kb", "ref_id": h.entry.citation(), "source": h.entry.source,
                       "valid_until": str(h.entry.valid_until), "hit_score": h.score} for h in hits[:3]],
            kb_versions=[{"entry_id": h.entry.entry_id, "version": h.entry.version,
                          "valid_from": str(h.entry.valid_from), "valid_until": str(h.entry.valid_until),
                          "published_at": str(h.entry.published_at)} for h in hits],
            tool_calls=[asdict(t) for t in resp.tool_calls],
            decisions=[{"gate": d.gate, "action": d.action.value, "reason": d.reason, "stage": d.stage,
                        "details": d.details} for d in decisions],
            blocked_commits=plan.blocked_commits)
        return resp

    # ------------------------------------------------------------------ #
    def _generate(self, plan: GenerationPlan, text: str, state: SessionState) -> tuple[str, str | None]:
        req = LLMRequest(plan=plan, user_text=text, context_block=self.dialogue.context_block(state),
                         citations=sorted({e for a in plan.atoms for e in a.evidence_ids}))
        try:
            out = self.provider.generate(req)
            return out.text, None
        except ProviderError as exc:
            # 外部模型不可用 → 退回确定性模板渲染，并在审计里记一条降级
            return self.gates.render_plan(plan), str(exc)

    def _handoff(self, state, code, urgency, plan, rkey) -> HandoffOffer:
        queue = INTENT_QUEUE.get(code, "presale")
        with closing(connect(self.db_path)) as conn:
            row = conn.execute("SELECT name, on_duty FROM human_agents WHERE queue = ?", (queue,)).fetchone()
            if row is None or not row["on_duty"]:
                row = conn.execute("SELECT name, on_duty FROM human_agents WHERE queue = 'complaint'").fetchone()
            queue_name = row["name"] if row else "值班坐席"
        urgent = bool(urgency) or code == "COMPLAINT_ESCALATION"
        prompt = ("这一单需要人工受理。回复「转人工」立即接入坐席，本轮问题与查询到的订单数据会一并移交。"
                  if urgent else "如需人工，随时回复「转人工」，本轮的依据与数据会一并移交。")
        return HandoffOffer(available=True, prompt=prompt, channel=queue, urgent=urgent,
                            auto_transferred=urgent, queue_hint=f"当前队列：{queue}（{queue_name}）",
                            ticket_ref=f"TK-{rkey[:8]}")

    def _ensure_handoff(self, reply: str, handoff: HandoffOffer, decisions: list[GateDecision]) -> str:
        """F-009 的后置断言：人工出口缺失就补上，补不上则拒绝发送（A3 的机制保证）。"""
        if "人工" not in reply:
            reply = reply + handoff.prompt
            decisions.append(GateDecision(gate="G-009-post-check", action=GateAction.FORCE_HANDOFF,
                                          reason="渲染结果缺少人工出口，已补齐（后置断言命中）"))
            if "人工" not in reply:
                raise HandoffMissing("人工出口无法补齐，拒绝发送该轮回复")
        return reply

    def _replay_response(self, rkey: str, row: dict, sid: str, state: SessionState) -> AgentResponse:
        from src.schemas import IntentPrediction
        import json as _json
        intent = IntentPrediction(code=row["intent_code"], l1=self.router.by_code[row["intent_code"]].l1,
                                  score=row["intent_score"], matched=["replay"])
        decisions = [GateDecision(gate=d.get("gate", ""), action=GateAction(d.get("action", "pass")),
                                  reason=d.get("reason", ""), stage=d.get("stage", "pre_generation"),
                                  details=d.get("details", {}) or {})
                     for d in _json.loads(row["decisions"])]
        handoff = HandoffOffer(**_json.loads(row["handoff"]))
        return AgentResponse(
            session_id=sid, turn_id=row["turn_id"], request_key=rkey, reply=row["reply"], intent=intent,
            gate_actions=[GateAction(a) for a in _json.loads(row["gate_actions"])],
            decisions=decisions, handoff=handoff,
            citations=sorted({e.get("ref_id", "") for e in _json.loads(row["evidence"])
                              if e.get("kind") == "kb" and e.get("ref_id", "") in row["reply"]}),
            tool_calls=[ToolCallLog(tool=t.get("tool", ""), args=t.get("args", {}),
                                    status=ToolStatus(t.get("status", "ok")), elapsed_ms=t.get("elapsed_ms", 0),
                                    result_ref=t.get("result_ref", ""), numbers=t.get("numbers", []))
                        for t in _json.loads(row["tool_calls"])],
            confidence=row["confidence"],
            must_handoff=any(a in ("force_handoff", "explain_only", "block_commit", "soft_answer")
                             for a in _json.loads(row["gate_actions"])),
            replayed=True, degraded=False, answerable=True,
            latency_ms=int(row["latency_ms"]), token_estimate=_json.loads(row["token_estimate"]),
            atoms=[UtteranceAtom(CommitClass.POLICY_CITE, row["reply"])])
