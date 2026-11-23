"""Expire unpaid orders whose inventory reservations have timed out.

The worker runs this on ORDER_EXPIRE_INTERVAL_SECONDS; this entrypoint remains
for manual cleanup or one-off operations.

Run:
    uv run python -m app.scripts.expire_reservations
"""
from __future__ import annotations

from app.db.session import SessionLocal
from app.services.orders import expire_overdue_reservations


def main() -> int:
    with SessionLocal() as session:
        expired = expire_overdue_reservations(session)
    print(f"[expire] 取消 {expired} 个超时未支付订单")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
