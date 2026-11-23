"""集中配置：单一 .env 入口，零第三方依赖（自带轻量 dotenv 解析）。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | str | None = None, override: bool = False) -> Path | None:
    """加载 .env（不存在则跳过；已存在的环境变量优先，除非 override=True）。

    约定：形如「待填 + 中文冒号 + 说明 + 右中括号」的占位串一律视为未配置，不进入逻辑分支。
    """
    p = Path(path) if path else REPO_ROOT / ".env"
    if not p.exists():
        return None
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val.startswith("#"):
            continue
        if override or key not in os.environ:
            os.environ[key] = val
    return p


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if not raw or raw.startswith("【"):
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if not raw or raw.startswith("【"):
        return default
    try:
        return float(raw)
    except ValueError:
        return default


PLACEHOLDER_RE = re.compile(r"【填：[^】]*】")


def is_placeholder(value: str) -> bool:
    """.env.example 里的【填：…】占位视为「未配置」，不得进入逻辑分支。"""
    return bool(value) and bool(PLACEHOLDER_RE.search(value))


def _env_real(key: str, default: str = "") -> str:
    raw = _env(key, default)
    return "" if is_placeholder(raw) else raw


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "template"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""

    kb_embedding: str = "bm25"
    kb_ttl_seconds: int = 300
    kb_top_k: int = 5
    kb_as_of: str = ""

    tool_timeout_ms: int = 3000
    confidence_threshold: float = 0.55
    urgency_lexicon_path: str = "data/gates/urgency_lexicon.txt"

    context_token_budget: int = 1500
    max_recent_turns: int = 12

    app_host: str = "127.0.0.1"
    app_port: int = 8080
    drain_seconds: int = 5
    ops_services: str = "api"
    ollama_takeover: bool = False
    ollama_bin: str = "ollama"
    shop_db_path: str = "data/synthetic_shop/shop.db"
    audit_db_path: str = "data/synthetic_shop/audit.db"
    shop_category: str = "3C数码"

    @property
    def offline_deterministic(self) -> bool:
        return self.llm_provider == "template"

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else REPO_ROOT / p


def load_settings(env_file: Path | str | None = None) -> Settings:
    load_dotenv(env_file)
    return Settings(
        llm_provider=_env_real("LLM_PROVIDER", "template").lower() or "template",
        ollama_base_url=_env_real("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=_env_real("OLLAMA_MODEL"),
        openai_base_url=_env_real("OPENAI_BASE_URL"),
        openai_api_key=_env_real("OPENAI_API_KEY"),
        openai_model=_env_real("OPENAI_MODEL"),
        kb_embedding=_env_real("KB_EMBEDDING", "bm25").lower() or "bm25",
        kb_ttl_seconds=_env_int("KB_TTL_SECONDS", 300),
        kb_top_k=_env_int("KB_TOP_K", 5),
        kb_as_of=_env_real("KB_AS_OF"),
        tool_timeout_ms=_env_int("TOOL_TIMEOUT_MS", 3000),
        confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", 0.55),
        urgency_lexicon_path=_env_real("URGENCY_LEXICON", "data/gates/urgency_lexicon.txt"),
        context_token_budget=_env_int("CONTEXT_TOKEN_BUDGET", 1500),
        max_recent_turns=_env_int("MAX_RECENT_TURNS", 12),
        app_host=_env_real("APP_HOST", "127.0.0.1"),
        app_port=_env_int("APP_PORT", 8080),
        drain_seconds=_env_int("DRAIN_SECONDS", 5),
        ops_services=_env_real("OPS_SERVICES", "api"),
        ollama_takeover=_env_real("OLLAMA_TAKEOVER", "0") in ("1", "true", "True"),
        ollama_bin=_env_real("OLLAMA_BIN", "ollama"),
        shop_db_path=_env_real("SHOP_DB_PATH", "data/synthetic_shop/shop.db"),
        audit_db_path=_env_real("AUDIT_DB_PATH", "data/synthetic_shop/audit.db"),
        shop_category=_env_real("SHOP_CATEGORY", "3C数码"),
    )


_SETTINGS: Settings | None = None


def settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = load_settings()
    return _SETTINGS


def reset_settings(settings_obj: Settings | None = None) -> Settings:
    """测试注入用：替换全局配置（传 None 则重新从环境读取）。"""
    global _SETTINGS
    _SETTINGS = settings_obj or load_settings()
    return _SETTINGS
