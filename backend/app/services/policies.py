from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.models import PolicyEntry
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError

_ENTRY_ID_RE = re.compile(r"[A-Z0-9][A-Z0-9-]{2,63}")
_TOPIC_RE = re.compile(r"[a-z][a-z0-9_]{1,63}")
_VERSION_RE = re.compile(r"v(\d+)")


@dataclass(frozen=True)
class PolicyGroup:
    entry_id: str
    topic: str
    current: PolicyEntry | None
    versions: tuple[PolicyEntry, ...]


def current_policy(
    session: Session,
    merchant_id: int,
    topic: str,
    as_of: date | None = None,
) -> PolicyEntry:
    day = as_of or date.today()
    entry = session.execute(
        select(PolicyEntry)
        .where(
            PolicyEntry.merchant_id == merchant_id,
            PolicyEntry.topic == topic,
            PolicyEntry.valid_from <= day,
            or_(PolicyEntry.valid_until.is_(None), PolicyEntry.valid_until > day),
        )
        .order_by(PolicyEntry.valid_from.desc(), PolicyEntry.published_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if entry is None:
        raise NotFoundError(f"未找到主题 {topic} 的现行政策")
    return entry


def list_current_policies(
    session: Session,
    merchant_id: int,
    as_of: date | None = None,
) -> list[PolicyEntry]:
    day = as_of or date.today()
    topics = session.execute(
        select(PolicyEntry.topic)
        .where(PolicyEntry.merchant_id == merchant_id)
        .distinct()
        .order_by(PolicyEntry.topic)
    ).scalars().all()
    out: list[PolicyEntry] = []
    for topic in topics:
        try:
            out.append(current_policy(session, merchant_id, topic, day))
        except NotFoundError:
            continue
    return out


def list_policy_groups(
    session: Session,
    merchant_id: int,
    as_of: date | None = None,
) -> list[PolicyGroup]:
    day = as_of or date.today()
    entries = session.execute(
        select(PolicyEntry)
        .where(PolicyEntry.merchant_id == merchant_id)
        .order_by(
            PolicyEntry.entry_id,
            PolicyEntry.valid_from.desc(),
            PolicyEntry.id.desc(),
        )
    ).scalars().all()
    grouped: dict[str, list[PolicyEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.entry_id, []).append(entry)
    groups: list[PolicyGroup] = []
    for entry_versions in grouped.values():
        current = next(
            (
                entry
                for entry in entry_versions
                if entry.valid_from <= day
                and (entry.valid_until is None or entry.valid_until > day)
            ),
            None,
        )
        groups.append(
            PolicyGroup(
                entry_id=entry_versions[0].entry_id,
                topic=entry_versions[0].topic,
                current=current,
                versions=tuple(entry_versions),
            )
        )
    groups.sort(key=lambda group: (group.topic, group.entry_id))
    return groups


def _next_version(versions: list[PolicyEntry]) -> str:
    existing = {entry.version for entry in versions}
    numbers = [
        int(match.group(1))
        for entry in versions
        if (match := _VERSION_RE.fullmatch(entry.version))
    ]
    candidate = max(numbers, default=len(versions)) + 1
    while f"v{candidate}" in existing:
        candidate += 1
    return f"v{candidate}"


def publish_policy_version(
    session: Session,
    merchant_id: int,
    user_id: int,
    *,
    entry_id: str,
    topic: str,
    title: str,
    body: str,
    source: str,
    valid_from: date | None = None,
) -> PolicyEntry:
    entry_value = entry_id.strip().upper()
    topic_value = topic.strip().lower()
    title_value = title.strip()
    body_value = body.strip()
    source_value = source.strip()
    if not all(
        (entry_value, topic_value, title_value, body_value, source_value)
    ):
        raise ValidationError("政策条目、主题、标题、正文与来源均不能为空")
    if _ENTRY_ID_RE.fullmatch(entry_value) is None:
        raise ValidationError("政策条目需为 3–64 位大写字母、数字或连字符")
    if _TOPIC_RE.fullmatch(topic_value) is None:
        raise ValidationError("主题需为 2–64 位小写字母、数字或下划线")
    if len(title_value) > 255:
        raise ValidationError("政策标题不能超过 255 个字符")
    if len(body_value) > 10000:
        raise ValidationError("政策正文不能超过 10000 个字符")
    if len(source_value) > 255:
        raise ValidationError("政策来源不能超过 255 个字符")
    day = valid_from or date.today()
    try:
        versions = session.execute(
            select(PolicyEntry)
            .where(
                PolicyEntry.merchant_id == merchant_id,
                PolicyEntry.entry_id == entry_value,
            )
            .order_by(PolicyEntry.valid_from.desc(), PolicyEntry.id.desc())
            .with_for_update()
        ).scalars().all()
        previous_version: str | None = None
        previous_until: date | None = None
        if versions:
            latest = versions[0]
            if latest.topic != topic_value:
                raise ConflictError("该政策条目的主题不可更改")
            if day <= latest.valid_from:
                raise ConflictError("新版本生效日必须晚于最新版本")
            previous_version = latest.version
            previous_until = latest.valid_until
            if latest.valid_until is None or latest.valid_until > day:
                latest.valid_until = day
        else:
            duplicate = session.execute(
                select(PolicyEntry.id).where(
                    PolicyEntry.merchant_id == merchant_id,
                    PolicyEntry.topic == topic_value,
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise ConflictError(
                    "该主题已有政策条目，请在该条目下发布新版本"
                )
        version = _next_version(versions)
        entry = PolicyEntry(
            merchant_id=merchant_id,
            entry_id=entry_value,
            topic=topic_value,
            title=title_value,
            body=body_value,
            version=version,
            valid_from=day,
            valid_until=None,
            published_at=date.today(),
            source=source_value,
        )
        session.add(entry)
        session.flush()
        append_event(
            session,
            merchant_id,
            "policy_published",
            {
                "entry_id": entry_value,
                "topic": topic_value,
                "version": version,
                "title": title_value,
                "valid_from": day.isoformat(),
                "previous_version": previous_version,
                "previous_valid_until": (
                    previous_until.isoformat() if previous_until else None
                ),
                "operator_user_id": user_id,
            },
        )
        session.commit()
        session.refresh(entry)
        return entry
    except Exception:
        session.rollback()
        raise
