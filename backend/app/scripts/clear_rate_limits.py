"""Dev-only helper: clear rate-limit events.

Run:
    uv run python -m app.scripts.clear_rate_limits
"""
from __future__ import annotations

from sqlalchemy import delete

from app.db.session import SessionLocal
from app.domain.models import RateLimitEvent


def main() -> int:
    with SessionLocal() as session:
        result = session.execute(delete(RateLimitEvent))
        session.commit()
        print(f"[rate-limit] 已清理 {result.rowcount} 条限流记录（仅开发/评测使用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
