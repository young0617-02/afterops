from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import Inventory, Product
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.images import (
    delete_image_file,
    image_file_path,
    save_image_file,
    validate_image,
)
from app.services.inventory_alerts import maybe_notify_low_stock
from app.services.inventory_ledger import record_movement, set_inventory_qty

STATUSES = ("draft", "active", "archived")
MAX_GALLERY_IMAGES = 6


@dataclass(frozen=True)
class GalleryImageView:
    storage_name: str
    content_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class InventoryView:
    warehouse_id: str
    warehouse_name: str
    qty_available: int
    qty_reserved: int


@dataclass(frozen=True)
class ProductView:
    sku: str
    title: str
    category: str
    brand: str
    description: str
    specs: str
    price_cents: int
    cost_cents: int
    variant_group: str
    variant_label: str
    support_7d_no_reason: bool
    warranty_months: int
    status: str
    listed_at: date
    image_name: str | None
    image_content_type: str | None
    gallery: tuple[GalleryImageView, ...]
    inventory: tuple[InventoryView, ...]

    @property
    def available_qty(self) -> int:
        return sum(row.qty_available for row in self.inventory)

    @property
    def reserved_qty(self) -> int:
        return sum(row.qty_reserved for row in self.inventory)


def _view(product: Product, inventory: list[Inventory]) -> ProductView:
    return ProductView(
        sku=product.sku,
        title=product.title,
        category=product.category,
        brand=product.brand,
        description=product.description,
        specs=product.specs,
        price_cents=product.price_cents,
        cost_cents=product.cost_cents,
        variant_group=product.variant_group,
        variant_label=product.variant_label,
        support_7d_no_reason=product.support_7d_no_reason,
        warranty_months=product.warranty_months,
        status=product.status,
        listed_at=product.listed_at,
        image_name=product.image_name,
        image_content_type=product.image_content_type,
        gallery=tuple(
            GalleryImageView(
                storage_name=str(item.get("name", "")),
                content_type=str(item.get("content_type", "image/jpeg")),
                size_bytes=int(item.get("size_bytes", 0)),
                sha256=str(item.get("sha256", "")),
            )
            for item in (product.gallery_images or [])
            if item.get("name")
        ),
        inventory=tuple(
            InventoryView(
                warehouse_id=row.warehouse_id,
                warehouse_name=row.warehouse_name,
                qty_available=row.qty_available,
                qty_reserved=row.qty_reserved,
            )
            for row in inventory
        ),
    )


def _inventory_for(
    session: Session, product_ids: list[int]
) -> dict[int, list[Inventory]]:
    if not product_ids:
        return {}
    rows = session.execute(
        select(Inventory)
        .where(Inventory.product_id.in_(product_ids))
        .order_by(Inventory.product_id, Inventory.warehouse_id)
    ).scalars().all()
    grouped: dict[int, list[Inventory]] = {}
    for row in rows:
        grouped.setdefault(row.product_id, []).append(row)
    return grouped


def list_products(
    session: Session,
    merchant_id: int,
    *,
    q: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[ProductView]:
    stmt = select(Product).where(Product.merchant_id == merchant_id)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Product.sku.ilike(pattern),
                Product.title.ilike(pattern),
                Product.brand.ilike(pattern),
                Product.category.ilike(pattern),
                Product.variant_group.ilike(pattern),
                Product.variant_label.ilike(pattern),
            )
        )
    if status:
        if status not in STATUSES:
            raise ValidationError("未知商品状态")
        stmt = stmt.where(Product.status == status)
    products = session.execute(
        stmt.order_by(Product.updated_at.desc(), Product.id.desc()).limit(limit)
    ).scalars().all()
    inventory = _inventory_for(session, [product.id for product in products])
    return [
        _view(product, inventory.get(product.id, [])) for product in products
    ]


def get_product(session: Session, merchant_id: int, sku: str) -> ProductView:
    product = session.execute(
        select(Product).where(
            Product.merchant_id == merchant_id,
            Product.sku == sku,
        )
    ).scalar_one_or_none()
    if product is None:
        raise NotFoundError("商品不存在")
    inventory = _inventory_for(session, [product.id]).get(product.id, [])
    return _view(product, inventory)


