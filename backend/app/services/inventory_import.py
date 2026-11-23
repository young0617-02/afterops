from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Inventory, Product
from app.services.audit import append_event
from app.services.errors import ValidationError
from app.services.inventory_alerts import maybe_notify_low_stock
from app.services.inventory_ledger import set_inventory_qty

MAX_ROWS = 500


@dataclass(frozen=True)
class InventoryImportResult:
    updated: int
    changed: int
    skus: tuple[str, ...]


def import_inventory_csv(
    session: Session,
    merchant_id: int,
    user_id: int,
    data: bytes,
) -> InventoryImportResult:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("CSV 需为 UTF-8 编码") from exc
    reader = csv.DictReader(io.StringIO(text))
    raw_headers = reader.fieldnames or []
    headers = [header.strip().lower() for header in raw_headers]
    if len(set(headers)) != len(headers):
        raise ValidationError("CSV 表头重复")
    required = {"sku", "warehouse_id", "qty_available"}
    missing = sorted(required - set(headers))
    if missing:
        raise ValidationError(f"CSV 缺少列：{', '.join(missing)}")
    reader.fieldnames = headers

    rows: list[dict] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in raw.values()):
            continue
        if len(rows) >= MAX_ROWS:
            raise ValidationError(f"单次最多导入 {MAX_ROWS} 行")
        try:
            sku = str(raw.get("sku") or "").strip().upper()
            if not sku:
                raise ValueError("SKU 不能为空")
            warehouse_id = str(raw.get("warehouse_id") or "").strip()
            if not warehouse_id:
                raise ValueError("仓库编号不能为空")
            if len(warehouse_id) > 64:
                raise ValueError("仓库编号不能超过 64 个字符")
            warehouse_name = str(raw.get("warehouse_name") or "").strip()
            if len(warehouse_name) > 120:
                raise ValueError("仓库名称不能超过 120 个字符")
            try:
                qty_available = int(
                    str(raw.get("qty_available") or "").strip()
                )
            except ValueError as exc:
                raise ValueError("可售库存必须是整数") from exc
            if qty_available < 0 or qty_available > 1_000_000:
                raise ValueError("可售库存需在 0–1,000,000 之间")
            key = (sku, warehouse_id)
            if key in seen:
                raise ValueError(f"文件内库存行重复：{sku} / {warehouse_id}")
            seen.add(key)
            rows.append(
                {
                    "sku": sku,
                    "warehouse_id": warehouse_id,
                    "warehouse_name": warehouse_name,
                    "qty_available": qty_available,
                }
            )
        except ValueError as exc:
            errors.append(f"第 {index} 行：{exc}")
    if not rows and not errors:
        raise ValidationError("CSV 没有库存数据")
    if errors:
        detail = "；".join(errors[:20])
        if len(errors) > 20:
            detail += f"；另有 {len(errors) - 20} 行错误"
        raise ValidationError(detail)

    products = {
        product.sku: product
        for product in session.execute(
            select(Product).where(
                Product.merchant_id == merchant_id,
                Product.sku.in_([row["sku"] for row in rows]),
            )
        ).scalars().all()
    }
    missing_skus = sorted({row["sku"] for row in rows} - set(products))
    if missing_skus:
        raise ValidationError("商品不存在：" + "、".join(missing_skus))

    rows.sort(key=lambda row: (row["sku"], row["warehouse_id"]))
    updated = 0
    changed = 0
    for row in rows:
        product = session.execute(
            select(Product)
            .where(Product.id == products[row["sku"]].id)
            .with_for_update()
        ).scalar_one()
        inventory = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == product.id,
                Inventory.warehouse_id == row["warehouse_id"],
            )
            .with_for_update()
        ).scalar_one_or_none()
        if inventory is None:
            inventory = Inventory(
                merchant_id=merchant_id,
                product_id=product.id,
                warehouse_id=row["warehouse_id"],
                warehouse_name=(
                    row["warehouse_name"] or row["warehouse_id"]
                ),
                qty_available=0,
            )
            session.add(inventory)
            session.flush()
            previous = 0
        else:
            previous = inventory.qty_available
            if row["warehouse_name"]:
                inventory.warehouse_name = row["warehouse_name"]
        set_inventory_qty(
            session,
            inventory,
            row["qty_available"],
            "admin_adjustment",
            operator_user_id=user_id,
            note="批量导入库存",
        )
        maybe_notify_low_stock(
            session,
            product,
            inventory.warehouse_id,
            previous_qty=previous,
            current_qty=inventory.qty_available,
        )
        append_event(
            session,
            merchant_id,
            "inventory_updated",
            {
                "sku": product.sku,
                "warehouse_id": inventory.warehouse_id,
                "from_qty": previous,
                "to_qty": inventory.qty_available,
                "delta": inventory.qty_available - previous,
                "operator_user_id": user_id,
                "source": "csv_import",
            },
        )
        updated += 1
        if inventory.qty_available != previous:
            changed += 1
    append_event(
        session,
        merchant_id,
        "inventory_imported",
        {
            "updated": updated,
            "changed": changed,
            "skus": sorted(products),
            "operator_user_id": user_id,
        },
    )
    session.commit()
    return InventoryImportResult(
        updated=updated,
        changed=changed,
        skus=tuple(sorted(products)),
    )