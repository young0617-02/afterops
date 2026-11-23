"""Core domain tables.

Money is stored as integer cents. State columns are strings with CHECK constraints
so migrations stay explicit and database-portable. JSON uses JSONB on PostgreSQL
and plain JSON on SQLite for unit tests.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

BigIntPK = BigInteger().with_variant(Integer, "sqlite")
JSONType = JSON().with_variant(JSONB, "postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Merchant(TimestampMixin, Base):
    __tablename__ = "merchants"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    auto_assign_handoffs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="status_valid"),
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="agent")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    handoff_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    handoff_skills: Mapped[list[str] | None] = mapped_column(
        JSONType, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("merchant_id", "email", name="uq_users_merchant_email"),
        CheckConstraint("role IN ('owner','agent')", name="role_valid"),
        CheckConstraint("status IN ('active','disabled')", name="status_valid"),
        Index("ix_users_merchant_role", "merchant_id", "role"),
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notify_email_shipment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    notify_email_after_sales: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    notify_email_return: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("merchant_id", "phone", name="uq_customers_merchant_phone"),
        UniqueConstraint("merchant_id", "email", name="uq_customers_merchant_email"),
        CheckConstraint("phone IS NOT NULL OR email IS NOT NULL", name="contact_present"),
        Index("ix_customers_merchant_created", "merchant_id", "created_at"),
    )


class CustomerAddress(TimestampMixin, Base):
    __tablename__ = "customer_addresses"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )

    __table_args__ = (
        Index("ix_customer_addresses_customer_updated", "customer_id", "updated_at"),
        Index("ix_customer_addresses_customer_default", "customer_id", "is_default"),
    )


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    brand: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    specs: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    variant_group: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    variant_label: Mapped[str] = mapped_column(
        String(80), nullable=False, server_default=""
    )
    support_7d_no_reason: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    warranty_months: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="12"
    )
    listed_at: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    image_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_content_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gallery_images: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONType, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("merchant_id", "sku", name="uq_products_merchant_sku"),
        CheckConstraint("price_cents >= 0", name="price_nonnegative"),
        CheckConstraint("cost_cents >= 0", name="cost_nonnegative"),
        CheckConstraint(
            "warranty_months >= 0 AND warranty_months <= 120",
            name="warranty_months_range",
        ),
        CheckConstraint("status IN ('draft','active','archived')", name="status_valid"),
        Index("ix_products_merchant_status", "merchant_id", "status"),
    )


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    warehouse_name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    qty_available: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    qty_reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", name="uq_inventory_product_warehouse"),
        CheckConstraint("qty_available >= 0", name="available_nonnegative"),
        CheckConstraint("qty_reserved >= 0", name="reserved_nonnegative"),
        Index("ix_inventory_merchant_product", "merchant_id", "product_id"),
    )


class StockTransfer(TimestampMixin, Base):
    __tablename__ = "stock_transfers"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    transfer_no: Mapped[str] = mapped_column(String(32), nullable=False)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title_snapshot: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    from_warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False)
    to_warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="in_transit"
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    received_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )

    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "transfer_no", name="uq_stock_transfers_merchant_no"
        ),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            "status IN ('in_transit','received','cancelled')",
            name="status_valid",
        ),
        Index(
            "ix_stock_transfers_merchant_status",
            "merchant_id",
            "status",
            "updated_at",
        ),
        Index("ix_stock_transfers_product", "product_id"),
    )


class Cart(TimestampMixin, Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")

    __table_args__ = (
        CheckConstraint("status IN ('active','ordered','abandoned')", name="status_valid"),
        Index("ix_carts_merchant_customer_status", "merchant_id", "customer_id", "status"),
    )


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    cart_id: Mapped[int] = mapped_column(
        ForeignKey("carts.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("cart_id", "product_id", name="uq_cart_items_cart_product"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price_cents >= 0", name="price_nonnegative"),
        Index("ix_cart_items_cart", "cart_id"),
    )


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    recipient_phone: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    address_region: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    address_city: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    address_detail: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending_payment"
    )
    payment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="unpaid"
    )
    fulfillment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="unfulfilled"
    )
    item_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    freight_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    discount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    payable_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="CNY")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_payment','paid','fulfilling','shipped','delivered',"
            "'completed','cancelled','refunding','refunded','partially_refunded')",
            name="status_valid",
        ),
        CheckConstraint(
            "payment_status IN ('unpaid','pending','paid','failed','refunded','partially_refunded')",
            name="payment_status_valid",
        ),
        CheckConstraint(
            "fulfillment_status IN ('unfulfilled','allocated','shipped','delivered')",
            name="fulfillment_status_valid",
        ),
        CheckConstraint(
            "item_total_cents >= 0 AND freight_cents >= 0 AND discount_cents >= 0 "
            "AND payable_cents >= 0",
            name="amounts_nonnegative",
        ),
        Index("ix_orders_merchant_placed", "merchant_id", "placed_at"),
        Index("ix_orders_customer_placed", "customer_id", "placed_at"),
        Index("ix_orders_status", "status"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    cancelled_qty: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warranty_months: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="12"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            "cancelled_qty >= 0 AND cancelled_qty <= qty",
            name="cancelled_qty_range",
        ),
        CheckConstraint("unit_price_cents >= 0", name="price_nonnegative"),
        CheckConstraint(
            "warranty_months >= 0 AND warranty_months <= 120",
            name="warranty_months_range",
        ),
        Index("ix_order_items_order", "order_id"),
    )


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="reserved")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_reservations_order_product"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            "status IN ('reserved','committed','released','expired')", name="status_valid"
        ),
        Index("ix_reservations_status_expires", "status", "expires_at"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default="mock")
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="created")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="amount_nonnegative"),
        CheckConstraint(
            "status IN ('created','pending','paid','failed','refunded','partially_refunded')",
            name="status_valid",
        ),
        Index("ix_payments_order", "order_id"),
        Index("ix_payments_provider_payment", "provider", "provider_payment_id"),
    )


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    package_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    carrier: Mapped[str] = mapped_column(String(64), nullable=False, server_default="mock")
    tracking_no: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="pending")
    current_node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','in_transit','out_for_delivery','delivered','exception')",
            name="status_valid",
        ),
        CheckConstraint("package_no > 0", name="package_no_positive"),
        UniqueConstraint("order_id", "package_no", name="uq_shipments_order_package"),
        UniqueConstraint(
            "merchant_id", "tracking_no", name="uq_shipments_merchant_tracking"
        ),
        Index("ix_shipments_tracking", "tracking_no"),
    )


class ShipmentItem(Base):
    __tablename__ = "shipment_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "shipment_id", "order_item_id", name="uq_shipment_items_shipment_item"
        ),
        CheckConstraint("qty > 0", name="qty_positive"),
        Index("ix_shipment_items_order_item", "order_item_id"),
    )


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_shipment_events_shipment_time", "shipment_id", "happened_at"),)


class PolicyEntry(Base):
    __tablename__ = "policy_entries"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_at: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "entry_id", "version", name="uq_policy_entries_merchant_entry_version"
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="validity_order"
        ),
        Index("ix_policy_entries_merchant_topic", "merchant_id", "topic"),
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, server_default="web")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")

    __table_args__ = (
        CheckConstraint("status IN ('open','handoff','closed')", name="status_valid"),
        Index("ix_conversations_merchant_customer", "merchant_id", "customer_id", "created_at"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    attachment_ids: Mapped[list[int] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','tool','system','agent')",
            name="role_valid",
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )


class QuickReply(TimestampMixin, Base):
    __tablename__ = "quick_replies"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(
        String(80), nullable=False, server_default=""
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="active"
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "title", name="uq_quick_replies_merchant_title"
        ),
        CheckConstraint(
            "status IN ('active','disabled')", name="status_valid"
        ),
        Index("ix_quick_replies_merchant_status", "merchant_id", "status"),
    )


class HandoffTicket(TimestampMixin, Base):
    __tablename__ = "handoff_tickets"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    queue: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="after_sales"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="open"
    )
    priority: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="normal"
    )
    intent: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    last_user_message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    last_reply: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    follow_up_for_ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("handoff_tickets.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','claimed','resolved')", name="status_valid"
        ),
        CheckConstraint(
            "queue IN ('after_sales','complaint')", name="queue_valid"
        ),
        CheckConstraint(
            "priority IN ('normal','high','urgent')", name="priority_valid"
        ),
        Index(
            "ix_handoff_tickets_merchant_status",
            "merchant_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_handoff_tickets_merchant_priority",
            "merchant_id",
            "status",
            "priority",
            "updated_at",
        ),
        Index("ix_handoff_tickets_conversation_status", "conversation_id", "status"),
    )


class HandoffNote(Base):
    __tablename__ = "handoff_notes"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("handoff_tickets.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_handoff_notes_ticket_created", "ticket_id", "created_at"),
        Index("ix_handoff_notes_merchant", "merchant_id"),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    recipient_type: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    link: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "recipient_type IN ('customer','user')", name="recipient_type_valid"
        ),
        CheckConstraint(
            "(recipient_type = 'customer' AND customer_id IS NOT NULL "
            "AND user_id IS NULL) OR (recipient_type = 'user' "
            "AND user_id IS NOT NULL AND customer_id IS NULL)",
            name="recipient_valid",
        ),
        Index("ix_notifications_customer_created", "customer_id", "created_at"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )


class AfterSalesCase(TimestampMixin, Base):
    __tablename__ = "after_sales_cases"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="requested")
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('refund','return','compensation','address_change')", name="type_valid"
        ),
        CheckConstraint(
            "status IN ('requested','reviewing','approved','rejected','executing','executed','failed','withdrawn')",
            name="status_valid",
        ),
        CheckConstraint("amount_cents IS NULL OR amount_cents >= 0", name="amount_nonnegative"),
        Index("ix_after_sales_cases_order", "order_id"),
        Index("ix_after_sales_cases_merchant_status", "merchant_id", "status"),
    )


class ActionProposal(TimestampMixin, Base):
    __tablename__ = "action_proposals"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    after_sales_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("after_sales_cases.id", ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ai")
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    commerce_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('refund','address_change','compensation','cancel_order','handoff')",
            name="action_type_valid",
        ),
        CheckConstraint("risk_level IN ('L1','L2','L3')", name="risk_level_valid"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','executing','executed','failed','expired','withdrawn')",
            name="status_valid",
        ),
        Index("ix_action_proposals_merchant_status", "merchant_id", "status"),
        Index("ix_action_proposals_conversation", "conversation_id"),
    )


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    after_sales_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("after_sales_cases.id", ondelete="SET NULL"), nullable=True
    )
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("action_proposals.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="executed")
    provider_refund_id: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=""
    )
    settlement_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="succeeded"
    )
    settlement_reference: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=""
    )
    settlement_failure_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    settlement_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settlement_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settlement_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('refund','compensation')", name="kind_valid"),
        CheckConstraint("amount_cents >= 0", name="amount_nonnegative"),
        CheckConstraint("status IN ('executed','failed')", name="status_valid"),
        CheckConstraint(
            "settlement_status IN ('processing','succeeded','failed')",
            name="settlement_status_valid",
        ),
        CheckConstraint(
            "settlement_attempts >= 0", name="settlement_attempts_nonnegative"
        ),
        UniqueConstraint("proposal_id", name="uq_refunds_proposal_id"),
        Index("ix_refunds_order", "order_id"),
        Index("ix_refunds_merchant_created", "merchant_id", "created_at"),
    )


class AfterSalesCharge(Base):
    __tablename__ = "after_sales_charges"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    return_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("return_requests.id", ondelete="SET NULL"), nullable=True
    )
    repair_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("repair_requests.id", ondelete="SET NULL"), nullable=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('exchange_supplement','repair_fee')", name="kind_valid"
        ),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        UniqueConstraint(
            "reference", name="uq_after_sales_charges_reference"
        ),
        UniqueConstraint(
            "return_request_id",
            name="uq_after_sales_charges_return_request_id",
        ),
        UniqueConstraint(
            "repair_request_id",
            name="uq_after_sales_charges_repair_request_id",
        ),
        Index(
            "ix_after_sales_charges_merchant_created",
            "merchant_id",
            "created_at",
        ),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_events_merchant_created", "merchant_id", "created_at"),
        Index("ix_audit_events_conversation", "conversation_id"),
        Index("ix_audit_events_proposal", "proposal_id"),
    )


class ReturnRequest(TimestampMixin, Base):
    __tablename__ = "return_requests"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="requested"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    reason_category: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="other"
    )
    resolution: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="refund"
    )
    exchange_sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_unit_price_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    return_address: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    carrier: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    tracking_no: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    shipment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="none"
    )
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refund_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_proposals.id", ondelete="SET NULL"), nullable=True
    )
    refund_amount_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    replacement_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    exchange_supplement_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    exchange_payment_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="none"
    )
    exchange_payment_ref: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    exchange_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exchange_cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exchange_cancel_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    exchange_cancelled_by: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=""
    )
    exchange_supplement_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exchange_supplement_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attachment_ids: Mapped[list[int] | None] = mapped_column(JSONType, nullable=True)
    inspection_attachment_ids: Mapped[list[int] | None] = mapped_column(
        JSONType, nullable=True
    )
    inspection_deduction_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    inspection_note: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    items: Mapped[list[ReturnItem]] = relationship(
        "ReturnItem",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReturnItem.id",
    )
    tracking_events: Mapped[list[ReturnTrackingEvent]] = relationship(
        "ReturnTrackingEvent",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReturnTrackingEvent.happened_at, ReturnTrackingEvent.id",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','approved','in_transit','received','rejected','withdrawn',"
            "'exchange_cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "shipment_status IN ('none','registered','picked_up','in_transit',"
            "'out_for_delivery','delivered')",
            name="shipment_status_valid",
        ),
        CheckConstraint(
            "resolution IN ('refund','exchange')", name="resolution_valid"
        ),
        CheckConstraint(
            "reason_category IN ('quality','damaged','wrong_item','size',"
            "'not_needed','other')",
            name="reason_category_valid",
        ),
        CheckConstraint(
            "inspection_deduction_cents >= 0",
            name="inspection_deduction_nonnegative",
        ),
        CheckConstraint(
            "exchange_supplement_cents >= 0",
            name="exchange_supplement_nonnegative",
        ),
        CheckConstraint(
            "exchange_payment_status IN ('none','pending','paid','cancelled')",
            name="exchange_payment_status_valid",
        ),
        Index(
            "ix_return_requests_merchant_status",
            "merchant_id",
            "status",
            "updated_at",
        ),
        Index("ix_return_requests_order", "order_id"),
    )


class ReturnItem(Base):
    __tablename__ = "return_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    return_request_id: Mapped[int] = mapped_column(
        ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    exchange_sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_unit_price_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    disposition: Mapped[str | None] = mapped_column(String(12), nullable=True)
    inspection_deduction_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    responsibility: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    inspected_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    inspected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "return_request_id",
            "order_item_id",
            name="uq_return_items_request_item",
        ),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            "disposition IS NULL OR disposition IN ('restock','damaged')",
            name="disposition_valid",
        ),
        CheckConstraint(
            "inspection_deduction_cents >= 0",
            name="inspection_deduction_nonnegative",
        ),
        CheckConstraint(
            "responsibility IS NULL OR responsibility IN "
            "('merchant','customer','logistics','other')",
            name="responsibility_valid",
        ),
    )


class ReturnDeductionAppeal(TimestampMixin, Base):
    __tablename__ = "return_deduction_appeals"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    return_request_id: Mapped[int] = mapped_column(
        ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    attachment_ids: Mapped[list[int] | None] = mapped_column(
        JSONType, nullable=True
    )
    requested_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    approved_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    resolution_note: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "return_request_id", name="uq_return_deduction_appeals_return"
        ),
        CheckConstraint(
            "status IN ('pending','accepted','rejected','withdrawn')",
            name="status_valid",
        ),
        CheckConstraint(
            "requested_cents >= 0", name="requested_nonnegative"
        ),
        CheckConstraint(
            "approved_cents IS NULL OR approved_cents >= 0",
            name="approved_nonnegative",
        ),
        Index(
            "ix_return_deduction_appeals_merchant_status",
            "merchant_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_return_deduction_appeals_customer",
            "customer_id",
            "created_at",
        ),
    )


class ReturnPickup(TimestampMixin, Base):
    __tablename__ = "return_pickups"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    return_request_id: Mapped[int] = mapped_column(
        ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    pickup_no: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="scheduled"
    )
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    time_window: Mapped[str] = mapped_column(String(16), nullable=False)
    address_recipient: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    address_phone: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    address_region: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    address_city: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    address_detail: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    courier_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    courier_phone: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    cancel_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    picked_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "pickup_no", name="uq_return_pickups_merchant_no"
        ),
        CheckConstraint(
            "status IN ('scheduled','picked_up','cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "time_window IN ('09:00-12:00','13:00-18:00','18:00-21:00')",
            name="time_window_valid",
        ),
        Index(
            "ix_return_pickups_merchant_status_date",
            "merchant_id",
            "status",
            "scheduled_date",
        ),
        Index("ix_return_pickups_return", "return_request_id"),
    )


class ReturnTrackingEvent(Base):
    __tablename__ = "return_tracking_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    return_request_id: Mapped[int] = mapped_column(
        ForeignKey("return_requests.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    happened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('registered','picked_up','in_transit',"
            "'out_for_delivery','delivered','exception')",
            name="status_valid",
        ),
        Index(
            "ix_return_tracking_events_request",
            "return_request_id",
            "happened_at",
        ),
    )


class ReturnPolicy(TimestampMixin, Base):
    __tablename__ = "return_policies"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    return_shipping_fee_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    allow_exchange: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    final_sale_categories: Mapped[list[str] | None] = mapped_column(
        JSONType, nullable=True
    )
    category_windows: Mapped[list[dict] | None] = mapped_column(
        JSONType, nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("merchant_id", name="uq_return_policies_merchant"),
        CheckConstraint(
            "window_days >= 0 AND window_days <= 90", name="window_days_range"
        ),
        CheckConstraint(
            "return_shipping_fee_cents >= 0 AND return_shipping_fee_cents <= 20000",
            name="return_shipping_fee_range",
        ),
    )


class InvoiceRequest(TimestampMixin, Base):
    __tablename__ = "invoice_requests"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    invoice_type: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    tax_no: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=""
    )
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="requested"
    )
    invoice_no: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    reject_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    issued_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    voided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "invoice_type IN ('personal','company')",
            name="invoice_type_valid",
        ),
        CheckConstraint(
            "status IN ('requested','issued','rejected','voided')",
            name="status_valid",
        ),
        CheckConstraint("amount_cents >= 0", name="amount_nonnegative"),
        Index(
            "ix_invoice_requests_merchant_status",
            "merchant_id",
            "status",
            "created_at",
        ),
        Index("ix_invoice_requests_order", "order_id"),
    )


class ShippingMethod(TimestampMixin, Base):
    __tablename__ = "shipping_methods"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    fee_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    free_over_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="active"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "code", name="uq_shipping_methods_merchant_code"
        ),
        CheckConstraint("fee_cents >= 0", name="fee_nonnegative"),
        CheckConstraint(
            "free_over_cents IS NULL OR free_over_cents >= 0",
            name="free_over_nonnegative",
        ),
        CheckConstraint(
            "status IN ('active','disabled')", name="status_valid"
        ),
        Index(
            "ix_shipping_methods_merchant_status",
            "merchant_id",
            "status",
            "sort_order",
        ),
    )


class Coupon(TimestampMixin, Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    discount_type: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    min_spend_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    per_customer_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    scope_type: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="all"
    )
    scope_values: Mapped[list[str] | None] = mapped_column(
        JSONType, nullable=True
    )
    claimable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="active"
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "code", name="uq_coupons_merchant_code"
        ),
        CheckConstraint(
            "discount_type IN ('fixed','percent')", name="discount_type_valid"
        ),
        CheckConstraint("value > 0", name="value_positive"),
        CheckConstraint("min_spend_cents >= 0", name="min_spend_nonnegative"),
        CheckConstraint("used_count >= 0", name="used_count_nonnegative"),
        CheckConstraint(
            "per_customer_limit >= 1", name="per_customer_limit_positive"
        ),
        CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on", name="validity_order"
        ),
        CheckConstraint(
            "status IN ('active','disabled')", name="status_valid"
        ),
        CheckConstraint(
            "scope_type IN ('all','category','sku')", name="scope_type_valid"
        ),
        Index("ix_coupons_merchant_status", "merchant_id", "status"),
    )


class CouponClaim(Base):
    __tablename__ = "coupon_claims"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "coupon_id", "customer_id", name="uq_coupon_claims_coupon_customer"
        ),
        Index(
            "ix_coupon_claims_customer_created",
            "customer_id",
            "claimed_at",
        ),
    )


class CouponRedemption(Base):
    __tablename__ = "coupon_redemptions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    discount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("order_id", name="uq_coupon_redemptions_order"),
        Index(
            "ix_coupon_redemptions_coupon_customer",
            "coupon_id",
            "customer_id",
        ),
    )


class ProductReview(TimestampMixin, Base):
    __tablename__ = "product_reviews"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    attachment_ids: Mapped[list[int] | None] = mapped_column(
        JSONType, nullable=True
    )
    merchant_reply: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    replied_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="visible"
    )
    follow_up_content: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    follow_up_attachment_ids: Mapped[list[int] | None] = mapped_column(
        JSONType, nullable=True
    )
    followed_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("order_item_id", name="uq_product_reviews_order_item"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="rating_range"),
        CheckConstraint(
            "status IN ('visible','hidden')", name="status_valid"
        ),
        Index(
            "ix_product_reviews_product_status",
            "product_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_product_reviews_merchant_status",
            "merchant_id",
            "status",
            "created_at",
        ),
    )


class SatisfactionRating(Base):
    __tablename__ = "satisfaction_ratings"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    handoff_ticket_id: Mapped[int] = mapped_column(
        ForeignKey("handoff_tickets.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "handoff_ticket_id", name="uq_satisfaction_ratings_ticket"
        ),
        CheckConstraint("score >= 1 AND score <= 5", name="score_range"),
    )


class RepairRequest(TimestampMixin, Base):
    __tablename__ = "repair_requests"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="requested"
    )
    issue: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    attachment_ids: Mapped[list[int] | None] = mapped_column(
        JSONType, nullable=True
    )
    carrier: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    tracking_no: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    return_carrier: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    return_tracking_no: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    rejected_reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    resolution_note: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    quote_cents: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    quote_status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="none"
    )
    quote_note: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    quote_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quote_revised_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quote_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quote_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quote_payment_ref: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    quote_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    parts_cost_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    parts_note: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    repaired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','approved','rejected','in_transit',"
            "'received','return_shipped','completed','withdrawn')",
            name="status_valid",
        ),
        CheckConstraint(
            "quote_status IN ('none','pending','accepted','declined','paid')",
            name="quote_status_valid",
        ),
        CheckConstraint(
            "quote_cents IS NULL OR quote_cents >= 0",
            name="quote_nonnegative",
        ),
        CheckConstraint(
            "parts_cost_cents >= 0",
            name="parts_cost_nonnegative",
        ),
        Index(
            "ix_repair_requests_merchant_status",
            "merchant_id",
            "status",
            "updated_at",
        ),
        Index("ix_repair_requests_order_item", "order_item_id"),
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False)
    warehouse_name: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=""
    )
    qty_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    return_id: Mapped[int | None] = mapped_column(
        ForeignKey("return_requests.id", ondelete="SET NULL"), nullable=True
    )
    operator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("qty_after >= 0", name="qty_after_nonnegative"),
        CheckConstraint("qty_delta != 0", name="qty_delta_nonzero"),
        Index(
            "ix_inventory_movements_merchant_product",
            "merchant_id",
            "product_id",
            "created_at",
        ),
        Index("ix_inventory_movements_order", "order_id"),
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_attachments_customer_created", "customer_id", "created_at"),
        Index("ix_attachments_merchant_created", "merchant_id", "created_at"),
    )


class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','processed','failed')", name="status_valid"
        ),
        Index("ix_outbox_status_available", "status", "available_at"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_keys_scope_key"),
        Index("ix_idempotency_keys_expires", "expires_at"),
    )


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="received")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_events_provider_event"),
        CheckConstraint(
            "status IN ('received','processed','failed','ignored')", name="status_valid"
        ),
        Index("ix_webhook_events_status_received", "status", "received_at"),
    )


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_otp_challenges_phone_created", "phone", "created_at"),
        Index("ix_otp_challenges_expires", "expires_at"),
    )


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_rate_limit_events_key_created", "key", "created_at"),)