def create_product(
    session: Session,
    merchant_id: int,
    user_id: int,
    *,
    sku: str,
    title: str,
    category: str,
    brand: str,
    description: str,
    specs: str,
    price_cents: int,
    cost_cents: int = 0,
    variant_group: str = "",
    variant_label: str = "",
    support_7d_no_reason: bool,
    warranty_months: int,
    status: str,
    initial_qty: int,
) -> ProductView:
    sku_value = sku.strip().upper()
    if not sku_value:
        raise ValidationError("SKU 不能为空")
    if price_cents < 0:
        raise ValidationError("价格不能为负")
    if cost_cents < 0:
        raise ValidationError("成本价不能为负")
    variant_group_value = variant_group.strip()
    variant_label_value = variant_label.strip()
    if len(variant_group_value) > 64:
        raise ValidationError("规格组不能超过 64 个字符")
    if len(variant_label_value) > 80:
        raise ValidationError("规格名称不能超过 80 个字符")
    if not 0 <= warranty_months <= 120:
        raise ValidationError("保修期必须在 0–120 个月之间")
    if initial_qty < 0:
        raise ValidationError("初始库存不能为负")
    if status not in STATUSES:
        raise ValidationError("未知商品状态")
    try:
        existing = session.execute(
            select(Product.id).where(
                Product.merchant_id == merchant_id,
                Product.sku == sku_value,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("SKU 已存在")
        product = Product(
            merchant_id=merchant_id,
            sku=sku_value,
            title=title.strip(),
            category=category.strip(),
            brand=brand.strip(),
            description=description.strip(),
            specs=specs.strip(),
            price_cents=price_cents,
            cost_cents=cost_cents,
            variant_group=variant_group_value,
            variant_label=variant_label_value,
            support_7d_no_reason=support_7d_no_reason,
            warranty_months=warranty_months,
            status=status,
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
            initial_qty,
            "product_created",
            operator_user_id=user_id,
            note="创建商品初始库存",
        )
        if status == "active":
            maybe_notify_low_stock(
                session,
                product,
                inventory.warehouse_id,
                previous_qty=0,
                current_qty=initial_qty,
            )
        append_event(
            session,
            merchant_id,
            "product_created",
            {
                "sku": sku_value,
                "title": product.title,
                "price_cents": price_cents,
                "cost_cents": cost_cents,
                "variant_group": variant_group_value,
                "variant_label": variant_label_value,
                "status": status,
                "initial_qty": initial_qty,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(product, [inventory])
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("SKU 已存在") from exc
    except Exception:
        session.rollback()
        raise


def update_product(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    changes: dict,
) -> ProductView:
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        if "status" in changes and changes["status"] not in STATUSES:
            raise ValidationError("未知商品状态")
        if "price_cents" in changes and changes["price_cents"] < 0:
            raise ValidationError("价格不能为负")
        if "cost_cents" in changes and changes["cost_cents"] < 0:
            raise ValidationError("成本价不能为负")
        if (
            "variant_group" in changes
            and len(changes["variant_group"].strip()) > 64
        ):
            raise ValidationError("规格组不能超过 64 个字符")
        if (
            "variant_label" in changes
            and len(changes["variant_label"].strip()) > 80
        ):
            raise ValidationError("规格名称不能超过 80 个字符")
        if "warranty_months" in changes and not (
            0 <= changes["warranty_months"] <= 120
        ):
            raise ValidationError("保修期必须在 0–120 个月之间")
        changed_fields: list[str] = []
        for field in (
            "title",
            "category",
            "brand",
            "description",
            "specs",
            "price_cents",
            "cost_cents",
            "variant_group",
            "variant_label",
            "support_7d_no_reason",
            "warranty_months",
            "status",
        ):
            if field not in changes:
                continue
            value = changes[field]
            if isinstance(value, str):
                value = value.strip()
            if field == "title" and not value:
                raise ValidationError("商品标题不能为空")
            if getattr(product, field) != value:
                setattr(product, field, value)
                changed_fields.append(field)
        if changed_fields:
            append_event(
                session,
                merchant_id,
                "product_updated",
                {
                    "sku": product.sku,
                    "fields": changed_fields,
                    "price_cents": product.price_cents,
                    "cost_cents": product.cost_cents,
                    "variant_group": product.variant_group,
                    "variant_label": product.variant_label,
                    "status": product.status,
                    "operator_user_id": user_id,
                },
            )
            session.commit()
        inventory = _inventory_for(session, [product.id]).get(product.id, [])
        return _view(product, inventory)
    except Exception:
        session.rollback()
        raise


@dataclass(frozen=True)
class StocktakeResult:
    sku: str
    warehouse_id: str
    before_qty: int
    after_qty: int
    delta: int


def stocktake_inventory(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    warehouse_id: str,
    counted_qty: int,
    note: str = "",
) -> StocktakeResult:
    if counted_qty < 0:
        raise ValidationError("盘点数量不能为负")
    warehouse_value = warehouse_id.strip()
    if not warehouse_value:
        raise ValidationError("仓库编号不能为空")
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        inventory = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == product.id,
                Inventory.warehouse_id == warehouse_value,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if inventory is None:
            raise NotFoundError("库存行不存在")
        before = inventory.qty_available
        delta = counted_qty - before
        if delta != 0:
            set_inventory_qty(
                session,
                inventory,
                counted_qty,
                "stocktake",
                operator_user_id=user_id,
                note=note.strip() or "后台盘点",
            )
        append_event(
            session,
            merchant_id,
            "inventory_stocktake",
            {
                "sku": product.sku,
                "warehouse_id": warehouse_value,
                "from_qty": before,
                "to_qty": inventory.qty_available,
                "delta": inventory.qty_available - before,
                "operator_user_id": user_id,
                "note": note.strip(),
            },
        )
        session.commit()
        return StocktakeResult(
            sku=product.sku,
            warehouse_id=warehouse_value,
            before_qty=before,
            after_qty=inventory.qty_available,
            delta=inventory.qty_available - before,
        )
    except Exception:
        session.rollback()
        raise


@dataclass(frozen=True)
class InventoryTransferResult:
    sku: str
    from_warehouse_id: str
    to_warehouse_id: str
    quantity: int
    from_before_qty: int
    from_after_qty: int
    to_before_qty: int
    to_after_qty: int


def transfer_inventory(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    from_warehouse_id: str,
    to_warehouse_id: str,
    quantity: int,
    note: str = "",
) -> InventoryTransferResult:
    source_value = from_warehouse_id.strip()
    target_value = to_warehouse_id.strip()
    if not source_value or not target_value:
        raise ValidationError("仓库编号不能为空")
    if source_value == target_value:
        raise ValidationError("调出与调入仓库不能相同")
    if quantity <= 0:
        raise ValidationError("调拨数量必须大于 0")
    note_value = note.strip()
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        source = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == product.id,
                Inventory.warehouse_id == source_value,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if source is None:
            raise NotFoundError("调出仓库库存不存在")
        target = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == product.id,
                Inventory.warehouse_id == target_value,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if target is None:
            target = Inventory(
                merchant_id=merchant_id,
                product_id=product.id,
                warehouse_id=target_value,
                warehouse_name=target_value,
                qty_available=0,
            )
            session.add(target)
            session.flush()
        from_before = source.qty_available
        to_before = target.qty_available
        if quantity > from_before:
            raise ConflictError("调出仓可售库存不足")
        record_movement(
            session,
            source,
            -quantity,
            "transfer_out",
            operator_user_id=user_id,
            note=note_value or f"调拨至 {target_value}",
        )
        record_movement(
            session,
            target,
            quantity,
            "transfer_in",
            operator_user_id=user_id,
            note=note_value or f"调拨自 {source_value}",
        )
        maybe_notify_low_stock(
            session,
            product,
            source.warehouse_id,
            previous_qty=from_before,
            current_qty=source.qty_available,
        )
        append_event(
            session,
            merchant_id,
            "inventory_transferred",
            {
                "sku": product.sku,
                "from_warehouse_id": source.warehouse_id,
                "to_warehouse_id": target.warehouse_id,
                "quantity": quantity,
                "from_before_qty": from_before,
                "from_after_qty": source.qty_available,
                "to_before_qty": to_before,
                "to_after_qty": target.qty_available,
                "operator_user_id": user_id,
                "note": note_value,
            },
        )
        session.commit()
        return InventoryTransferResult(
            sku=product.sku,
            from_warehouse_id=source.warehouse_id,
            to_warehouse_id=target.warehouse_id,
            quantity=quantity,
            from_before_qty=from_before,
            from_after_qty=source.qty_available,
            to_before_qty=to_before,
            to_after_qty=target.qty_available,
        )
    except Exception:
        session.rollback()
        raise


def set_inventory(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    warehouse_id: str,
    *,
    qty_available: int,
    warehouse_name: str = "",
) -> ProductView:
    warehouse_value = warehouse_id.strip()
    if not warehouse_value:
        raise ValidationError("仓库编号不能为空")
    if qty_available < 0:
        raise ValidationError("可售库存不能为负")
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        inventory = session.execute(
            select(Inventory)
            .where(
                Inventory.product_id == product.id,
                Inventory.warehouse_id == warehouse_value,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if inventory is None:
            inventory = Inventory(
                merchant_id=merchant_id,
                product_id=product.id,
                warehouse_id=warehouse_value,
                warehouse_name=warehouse_name.strip() or warehouse_value,
                qty_available=0,
            )
            session.add(inventory)
            session.flush()
            previous = 0
        else:
            previous = inventory.qty_available
            if warehouse_name.strip():
                inventory.warehouse_name = warehouse_name.strip()
        set_inventory_qty(
            session,
            inventory,
            qty_available,
            "admin_adjustment",
            operator_user_id=user_id,
            note="后台调整可售库存",
        )
        maybe_notify_low_stock(
            session,
            product,
            inventory.warehouse_id,
            previous_qty=previous,
            current_qty=inventory.qty_available,
        )
        if previous != qty_available or warehouse_name.strip():
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
                },
            )
        session.commit()
        rows = _inventory_for(session, [product.id]).get(product.id, [])
        return _view(product, rows)
    except Exception:
        session.rollback()
        raise


def set_product_image(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    data: bytes,
) -> ProductView:
    content_type = validate_image(data)
    filename = save_image_file("products", data, content_type)
    old_name: str | None = None
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        old_name = product.image_name
        product.image_name = filename
        product.image_content_type = content_type
        append_event(
            session,
            merchant_id,
            "product_image_updated",
            {
                "sku": product.sku,
                "content_type": content_type,
                "size_bytes": len(data),
                "operator_user_id": user_id,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        delete_image_file("products", filename)
        raise
    if old_name:
        delete_image_file("products", old_name)
    rows = _inventory_for(session, [product.id]).get(product.id, [])
    return _view(product, rows)


def clear_product_image(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
) -> ProductView:
    old_name: str | None = None
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        old_name = product.image_name
        if old_name is not None:
            product.image_name = None
            product.image_content_type = None
            append_event(
                session,
                merchant_id,
                "product_image_removed",
                {"sku": product.sku, "operator_user_id": user_id},
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    if old_name:
        delete_image_file("products", old_name)
    rows = _inventory_for(session, [product.id]).get(product.id, [])
    return _view(product, rows)


def product_image_path(
    session: Session,
    merchant_id: int,
    sku: str,
    *,
    active_only: bool = False,
) -> tuple[Path, str]:
    product = session.execute(
        select(Product).where(
            Product.merchant_id == merchant_id,
            Product.sku == sku,
        )
    ).scalar_one_or_none()
    if product is None or not product.image_name:
        raise NotFoundError("商品主图不存在")
    if active_only and product.status != "active":
        raise NotFoundError("商品主图不存在")
    path = image_file_path("products", product.image_name)
    return path, product.image_content_type or "image/jpeg"


def add_product_gallery_image(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    data: bytes,
) -> ProductView:
    content_type = validate_image(data)
    filename = save_image_file("products", data, content_type)
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        gallery = list(product.gallery_images or [])
        if len(gallery) >= MAX_GALLERY_IMAGES:
            raise ConflictError(f"详情图最多 {MAX_GALLERY_IMAGES} 张")
        gallery.append(
            {
                "name": filename,
                "content_type": content_type,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        product.gallery_images = gallery
        append_event(
            session,
            merchant_id,
            "product_gallery_updated",
            {
                "sku": product.sku,
                "action": "added",
                "count": len(gallery),
                "operator_user_id": user_id,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        delete_image_file("products", filename)
        raise
    rows = _inventory_for(session, [product.id]).get(product.id, [])
    return _view(product, rows)


def delete_product_gallery_image(
    session: Session,
    merchant_id: int,
    user_id: int,
    sku: str,
    storage_name: str,
) -> ProductView:
    try:
        product = session.execute(
            select(Product)
            .where(Product.merchant_id == merchant_id, Product.sku == sku)
            .with_for_update()
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("商品不存在")
        gallery = list(product.gallery_images or [])
        remaining = [
            item for item in gallery if item.get("name") != storage_name
        ]
        if len(remaining) == len(gallery):
            raise NotFoundError("详情图不存在")
        product.gallery_images = remaining or None
        append_event(
            session,
            merchant_id,
            "product_gallery_updated",
            {
                "sku": product.sku,
                "action": "removed",
                "count": len(remaining),
                "operator_user_id": user_id,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    delete_image_file("products", storage_name)
    rows = _inventory_for(session, [product.id]).get(product.id, [])
    return _view(product, rows)


def product_gallery_path(
    session: Session,
    merchant_id: int,
    sku: str,
    storage_name: str,
    *,
    active_only: bool = False,
) -> tuple[Path, str]:
    product = session.execute(
        select(Product).where(
            Product.merchant_id == merchant_id,
            Product.sku == sku,
        )
    ).scalar_one_or_none()
    if product is None or (active_only and product.status != "active"):
        raise NotFoundError("商品详情图不存在")
    for item in product.gallery_images or []:
        if item.get("name") == storage_name:
            path = image_file_path("products", storage_name)
            return path, str(item.get("content_type") or "image/jpeg")
    raise NotFoundError("商品详情图不存在")
