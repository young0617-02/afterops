"""Storefront catalog read API."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.api.deps import SessionDep
from app.domain.models import Inventory, Product
from app.domain.money import format_cents
from app.services.errors import NotFoundError
from app.services.merchant import demo_merchant_id
from app.services.products import product_gallery_path, product_image_path

router = APIRouter(prefix="/api/store", tags=["catalog"])


def _money(cents: int) -> str:
    return format_cents(cents)


class InventoryRow(BaseModel):
    warehouse_id: str
    warehouse_name: str
    qty_available: int


class ProductSummary(BaseModel):
    sku: str
    title: str
    category: str
    brand: str
    price_cents: int
    price_display: str
    support_7d_no_reason: bool
    warranty_months: int
    available_qty: int
    in_stock: bool
    listed_at: date
    image_url: str | None
    variant_label: str


class ProductVariantOut(BaseModel):
    sku: str
    title: str
    variant_label: str
    price_cents: int
    price_display: str
    available_qty: int
    in_stock: bool
    image_url: str | None


class ProductDetail(ProductSummary):
    description: str
    specs: str
    gallery_urls: list[str]
    inventory: list[InventoryRow]
    variants: list[ProductVariantOut]


class CategoryCount(BaseModel):
    category: str
    count: int


@router.get("/products", response_model=list[ProductSummary])
def list_products(
    session: SessionDep,
    q: str | None = Query(default=None, max_length=80),
    category: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ProductSummary]:
    merchant_id = demo_merchant_id(session)
    available = func.coalesce(func.sum(Inventory.qty_available), 0).label("available_qty")
    stmt = (
        select(Product, available)
        .outerjoin(Inventory, Inventory.product_id == Product.id)
        .where(Product.merchant_id == merchant_id, Product.status == "active")
        .group_by(Product.id)
        .order_by(Product.listed_at.desc(), Product.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Product.title.ilike(pattern),
                Product.sku.ilike(pattern),
                Product.brand.ilike(pattern),
                Product.category.ilike(pattern),
                Product.variant_group.ilike(pattern),
                Product.variant_label.ilike(pattern),
            )
        )
    if category:
        stmt = stmt.where(Product.category == category)

    rows = session.execute(stmt).all()
    return [
        ProductSummary(
            sku=product.sku,
            title=product.title,
            category=product.category,
            brand=product.brand,
            price_cents=product.price_cents,
            price_display=_money(product.price_cents),
            support_7d_no_reason=product.support_7d_no_reason,
            warranty_months=product.warranty_months,
            available_qty=int(qty or 0),
            in_stock=bool(qty),
            listed_at=product.listed_at,
            image_url=(
                f"/api/store/products/{product.sku}/image?v={product.image_name}"
                if product.image_name
                else None
            ),
            variant_label=product.variant_label,
        )
        for product, qty in rows
    ]


@router.get("/products/{sku}", response_model=ProductDetail)
def get_product(sku: str, session: SessionDep) -> ProductDetail:
    merchant_id = demo_merchant_id(session)
    product = session.execute(
        select(Product).where(
            Product.merchant_id == merchant_id,
            Product.sku == sku,
            Product.status == "active",
        )
    ).scalar_one_or_none()
    if product is None:
        raise NotFoundError("商品不存在")

    inventory = session.execute(
        select(Inventory)
        .where(Inventory.product_id == product.id)
        .order_by(Inventory.warehouse_id)
    ).scalars().all()
    available_qty = sum(row.qty_available for row in inventory)
    variants: list[ProductVariantOut] = []
    if product.variant_group:
        available = func.coalesce(
            func.sum(Inventory.qty_available), 0
        ).label("available_qty")
        sibling_rows = session.execute(
            select(Product, available)
            .outerjoin(Inventory, Inventory.product_id == Product.id)
            .where(
                Product.merchant_id == merchant_id,
                Product.status == "active",
                Product.variant_group == product.variant_group,
            )
            .group_by(Product.id)
            .order_by(Product.variant_label, Product.sku)
        ).all()
        variants = [
            ProductVariantOut(
                sku=sibling.sku,
                title=sibling.title,
                variant_label=sibling.variant_label,
                price_cents=sibling.price_cents,
                price_display=_money(sibling.price_cents),
                available_qty=int(qty or 0),
                in_stock=bool(qty),
                image_url=(
                    f"/api/store/products/{sibling.sku}/image"
                    f"?v={sibling.image_name}"
                    if sibling.image_name
                    else None
                ),
            )
            for sibling, qty in sibling_rows
        ]
    return ProductDetail(
        sku=product.sku,
        title=product.title,
        category=product.category,
        brand=product.brand,
        price_cents=product.price_cents,
        price_display=_money(product.price_cents),
        support_7d_no_reason=product.support_7d_no_reason,
        warranty_months=product.warranty_months,
        available_qty=available_qty,
        in_stock=available_qty > 0,
        listed_at=product.listed_at,
        image_url=(
            f"/api/store/products/{product.sku}/image?v={product.image_name}"
            if product.image_name
            else None
        ),
        variant_label=product.variant_label,
        variants=variants,
        description=product.description,
        specs=product.specs,
        gallery_urls=[
            f"/api/store/products/{product.sku}/images/{item['name']}"
            for item in (product.gallery_images or [])
            if item.get("name")
        ],
        inventory=[
            InventoryRow(
                warehouse_id=row.warehouse_id,
                warehouse_name=row.warehouse_name,
                qty_available=row.qty_available,
            )
            for row in inventory
        ],
    )


@router.get("/products/{sku}/image")
def get_product_image(sku: str, session: SessionDep) -> FileResponse:
    merchant_id = demo_merchant_id(session)
    path, content_type = product_image_path(
        session, merchant_id, sku, active_only=True
    )
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/products/{sku}/images/{storage_name}")
def get_product_gallery_image(
    sku: str, storage_name: str, session: SessionDep
) -> FileResponse:
    merchant_id = demo_merchant_id(session)
    path, content_type = product_gallery_path(
        session,
        merchant_id,
        sku,
        storage_name,
        active_only=True,
    )
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/categories", response_model=list[CategoryCount])
def list_categories(session: SessionDep) -> list[CategoryCount]:
    merchant_id = demo_merchant_id(session)
    stmt = (
        select(Product.category, func.count(Product.id).label("count"))
        .where(Product.merchant_id == merchant_id, Product.status == "active")
        .group_by(Product.category)
        .order_by(Product.category)
    )
    return [CategoryCount(category=category, count=count) for category, count in session.execute(stmt).all()]
