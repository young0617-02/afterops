from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import SessionDep
from app.domain.money import format_cents
from app.services.errors import ValidationError
from app.services.product_import import import_products_csv
from app.services.products import (
    ProductView,
    add_product_gallery_image,
    clear_product_image,
    create_product,
    delete_product_gallery_image,
    get_product,
    list_products,
    product_gallery_path,
    product_image_path,
    set_inventory,
    set_product_image,
    stocktake_inventory,
    transfer_inventory,
    update_product,
)
from app.settings import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminInventoryOut(BaseModel):
    warehouse_id: str
    warehouse_name: str
    qty_available: int
    qty_reserved: int


class AdminGalleryOut(BaseModel):
    storage_name: str
    content_type: str
    size_bytes: int
    url: str


class AdminProductOut(BaseModel):
    sku: str
    title: str
    category: str
    brand: str
    description: str
    specs: str
    price_cents: int
    price_display: str
    cost_cents: int | None
    cost_display: str | None
    variant_group: str
    variant_label: str
    support_7d_no_reason: bool
    warranty_months: int
    status: str
    listed_at: date
    available_qty: int
    reserved_qty: int
    image_url: str | None
    gallery: list[AdminGalleryOut]
    inventory: list[AdminInventoryOut]


class CreateProductIn(BaseModel):
    sku: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(default="", max_length=80)
    brand: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=5000)
    specs: str = Field(default="", max_length=5000)
    price_cents: int = Field(ge=0, le=100_000_000)
    cost_cents: int = Field(default=0, ge=0, le=100_000_000)
    variant_group: str = Field(default="", max_length=64)
    variant_label: str = Field(default="", max_length=80)
    support_7d_no_reason: bool = True
    warranty_months: int = Field(default=12, ge=0, le=120)
    status: Literal["draft", "active", "archived"] = "active"
    initial_qty: int = Field(default=0, ge=0, le=1_000_000)


class UpdateProductIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    specs: str | None = Field(default=None, max_length=5000)
    price_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    cost_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    variant_group: str | None = Field(default=None, max_length=64)
    variant_label: str | None = Field(default=None, max_length=80)
    support_7d_no_reason: bool | None = None
    warranty_months: int | None = Field(default=None, ge=0, le=120)
    status: Literal["draft", "active", "archived"] | None = None


class InventoryUpdateIn(BaseModel):
    qty_available: int = Field(ge=0, le=1_000_000)
    warehouse_name: str = Field(default="", max_length=120)


class StocktakeIn(BaseModel):
    counted_qty: int = Field(ge=0, le=1_000_000)
    note: str = Field(default="", max_length=255)


class StocktakeOut(BaseModel):
    sku: str
    warehouse_id: str
    before_qty: int
    after_qty: int
    delta: int


class TransferInventoryIn(BaseModel):
    from_warehouse_id: str = Field(min_length=1, max_length=64)
    to_warehouse_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=1_000_000)
    note: str = Field(default="", max_length=255)


class TransferInventoryOut(BaseModel):
    sku: str
    from_warehouse_id: str
    to_warehouse_id: str
    quantity: int
    from_before_qty: int
    from_after_qty: int
    to_before_qty: int
    to_after_qty: int


class ImportProductsOut(BaseModel):
    created: int
    skus: list[str]


def _out(
    view: ProductView, *, include_cost: bool = True
) -> AdminProductOut:
    return AdminProductOut(
        sku=view.sku,
        title=view.title,
        category=view.category,
        brand=view.brand,
        description=view.description,
        specs=view.specs,
        price_cents=view.price_cents,
        price_display=format_cents(view.price_cents),
        cost_cents=view.cost_cents if include_cost else None,
        cost_display=(
            format_cents(view.cost_cents) if include_cost else None
        ),
        variant_group=view.variant_group,
        variant_label=view.variant_label,
        support_7d_no_reason=view.support_7d_no_reason,
        warranty_months=view.warranty_months,
        status=view.status,
        listed_at=view.listed_at,
        available_qty=view.available_qty,
        reserved_qty=view.reserved_qty,
        image_url=(
            f"/api/admin/products/{view.sku}/image?v={view.image_name}"
            if view.image_name
            else None
        ),
        gallery=[
            AdminGalleryOut(
                storage_name=image.storage_name,
                content_type=image.content_type,
                size_bytes=image.size_bytes,
                url=(
                    f"/api/admin/products/{view.sku}/images/"
                    f"{image.storage_name}"
                ),
            )
            for image in view.gallery
        ],
        inventory=[
            AdminInventoryOut(
                warehouse_id=row.warehouse_id,
                warehouse_name=row.warehouse_name,
                qty_available=row.qty_available,
                qty_reserved=row.qty_reserved,
            )
            for row in view.inventory
        ],
    )


