"""合成单店数据层（F-004）：建库、读取与可复现性校验。"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from src.config import REPO_ROOT, settings

DATA_DIR = REPO_ROOT / "data" / "synthetic_shop"
SCHEMA_SQL = DATA_DIR / "schema.sql"
SEED_SQL = DATA_DIR / "seed.sql"
KB_SEED_SQL = DATA_DIR / "kb_seed.sql"

TABLES = (
    "products",
    "inventory",
    "orders",
    "order_items",
    "shipments",
    "shipment_events",
    "kb_entries",
    "human_agents",
)
VIEWS = ("v_order_item_total", "v_order_settlement", "v_refund_cap")


def connect(db_path: Path | str | None = None, timeout: float = 2.0) -> sqlite3.Connection:
    p = Path(db_path) if db_path else settings().path(settings().shop_db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _exec_script(conn: sqlite3.Connection, script: Path) -> None:
    conn.executescript(script.read_text(encoding="utf-8"))


def build_shop_db(db_path: Path | str | None = None) -> Path:
    """从 schema.sql + seed.sql + kb_seed.sql 重建库（确定性、可重复执行）。"""
    p = Path(db_path) if db_path else settings().path(settings().shop_db_path)
    if p.exists():
        p.unlink()
    conn = connect(p)
    try:
        _exec_script(conn, SCHEMA_SQL)
        _exec_script(conn, SEED_SQL)
        _exec_script(conn, KB_SEED_SQL)
        conn.commit()
    finally:
        conn.close()
    return p


def ensure_shop_db(db_path: Path | str | None = None) -> Path:
    p = Path(db_path) if db_path else settings().path(settings().shop_db_path)
    if not p.exists():
        build_shop_db(p)
    return p


def content_hash(db_path: Path | str | None = None) -> str:
    """对表内容与视图计算结果做哈希，用于证明「同种子 = 同数据」（验收 A11 前置）。"""
    conn = connect(db_path)
    h = hashlib.sha256()
    try:
        for name in TABLES + VIEWS:
            rows = conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2, 3").fetchall()  # noqa: S608 白名单表名
            for r in rows:
                h.update(name.encode())
                h.update(repr(tuple(r)).encode("utf-8"))
        return h.hexdigest()
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple | dict = (), db_path: Path | str | None = None) -> list[dict[str, Any]]:
    conn = ensure_shop_db(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def fetch_one(sql: str, params: tuple | dict = (), db_path: Path | str | None = None) -> dict[str, Any] | None:
    rows = fetch_all(sql, params, db_path)
    return rows[0] if rows else None


def money(cents: int | None) -> str:
    if cents is None:
        return ""
    return f"{cents / 100:.2f}元"
