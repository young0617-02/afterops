from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import AuditEvent, Merchant
from app.settings import Settings, settings

GENESIS = "GENESIS"


def _anchor_path(cfg: Settings, merchant_id: int) -> Path:
    directory = Path(cfg.audit_anchor_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"audit-anchors-{merchant_id}.jsonl"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _signature(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), _canonical(payload), hashlib.sha256
    ).hexdigest()


def _read_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file():
        return [], 0
    records: list[dict[str, Any]] = []
    errors = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            errors += 1
    return records, errors


def write_audit_anchors(
    session: Session,
    *,
    cfg: Settings | None = None,
    now: datetime | None = None,
) -> int:
    cfg = cfg or settings
    if not cfg.audit_anchor_secret:
        return 0
    now = now or datetime.now(UTC)
    merchant_ids = session.execute(select(Merchant.id)).scalars().all()
    written = 0
    for merchant_id in merchant_ids:
        latest = session.execute(
            select(AuditEvent)
            .where(AuditEvent.merchant_id == merchant_id)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is None:
            continue
        path = _anchor_path(cfg, merchant_id)
        records, _errors = _read_records(path)
        previous = records[-1] if records else None
        if previous is not None and previous.get("audit_id") == latest.id:
            continue
        payload = {
            "merchant_id": merchant_id,
            "audit_id": latest.id,
            "row_hash": latest.row_hash,
            "prev_anchor_hash": (
                str(previous.get("signature")) if previous else GENESIS
            ),
            "anchored_at": now.isoformat(),
        }
        record = {
            **payload,
            "signature": _signature(payload, cfg.audit_anchor_secret),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        written += 1
    return written


def read_audit_anchors(
    session: Session,
    merchant_id: int,
    *,
    cfg: Settings | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    cfg = cfg or settings
    path = _anchor_path(cfg, merchant_id)
    records, file_errors = _read_records(path)
    current = session.execute(
        select(AuditEvent)
        .where(AuditEvent.merchant_id == merchant_id)
        .order_by(AuditEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    anchors: list[dict[str, Any]] = []
    previous_signature = GENESIS
    chain_valid = True
    for record in records:
        payload = {
            key: record.get(key)
            for key in (
                "merchant_id",
                "audit_id",
                "row_hash",
                "prev_anchor_hash",
                "anchored_at",
            )
        }
        signature_valid = bool(
            cfg.audit_anchor_secret
            and hmac.compare_digest(
                str(record.get("signature", "")),
                _signature(payload, cfg.audit_anchor_secret),
            )
        )
        prev_valid = record.get("prev_anchor_hash") == previous_signature
        row = session.get(AuditEvent, record.get("audit_id"))
        row_match = (
            row is not None
            and row.merchant_id == merchant_id
            and row.row_hash == record.get("row_hash")
        )
        valid = signature_valid and prev_valid and row_match
        chain_valid = chain_valid and valid
        anchors.append(
            {
                **payload,
                "signature": record.get("signature"),
                "signature_valid": signature_valid,
                "prev_valid": prev_valid,
                "row_match": row_match,
                "valid": valid,
            }
        )
        previous_signature = str(record.get("signature", ""))

    latest = records[-1] if records else None
    head_covered = bool(
        latest
        and current is not None
        and latest.get("audit_id") == current.id
        and latest.get("row_hash") == current.row_hash
    )
    return {
        "configured": bool(cfg.audit_anchor_secret),
        "count": len(records),
        "chain_valid": chain_valid and bool(records),
        "file_errors": file_errors,
        "anchored_through_id": latest.get("audit_id") if latest else None,
        "current_head_id": current.id if current else None,
        "current_head_hash": current.row_hash if current else None,
        "head_covered": head_covered,
        "anchors": anchors[-limit:],
    }
