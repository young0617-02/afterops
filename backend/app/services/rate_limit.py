from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.domain.models import RateLimitEvent
from app.services.errors import RateLimitError


def check(
    session: Session,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    if limit <= 0:
        return
    normalized = key[:200]
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_seconds)
    session.execute(
        delete(RateLimitEvent).where(
            RateLimitEvent.key == normalized,
            RateLimitEvent.created_at < cutoff,
        )
    )
    count = session.execute(
        select(func.count())
        .select_from(RateLimitEvent)
        .where(
            RateLimitEvent.key == normalized,
            RateLimitEvent.created_at >= cutoff,
        )
    ).scalar_one()
    if count >= limit:
        session.rollback()
        raise RateLimitError(
            f"请求过于频繁，请 {window_seconds} 秒后再试",
            retry_after=window_seconds,
        )
    session.add(RateLimitEvent(key=normalized, created_at=now))
    session.commit()
