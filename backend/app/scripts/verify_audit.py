"""Verify the per-merchant audit hash chain.

Run:
    uv run python -m app.scripts.verify_audit
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.session import SessionLocal
from app.domain.models import Merchant
from app.services.audit import verify_chain


def main() -> int:
    failures = 0
    with SessionLocal() as session:
        merchants = session.execute(select(Merchant).order_by(Merchant.id)).scalars().all()
        for merchant in merchants:
            ok, detail = verify_chain(session, merchant.id)
            suffix = "OK" if ok else f"BROKEN at event_id={detail}"
            print(f"[audit] merchant={merchant.id} {merchant.name}: {suffix}")
            failures += 0 if ok else 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
