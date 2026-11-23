from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import Inventory, Product
from app.services.audit import append_event
from app.services.errors import ConflictError, ValidationError
from app.services.inventory_alerts import maybe_notify_low_stock
from app.services.inventory_ledger import record_movement

MAX_ROWS = 200
SKU_RE = re.compile(r"^[A-Za-z0-9-]{3,64}$")
STATUSES = ("draft", "active", "archived")
TRUE_VALUES = {"1", "true", "yes", "y", "是"}
FALSE_VALUES = {"0", "false", "no", "n", "否"}


@dataclass(frozen=True)
class ProductImportResult:
    created: int
    skus: tuple[str, ...]


def _parse_bool(value: str, default: bool = True) -> bool:
    text = value.strip().lower()
    if not text:
        return default
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError("支持 7 天无理由需为 true/false")


def _parse_price(value: str) -> int:
    try:
        amount = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError("价格无效") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("价格不能为负")
    cents = int((amount * 100).to_integral_value())
    if cents > 100_000_000:
        raise ValueError("价格超出上限")
    return cents


def _parse_int(
    value: str, default: int, minimum: int, maximum: int, label: str
) -> int:
    text = value.strip()
    if not text:
        return default
    try:
        number = int(text)
    except ValueError as exc:
        raise ValueError(f"{label}必须是整数") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{label}需在 {minimum}–{maximum} 之间")
    return number


def import_products_csv(
    session: Session,
    merchant_id: int,
    user_id: int,
    data: bytes,
) -> ProductImportResult:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("CSV 需为 UTF-8 编码") from exc
    reader = csv.DictReader(io.StringIO(text))
    raw_headers = reader.fieldnames or []
    headers = [header.strip().lower() for header in raw_headers]
    if len(set(headers)) != len(headers):
        raise ValidationError("CSV 表头重复")
    required = {"sku", "title", "price"}
    missing = sorted(required - set(headers))
    if missing:
        raise ValidationError(f"CSV 缺少列：{', '.join(missing)}")
    reader.fieldnames = headers

    rows: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in raw.values()):
            continue
        if len(rows) >= MAX_ROWS:
            raise ValidationError(f"单次最多导入 {MAX_ROWS} 行")
        try:
            sku = str(raw.get("sku") or "").strip().upper()
            title = str(raw.get("title") or "").strip()
            if not SKU_RE.fullmatch(sku):
                raise ValueError("SKU 需为 3–64 位字母/数字/短横线")
            if sku in seen:
                raise ValueError(f"文件内 SKU 重复：{sku}")
            if not title:
                raise ValueError("标题不能为空")
            if len(title) > 255:
                raise ValueError("标题不能超过 255 个字符")
            price_cents = _parse_price(str(raw.get("price") or ""))
            cost_cents = _parse_price(
                str(raw.get("cost") or "").strip() or "0"
            )
            variant_group = str(raw.get("variant_group") or "").strip()
            variant_label = str(raw.get("variant_label") or "").strip()
            if len(variant_group) > 64:
                raise ValueError("规格组不能超过 64 个字符")
            if len(variant_label) > 80:
                raise ValueError("规格名称不能超过 80 个字符")
            initial_qty = _parse_int(
                str(raw.get("initial_qty") or ""), 0, 0, 1_000_000, "初始库存"
            )
            warranty_months = _parse_int(
                str(raw.get("warranty_months") or ""),
                12,
                0,
                120,
                "保修月数",
            )
            status = str(raw.get("status") or "").strip() or "active"
            if status not in STATUSES:
                raise ValueError("状态需为 draft/active/archived")
            support = _parse_bool(
                str(raw.get("support_7d_no_reason") or "")
            )
            category = str(raw.get("category") or "").strip()
            brand = str(raw.get("brand") or "").strip()
            if len(category) > 80 or len(brand) > 80:
                raise ValueError("类目或品牌不能超过 80 个字符")
            seen.add(sku)
            rows.append(
                {
                    "sku": sku,
                    "title": title,
                    "category": category,
                    "brand": brand,
                    "price_cents": price_cents,
                    "cost_cents": cost_cents,
                    "variant_group": variant_group,
                    "variant_label": variant_label,
                    "initial_qty": initial_qty,
                    "warranty_months": warranty_months,
                    "status": status,
                    "support": support,
                }
            )
        except ValueError as exc:
            errors.append(f"第 {index} 行：{exc}")
    if not rows and not errors:
        raise ValidationError("CSV 没有商品数据")
    if errors:
        detail = "；".join(errors[:20])
        if len(errors) > 20:
            detail += f"；另有 {len(errors) - 20} 行错误"
        raise ValidationError(detail)

    existing = set(
        session.execute(
            select(Product.sku).where(
                Product.merchant_id == merchant_id,
                Product.sku.in_([row["sku"] for row in rows]),
            )
        ).scalars()
    )
    if existing:
        raise ConflictError(
            "SKU 已存在：" + "、".join(sorted(existing))
        )

    try:
        created_skus: list[str] = []
        for row in rows:
            product = Product(
                merchant_id=merchant_id,
                sku=row["sku"],
                title=row["title"],
                category=row["category"],
                brand=row["brand"],
                price_cents=row["price_cents"],
                cost_cents=row["cost_cents"],
                variant_group=row["variant_group"],
                variant_label=row["variant_label"],
                support_7d_no_reason=row["support"],
                warranty_months=row["warranty_months"],
                status=row["status"],
                listed_at=date.today(),
            )
            session.add(product)
            session.flush()
            inventory = Inventory(
                merchant_id=merchant_id,
                product_id=product.id,
                warehouse_id="default",
                warehouse_name="默认仓",
                qty_available=0,
            )
            session.add(inventory)
            session.flush()
            record_movement(
                session,
                inventory,
                row["initial_qty"],
                "product_created",
                operator_user_id=user_id,
                note="批量导入初始库存",
            )
            if product.status == "active":
                maybe_notify_low_stock(
                    session,
                    product,
                    inventory.warehouse_id,
                    previous_qty=0,
                    current_qty=row["initial_qty"],
                )
            append_event(
                session,
                merchant_id,
                "product_created",
                {
                    "sku": product.sku,
                    "title": product.title,
                    "price_cents": product.price_cents,
                    "cost_cents": product.cost_cents,
                    "variant_group": product.variant_group,
                    "variant_label": product.variant_label,
                    "status": product.status,
                    "initial_qty": row["initial_qty"],
                    "operator_user_id": user_id,
                    "source": "csv_import",
                },
            )
            created_skus.append(product.sku)
        append_event(
            session,
            merchant_id,
            "products_imported",
            {
                "count": len(created_skus),
                "skus": created_skus,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return ProductImportResult(
            created=len(created_skus),
            skus=tuple(created_skus),
        )
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("SKU 已存在，导入未执行") from exc
    except Exception:
        session.rollback()
        raise