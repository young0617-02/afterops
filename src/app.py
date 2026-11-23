"""应用容器：把配置 → 数据 → 模型 → 门禁 → 审计 装配成一个可复用对象。

CLI、HTTP 服务、测试都从这里拿 Agent，避免各处各建一套（之前 `src/cli.py` 直接 new Agent，
加服务后必须有一处统一的生命周期入口：`Application.close()` 负责关审计连接、释放句柄）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.agent import Agent
from src.audit import AuditLog
from src.config import Settings, load_settings, reset_settings, settings
from src.gates import Ruleset
from src.intents import IntentRouter
from src.kb import KnowledgeBase
from src.llm import ProviderError, get_provider
from src.shop.db import build_shop_db, connect, content_hash, ensure_shop_db


class PreflightError(Exception):
    """装配期就能发现的错误（配置缺失、库不可建、规则文件不可解析）。"""


@dataclass
class Application:
    cfg: Settings
    agent: Agent
    started_at: float
    counters: dict[str, int] = field(default_factory=lambda: {
        "requests": 0, "handoff_forced": 0, "commit_blocked_pre": 0, "commit_blocked_post": 0,
        "degraded": 0, "errors": 0})
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------ #
    @classmethod
    def build(cls, env_file: Path | str | None = None, *, db_path: Path | str | None = None,
              audit_db: Path | str | None = None, provider=None, rebuild: bool = False) -> "Application":
        cfg = reset_settings(load_settings(env_file))
        shop = Path(db_path) if db_path else cfg.path(cfg.shop_db_path)
        if rebuild or not Path(shop).exists():
            build_shop_db(shop)
        ensure_shop_db(shop)

        try:
            rules = Ruleset()
            rules.version  # 触发一次读取：文件缺失/不可解析要在这里炸，而不是请求时炸
            router = IntentRouter()
            if not router.intents:
                raise PreflightError("意图体系为空：data/intents/taxonomy.csv 可能被改写")
            kb = KnowledgeBase(shop)
            if not kb.entries:
                raise PreflightError("知识库为空：政策/FAQ 未入库（data/synthetic_shop/kb_seed.sql）")
            audit = AuditLog(Path(audit_db) if audit_db else cfg.path(cfg.audit_db_path))
            prov = provider if provider is not None else get_provider(cfg)
            agent = Agent(db_path=shop, kb=kb, audit=audit, ruleset=rules, provider=prov, router=router)
        except ProviderError as exc:
            raise PreflightError(f"生成器配置不可用：{exc}") from exc

        return Application(cfg=cfg, agent=agent, started_at=time.time())

    # ------------------------------------------------------------------ #
    def handle(self, text: str, session_id: str | None = None,
               client_request_id: str | None = None):
        resp = self.agent.handle(text, session_id=session_id, client_request_id=client_request_id)
        with self._lock:
            self.counters["requests"] += 1
            if resp.degraded:
                self.counters["degraded"] += 1
            for d in resp.decisions:
                if d.gate.startswith("G-010") and d.action.value == "block_commit":
                    key = "commit_blocked_pre" if d.stage == "pre_generation" else "commit_blocked_post"
                    self.counters[key] += 1
                if d.action.value == "force_handoff":
                    self.counters["handoff_forced"] += 1
        return resp

    def health(self) -> dict:
        """健康快照：每个组件都给明确状态，供 /health 与编排器的健康检查共用。"""
        with self._lock:
            try:
                with connect(self.agent.db_path) as conn:
                    orders = conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]
                    entries = conn.execute("SELECT COUNT(*) AS c FROM kb_entries").fetchone()["c"]
                db_ok = orders > 0 and entries > 0
            except Exception as exc:
                return {"status": "unhealthy", "reason": f"数据库不可读：{type(exc).__name__}: {exc}"}
            chain_ok, chain_at = self.agent.audit.verify_chain()
            rows = len(self.agent.audit.rows(limit=5000))
            return {
                "status": "ok" if db_ok and chain_ok else "unhealthy",
                "uptime_s": round(time.time() - self.started_at, 1),
                "provider": getattr(self.agent.provider, "name", "?"),
                "kb": self.agent.kb.stats(),
                "ruleset_version": self.agent.rules.version,
                "shop_content_sha256": content_hash(self.agent.db_path),
                "components": {
                    "shop_db": {"ok": db_ok, "orders": orders, "kb_entries": entries},
                    "audit_db": {"ok": chain_ok, "path": str(self.agent.audit.db_path),
                             "rows": rows, "chain_ok": chain_ok, "chain_broken_at": None if chain_ok else chain_at},
                    "intents": {"ok": True, **self.agent.router.coverage()},
                },
                "counters": dict(self.counters),
            }

    def metrics(self) -> dict:
        return {
            "counters": dict(self.counters),
            "gate_actions": self.agent.audit.counts(),
            "tool_statuses": self.agent.audit.all_tool_statuses(),
            "kb": self.agent.kb.stats(),
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    def close(self) -> None:
        """释放资源：审计连接（含 WAL 落盘）→ 线程池。幂等，重复调用不报错。"""
        try:
            self.agent.audit.close()
        except Exception:
            pass
        try:
            self.agent.tools._pool.shutdown(wait=False)
        except Exception:
            pass


__all__ = ["Application", "PreflightError", "settings"]
