"""Run opposite inventory transfers concurrently against PostgreSQL.

The transfer service locks the product row first, so A->B and B->A transfers
for the same SKU serialize instead of deadlocking on inventory rows.

Run:
    DATABASE_URL=postgresql+psycopg://afterops:afterops@127.0.0.1:5433/afterops \
        uv run python -m app.scripts.inventory_transfer_probe
"""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import delete, func, select

from app.db.session import SessionLocal
from app.domain.models import (
    AuditEvent,
    Inventory,
    InventoryMovement,
    Merchant,
    Product,
    User,
)
from app.services.audit import verify_chain
from app.services.products import transfer_inventory

WORKERS = 2
START_QTY = 50
MOVE_QTY = 10


def main() -> int:
    merchant_id: int
    user_id: int
    sku = f"SKU-XFER-PROBE-{uuid.uuid4().hex[:8].upper()}"
    with SessionLocal() as session:
        merchant = Merchant(name="inventory-transfer-probe")
        session.add(merchant)
        session.flush()
        merchant_id = merchant.id
        user = User(
            merchant_id=merchant_id,
            email=f"transfer-probe-{uuid.uuid4().hex[:8]}@local",
            password_hash="not-a-login",
            role="owner",
        )
        session.add(user)
        session.flush()
        user_id = user.id
        product = Product(
            merchant_id=merchant_id,
            sku=sku,
            title="transfer probe",
            price_cents=100,
        )
        session.add(product)
        session.flush()
        session.add_all(
            [
                Inventory(
                    merchant_id=merchant_id,
                    product_id=product.id,
                    warehouse_id="default",
                    warehouse_name="默认仓",
                    qty_available=START_QTY,
                ),
                Inventory(
                    merchant_id=merchant_id,
                    product_id=product.id,
                    warehouse_id="east",
                    warehouse_name="华东仓",
                    qty_available=START_QTY,
                ),
            ]
        )
        session.commit()

    barrier = threading.Barrier(WORKERS)
    errors: list[str] = []

    def worker(index: int) -> None:
        source, target = (
            ("default", "east") if index == 0 else ("east", "default")
        )
        try:
            barrier.wait(timeout=10)
            with SessionLocal() as session:
                transfer_inventory(
                    session,
                    merchant_id,
                    user_id,
                    sku,
                    source,
                    target,
                    MOVE_QTY,
                    note=f"probe {index}",
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
            quantities = {
                row.warehouse_id: row.qty_available
                for row in session.execute(
                    select(Inventory).where(
                        Inventory.merchant_id == merchant_id
                    )
                ).scalars()
            }
            movement_count = session.execute(
                select(func.count(InventoryMovement.id)).where(
                    InventoryMovement.merchant_id == merchant_id
                )
            ).scalar_one()
            audit_count = session.execute(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.merchant_id == merchant_id
                )
            ).scalar_one()
            chain_ok, detail = verify_chain(session, merchant_id)
    finally:
        with SessionLocal() as session:
            session.execute(
                delete(InventoryMovement).where(
                    InventoryMovement.merchant_id == merchant_id
                )
            )
            session.execute(
                delete(AuditEvent).where(AuditEvent.merchant_id == merchant_id)
            )
            session.execute(
                delete(Merchant).where(Merchant.id == merchant_id)
            )
            session.commit()

    expected = {"default": START_QTY, "east": START_QTY}
    if errors:
        print("[transfer-probe] FAIL")
        for error in errors:
            print(f"  {error}")
        return 1
    if quantities != expected:
        print(f"[transfer-probe] FAIL quantities={quantities}")
        return 1
    if movement_count != WORKERS * 2:
        print(f"[transfer-probe] FAIL movements={movement_count}")
        return 1
    if audit_count != WORKERS or not chain_ok:
        print(
            f"[transfer-probe] FAIL audits={audit_count} "
            f"chain={chain_ok} detail={detail}"
        )
        return 1
    print(
        f"[transfer-probe] OK {WORKERS} opposite transfers, "
        f"quantities={quantities}, movements={movement_count}, "
        f"chain intact"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
