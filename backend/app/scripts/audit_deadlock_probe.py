"""Reproduce concurrent audit appends for one merchant against PostgreSQL.

Two transactions first insert child rows (taking an FK KEY SHARE lock on the
merchant), then append audit events. With a merchant row lock this deadlocks;
the advisory lock must serialize the appends instead.

Run:
    uv run python -m app.scripts.audit_deadlock_probe
"""
from __future__ import annotations

import threading

from sqlalchemy import delete, func, select

from app.db.session import SessionLocal
from app.domain.models import AuditEvent, Customer, Merchant, Notification
from app.services.audit import append_event, verify_chain

WORKERS = 2


def main() -> int:
    with SessionLocal() as session:
        merchant = Merchant(name="audit-lock-probe")
        session.add(merchant)
        session.commit()
        merchant_id = merchant.id

    barrier = threading.Barrier(WORKERS)
    errors: list[str] = []

    def worker(index: int) -> None:
        try:
            with SessionLocal() as session, session.begin():
                customer = Customer(
                    merchant_id=merchant_id,
                    phone=f"+100000{index}",
                    name=f"probe-{index}",
                )
                session.add(customer)
                session.flush()
                session.add(
                    Notification(
                        merchant_id=merchant_id,
                        recipient_type="customer",
                        customer_id=customer.id,
                        type="probe",
                        title=f"probe {index}",
                    )
                )
                session.flush()
                barrier.wait(timeout=10)
                append_event(
                    session,
                    merchant_id,
                    "probe_audit_lock",
                    {"worker": index},
                )
        except Exception as exc:  # noqa: BLE001 - probe reports any failure
            errors.append(f"worker {index}: {type(exc).__name__}: {exc}")

    try:
        threads = [
            threading.Thread(target=worker, args=(index,))
            for index in range(WORKERS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with SessionLocal() as session:
            count = session.execute(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.merchant_id == merchant_id
                )
            ).scalar_one()
            chain_ok, detail = verify_chain(session, merchant_id)
    finally:
        with SessionLocal() as session:
            session.execute(delete(Merchant).where(Merchant.id == merchant_id))
            session.commit()

    if errors:
        print("[audit-deadlock-probe] FAIL")
        for error in errors:
            print(f"  {error}")
        return 1
    if not chain_ok or count != WORKERS:
        print(
            f"[audit-deadlock-probe] FAIL chain={chain_ok} detail={detail} "
            f"events={count}"
        )
        return 1
    print(
        f"[audit-deadlock-probe] OK {WORKERS} concurrent appends, "
        f"chain intact, events={count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