@router.get("/products", response_model=list[AdminProductOut])
def admin_products(
    admin: AdminDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=80)] = None,
    status: Annotated[
        Literal["draft", "active", "archived"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AdminProductOut]:
    return [
        _out(view, include_cost=admin.role == "owner")
        for view in list_products(
            session, admin.merchant_id, q=q, status=status, limit=limit
        )
    ]


@router.get("/products/{sku}", response_model=AdminProductOut)
def admin_product_detail(
    sku: str, admin: AdminDep, session: SessionDep
) -> AdminProductOut:
    return _out(
        get_product(session, admin.merchant_id, sku),
        include_cost=admin.role == "owner",
    )


@router.post("/products/import", response_model=ImportProductsOut)
async def admin_products_import(
    file: Annotated[UploadFile, File()],
    admin: OwnerDep,
    session: SessionDep,
) -> ImportProductsOut:
    data = await file.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValidationError("CSV 不能超过 1MB")
    result = import_products_csv(
        session,
        admin.merchant_id,
        admin.user_id,
        data,
    )
    return ImportProductsOut(
        created=result.created,
        skus=list(result.skus),
    )


@router.post("/products", response_model=AdminProductOut)
def admin_product_create(
    payload: CreateProductIn,
    admin: OwnerDep,
    session: SessionDep,
) -> AdminProductOut:
    view = create_product(
        session,
        admin.merchant_id,
        admin.user_id,
        sku=payload.sku,
        title=payload.title,
        category=payload.category,
        brand=payload.brand,
        description=payload.description,
        specs=payload.specs,
        price_cents=payload.price_cents,
        cost_cents=payload.cost_cents,
        variant_group=payload.variant_group,
        variant_label=payload.variant_label,
        support_7d_no_reason=payload.support_7d_no_reason,
        warranty_months=payload.warranty_months,
        status=payload.status,
        initial_qty=payload.initial_qty,
    )
    return _out(view)


@router.patch("/products/{sku}", response_model=AdminProductOut)
def admin_product_update(
    sku: str,
    payload: UpdateProductIn,
    admin: OwnerDep,
    session: SessionDep,
) -> AdminProductOut:
    return _out(
        update_product(
            session,
            admin.merchant_id,
            admin.user_id,
            sku,
            payload.model_dump(exclude_unset=True),
        )
    )


@router.post(
    "/products/{sku}/inventory/{warehouse_id}/stocktake",
    response_model=StocktakeOut,
)
def admin_stocktake_inventory(
    sku: str,
    warehouse_id: str,
    payload: StocktakeIn,
    admin: OwnerDep,
    session: SessionDep,
) -> StocktakeOut:
    result = stocktake_inventory(
        session,
        admin.merchant_id,
        admin.user_id,
        sku,
        warehouse_id,
        payload.counted_qty,
        payload.note,
    )
    return StocktakeOut(
        sku=result.sku,
        warehouse_id=result.warehouse_id,
        before_qty=result.before_qty,
        after_qty=result.after_qty,
        delta=result.delta,
    )


@router.post(
    "/products/{sku}/inventory/transfer",
    response_model=TransferInventoryOut,
)
def admin_transfer_inventory(
    sku: str,
    payload: TransferInventoryIn,
    admin: OwnerDep,
    session: SessionDep,
) -> TransferInventoryOut:
    result = transfer_inventory(
        session,
        admin.merchant_id,
        admin.user_id,
        sku,
        payload.from_warehouse_id,
        payload.to_warehouse_id,
        payload.quantity,
        payload.note,
    )
    return TransferInventoryOut(
        sku=result.sku,
        from_warehouse_id=result.from_warehouse_id,
        to_warehouse_id=result.to_warehouse_id,
        quantity=result.quantity,
        from_before_qty=result.from_before_qty,
        from_after_qty=result.from_after_qty,
        to_before_qty=result.to_before_qty,
        to_after_qty=result.to_after_qty,
    )


@router.put(
    "/products/{sku}/inventory/{warehouse_id}", response_model=AdminProductOut
)
def admin_product_inventory(
    sku: str,
    warehouse_id: str,
    payload: InventoryUpdateIn,
    admin: OwnerDep,
    session: SessionDep,
) -> AdminProductOut:
    return _out(
        set_inventory(
            session,
            admin.merchant_id,
            admin.user_id,
            sku,
            warehouse_id,
            qty_available=payload.qty_available,
            warehouse_name=payload.warehouse_name,
        )
    )


@router.post("/products/{sku}/image", response_model=AdminProductOut)
async def admin_product_image_upload(
    sku: str,
    file: Annotated[UploadFile, File()],
    admin: OwnerDep,
    session: SessionDep,
) -> AdminProductOut:
    data = await file.read(settings.upload_max_bytes + 1)
    return _out(
        set_product_image(
            session, admin.merchant_id, admin.user_id, sku, data
        )
    )


@router.get("/products/{sku}/image")
def admin_product_image(
    sku: str,
    admin: AdminDep,
    session: SessionDep,
) -> FileResponse:
    path, content_type = product_image_path(
        session, admin.merchant_id, sku
    )
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.delete("/products/{sku}/image", response_model=AdminProductOut)
def admin_product_image_delete(
    sku: str,
    admin: OwnerDep,
    session: SessionDep,
) -> AdminProductOut:
    return _out(
        clear_product_image(
            session, admin.merchant_id, admin.user_id, sku
        )
    )


@router.post("/products/{sku}/images", response_model=AdminProductOut)
async def admin_product_gallery_upload(
    sku: str,
    file: Annotated[UploadFile, File()],
    admin: OwnerDep,
    session: SessionDep,
) -> AdminProductOut:
    data = await file.read(settings.upload_max_bytes + 1)
    return _out(
        add_product_gallery_image(
            session, admin.merchant_id, admin.user_id, sku, data
        )
    )


@router.get("/products/{sku}/images/{storage_name}")
def admin_product_gallery_image(
    sku: str,
    storage_name: str,
    admin: AdminDep,
    session: SessionDep,
) -> FileResponse:
    path, content_type = product_gallery_path(
        session, admin.merchant_id, sku, storage_name
    )
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.delete(
    "/products/{sku}/images/{storage_name}", response_model=AdminProductOut
)
def admin_product_gallery_delete(
    sku: str,
    storage_name: str,
    admin: OwnerDep,
    session: SessionDep,
) -> AdminProductOut:
    return _out(
        delete_product_gallery_image(
            session,
            admin.merchant_id,
            admin.user_id,
            sku,
            storage_name,
        )
    )
