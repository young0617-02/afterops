"""审计留痕（F-011）：幂等键 + 会话级日志 + 哈希链 + 反查规则版本与证据。

幂等键（request_key）= sha256(session_id | 归一化用户输入 | client_request_id?)。
同一请求重放（验收 A11）命中已有记录时**不再重新判定**，直接返回原结论，
并把 replay_count +1，保证「结论一致」而不是「再赌一次」。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from src.config import settings
from src.schemas import AgentResponse

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_turns (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_key    TEXT NOT NULL UNIQUE,
    session_id     TEXT NOT NULL,
    turn_id        TEXT NOT NULL,
    ts             REAL NOT NULL,
    user_text      TEXT NOT NULL,
    intent_code    TEXT NOT NULL,
    intent_score   REAL NOT NULL,
    confidence     REAL NOT NULL,
    gate_actions   TEXT NOT NULL,
    decisions      TEXT NOT NULL,
    tool_calls     TEXT NOT NULL,
    evidence       TEXT NOT NULL,
    blocked_commits TEXT NOT NULL,
    ruleset_version TEXT NOT NULL,
    kb_versions    TEXT NOT NULL,
    reply          TEXT NOT NULL,
    handoff        TEXT NOT NULL,
    replay_count   INTEGER NOT NULL DEFAULT 1,
    latency_ms     INTEGER NOT NULL,
    token_estimate TEXT NOT NULL,
    prev_hash      TEXT NOT NULL,
    row_hash       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_turns(session_id, ts);
"""

COLUMNS = ("request_key", "session_id", "turn_id", "ts", "user_text", "intent_code", "intent_score",
           "confidence", "gate_actions", "decisions", "tool_calls", "evidence", "blocked_commits",
           "ruleset_version", "kb_versions", "reply", "handoff", "latency_ms", "token_estimate")


def _canon(v: Any) -> str:
    if is_dataclass(v):
        v = asdict(v)
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def request_key_of(session_id: str, user_text: str, client_request_id: str | None = None) -> str:
    payload = "|".join([session_id.strip(), " ".join(user_text.split()).lower(), client_request_id or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, db_path: Path | str | None = None):
        s = settings()
        self.db_path = Path(db_path) if db_path else s.path(s.audit_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # HTTP 服务是多线程的：一条连接 + 一把锁（本项目不做吞吐指标，先保证正确性）
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(AUDIT_DDL)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    def find(self, request_key: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM audit_turns WHERE request_key = ?",
                                      (request_key,)).fetchone()

    def existing_response(self, request_key: str) -> dict | None:
        with self._lock:
            row = self.find(request_key)
            if row is None:
                return None
            self._conn.execute("UPDATE audit_turns SET replay_count = replay_count + 1 WHERE request_key = ?",
                               (request_key,))
            self._conn.commit()
            return dict(row)

    def _last_hash(self) -> str:
        row = self._conn.execute("SELECT row_hash FROM audit_turns ORDER BY seq DESC LIMIT 1").fetchone()
        return row["row_hash"] if row else "GENESIS"

    def record(self, *, request_key: str, session_id: str, turn_id: str, user_text: str,
               response: AgentResponse, ruleset_version: str, evidence: list[dict],
               kb_versions: list[dict], tool_calls: list[dict], decisions: list[dict],
               blocked_commits: list[str]) -> str:
        # 整段加锁：前一条 row_hash 与本次插入必须原子，否则哈希链在并发写时会断
        with self._lock:
            prev = self._last_hash()
            values = {
                "request_key": request_key, "session_id": session_id, "turn_id": turn_id, "ts": time.time(),
                "user_text": user_text, "intent_code": response.intent.code,
                "intent_score": response.intent.score, "confidence": response.confidence,
                "gate_actions": _canon([a.value for a in response.gate_actions]),
                "decisions": _canon(decisions), "tool_calls": _canon(tool_calls),
                "evidence": _canon(evidence), "blocked_commits": _canon(blocked_commits),
                "ruleset_version": ruleset_version, "kb_versions": _canon(kb_versions),
                "reply": response.reply, "handoff": _canon(asdict(response.handoff)),
                "latency_ms": response.latency_ms, "token_estimate": _canon(response.token_estimate),
            }
            payload = prev + "".join(f"{k}={values[k]}" for k in COLUMNS)
            row_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            self._conn.execute(
                f"INSERT INTO audit_turns ({', '.join(COLUMNS + ('prev_hash', 'row_hash'))}, replay_count) "
                f"VALUES ({', '.join(['?'] * (len(COLUMNS) + 2))}, 1)",
                tuple(values[c] for c in COLUMNS) + (prev, row_hash),
            )
            self._conn.commit()
            return row_hash

    # ------------------------------------------------------------------ #
    def trace(self, request_key: str) -> dict:
        """反查：某轮承诺/回答对应的规则版本、条文版本与工具证据（F-011/A11）。"""
        row = self.find(request_key)
        if row is None:
            return {"found": False, "request_key": request_key}
        d = dict(row)
        for k in ("gate_actions", "decisions", "tool_calls", "evidence", "blocked_commits",
                  "kb_versions", "handoff", "token_estimate"):
            d[k] = json.loads(d[k])
        d["found"] = True
        return d

    def verify_chain(self) -> tuple[bool, int | str]:
        """校验哈希链；返回 (是否完好, 首个断点 seq 或 'ok')。"""
        prev = "GENESIS"
        with self._lock:
            rows = self._conn.execute("SELECT * FROM audit_turns ORDER BY seq").fetchall()
        for r in rows:
            payload = prev + "".join(f"{k}={r[k]}" for k in COLUMNS)
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != r["row_hash"] or r["prev_hash"] != prev:
                return False, r["seq"]
            prev = r["row_hash"]
        return True, "ok"

    def counts(self) -> dict[str, int]:
        out = {}
        with self._lock:
            rows = self._conn.execute("SELECT gate_actions FROM audit_turns").fetchall()
        for r in rows:
            for a in json.loads(r["gate_actions"]):
                out[a] = out.get(a, 0) + 1
        return out

    def all_tool_statuses(self) -> dict[str, int]:
        out: dict[str, int] = {}
        with self._lock:
            rows = self._conn.execute("SELECT tool_calls FROM audit_turns").fetchall()
        for r in rows:
            for c in json.loads(r["tool_calls"]):
                out[c["status"]] = out.get(c["status"], 0) + 1
        return out

    def rows(self, session_id: str | None = None, limit: int = 1000) -> list[dict]:
        with self._lock:
            if session_id:
                cur = self._conn.execute("SELECT * FROM audit_turns WHERE session_id = ? ORDER BY seq LIMIT ?",
                                         (session_id, limit))
            else:
                cur = self._conn.execute("SELECT * FROM audit_turns ORDER BY seq LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]
