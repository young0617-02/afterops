from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.models import OtpChallenge
from app.settings import settings


def _hash(phone: str, code: str) -> str:
    payload = f"{phone}:{code}".encode()
    return hmac.new(
        settings.session_secret.encode(), payload, hashlib.sha256
    ).hexdigest()


def issue_code(session: Session, merchant_id: int, phone: str) -> tuple[str, int]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(UTC)
    session.execute(
        update(OtpChallenge)
        .where(
            OtpChallenge.merchant_id == merchant_id,
            OtpChallenge.phone == phone,
            OtpChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    session.add(
        OtpChallenge(
            merchant_id=merchant_id,
            phone=phone,
            code_hash=_hash(phone, code),
            expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
            attempts=0,
            created_at=now,
        )
    )
    session.commit()
    return code, settings.otp_ttl_seconds


def verify_code(session: Session, merchant_id: int, phone: str, code: str) -> bool:
    challenge = session.execute(
        select(OtpChallenge)
        .where(
            OtpChallenge.merchant_id == merchant_id,
            OtpChallenge.phone == phone,
            OtpChallenge.consumed_at.is_(None),
        )
        .order_by(OtpChallenge.id.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if challenge is None:
        return False

    now = datetime.now(UTC)
    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now or challenge.attempts >= settings.otp_max_attempts:
        challenge.consumed_at = now
        session.commit()
        return False

    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_hash, _hash(phone, code)):
        session.commit()
        return False
    challenge.consumed_at = now
    session.commit()
    return True
