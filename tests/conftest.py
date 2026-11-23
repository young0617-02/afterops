"""pytest 公共夹具：每个用例独立的审计库，店铺库用仓库内的种子库（只读使用）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.config import reset_settings  # noqa: E402
from src.shop.db import build_shop_db, ensure_shop_db  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """默认配置：template provider（离线确定性）+ 独立审计库 + 独立店铺库。"""
    shop = tmp_path / "shop.db"
    build_shop_db(shop)
    monkeypatch.setenv("SHOP_DB_PATH", str(shop))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("LLM_PROVIDER", "template")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.55")
    monkeypatch.setenv("TOOL_TIMEOUT_MS", "3000")
    reset_settings()
    yield {"tmp": tmp_path, "shop": shop}
    reset_settings()


@pytest.fixture()
def shop_db(env):
    ensure_shop_db(env["shop"])
    return env["shop"]


@pytest.fixture()
def agent(env):
    from src.agent import Agent

    a = Agent(db_path=env["shop"])
    yield a
    a.close()


@pytest.fixture()
def agent_factory(env):
    """同一审计库上多次构造 Agent，用于验证跨进程重放（A11）。"""
    created = []

    def make(**kw):
        from src.agent import Agent

        a = Agent(db_path=env["shop"], **kw)
        created.append(a)
        return a

    yield make
    for a in created:
        a.close()
