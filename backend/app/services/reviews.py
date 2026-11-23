from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Attachment,
    Customer,
    Order,
    OrderItem,
    ProductReview,
)
from app.services.attachments import attachment_path, validate_attachment_ids
from app.services.audit import append_event
from app.services.errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class ProductReviewView:
    id: int
    order_no: str
    sku: str
    product_title: str
    customer_name: str
    customer_phone: str | None
    rating: int
    content: str
    attachment_ids: tuple[int, ...]
    merchant_reply: str
    replied_at: datetime | None
    status: str
    follow_up_content: str
    follow_up_attachment_ids: tuple[int, ...]
    followed_up_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ProductReviewSummary:
    average: float | None
    count: int
    reviews: tuple[ProductReviewView, ...]


def _view(
    review: ProductReview,
    order: Order,
    item: OrderItem,
    customer: Customer,
) -> ProductReviewView:
    return ProductReviewView(
        id=review.id,
        order_no=order.order_no,
        sku=item.sku,
        product_title=item.title_snapshot,
        customer_name=customer.name or "匿名顾客",
        customer_phone=customer.phone,
        rating=review.rating,
        content=review.content,
        attachment_ids=tuple(review.attachment_ids or ()),
        merchant_reply=review.merchant_reply,
        replied_at=review.replied_at,
        status=review.status,
        follow_up_content=review.follow_up_content,
        follow_up_attachment_ids=tuple(
            review.follow_up_attachment_ids or ()
        ),
        followed_up_at=review.followed_up_at,
        created_at=review.created_at,
    )


def create_review(
    session: Session,
    customer: Customer,
    order_no: str,
    sku: str,
    rating: int,
    content: str,
    attachment_ids: list[int] | None = None,
) -> ProductReviewView:
    if rating < 1 or rating > 5:
        raise ValidationError("评分需在 1–5 星之间")
    content_text = content.strip()
    if len(content_text) > 2000:
        raise ValidationError("评价内容不能超过 2000 个字符")
    try:
        order = session.execute(
            select(Order)
            .where(
                Order.order_no == order_no,
                Order.merchant_id == customer.merchant_id,
                Order.customer_id == customer.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("订单不存在")
        if order.status not in ("delivered", "completed"):
            raise ConflictError("只有已签收订单可以评价")
        item = session.execute(
            select(OrderItem).where(
                OrderItem.order_id == order.id,
                OrderItem.sku == sku.strip().upper(),
            )
        ).scalar_one_or_none()
        if item is None:
            raise ValidationError("商品不属于该订单")
        if item.cancelled_qty >= item.qty:
            raise ConflictError("该商品已取消，不能评价")
        existing = session.execute(
            select(ProductReview.id).where(
                ProductReview.order_item_id == item.id
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("该商品已评价")
        attachments = validate_attachment_ids(
            session, customer, attachment_ids or []
        )
        review = ProductReview(
            merchant_id=customer.merchant_id,
            product_id=item.product_id,
            order_id=order.id,
            order_item_id=item.id,
            customer_id=customer.id,
            rating=rating,
            content=content_text,
            attachment_ids=attachments or None,
            status="visible",
        )
        session.add(review)
        session.flush()
        append_event(
            session,
            customer.merchant_id,
            "review_created",
            {
                "review_id": review.id,
                "order_no": order.order_no,
                "sku": item.sku,
                "rating": rating,
                "attachment_count": len(attachments),
            },
        )
        session.commit()
        return _view(review, order, item, customer)
    except Exception:
        session.rollback()
        raise


def add_review_follow_up(
    session: Session,
    customer: Customer,
    review_id: int,
    content: str,
    attachment_ids: list[int] | None = None,
) -> ProductReviewView:
    content_text = content.strip()
    if not content_text:
        raise ValidationError("追评内容不能为空")
    if len(content_text) > 1000:
        raise ValidationError("追评内容不能超过 1000 个字符")
    try:
        row = session.execute(
            select(ProductReview, Order, OrderItem, Customer)
            .join(Order, Order.id == ProductReview.order_id)
            .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
            .join(Customer, Customer.id == ProductReview.customer_id)
            .where(
                ProductReview.id == review_id,
                ProductReview.merchant_id == customer.merchant_id,
                ProductReview.customer_id == customer.id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("评价不存在")
        review, order, item, row_customer = row
        if review.status != "visible":
            raise ConflictError("评价已隐藏，不能追评")
        if review.followed_up_at is not None:
            raise ConflictError("该评价已追评")
        created_at = review.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - created_at > timedelta(days=30):
            raise ConflictError("评价超过 30 天，不能追评")
        attachments = validate_attachment_ids(
            session, customer, attachment_ids or []
        )
        review.follow_up_content = content_text
        review.follow_up_attachment_ids = attachments or None
        review.followed_up_at = datetime.now(UTC)
        append_event(
            session,
            customer.merchant_id,
            "review_followed_up",
            {
                "review_id": review.id,
                "order_no": order.order_no,
                "sku": item.sku,
                "attachment_count": len(attachments),
            },
        )
        session.commit()
        return _view(review, order, item, row_customer)
    except Exception:
        session.rollback()
        raise


def product_review_summary(
    session: Session, merchant_id: int, product_id: int, limit: int = 50
) -> ProductReviewSummary:
    rows = session.execute(
        select(ProductReview, Order, OrderItem, Customer)
        .join(Order, Order.id == ProductReview.order_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(Customer, Customer.id == ProductReview.customer_id)
        .where(
            ProductReview.merchant_id == merchant_id,
            ProductReview.product_id == product_id,
            ProductReview.status == "visible",
        )
        .order_by(ProductReview.id.desc())
    ).all()
    count = len(rows)
    average = (
        round(sum(row[0].rating for row in rows) / count, 1)
        if count
        else None
    )
    return ProductReviewSummary(
        average=average,
        count=count,
        reviews=tuple(
            _view(review, order, item, customer)
            for review, order, item, customer in rows[:limit]
        ),
    )


def my_reviews(
    session: Session, customer: Customer
) -> list[ProductReviewView]:
    rows = session.execute(
        select(ProductReview, Order, OrderItem, Customer)
        .join(Order, Order.id == ProductReview.order_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(Customer, Customer.id == ProductReview.customer_id)
        .where(
            ProductReview.merchant_id == customer.merchant_id,
            ProductReview.customer_id == customer.id,
        )
        .order_by(ProductReview.id.desc())
    ).all()
    return [
        _view(review, order, item, row_customer)
        for review, order, item, row_customer in rows
    ]


def admin_reviews(
    session: Session, merchant_id: int, status: str = "all"
) -> list[ProductReviewView]:
    stmt = (
        select(ProductReview, Order, OrderItem, Customer)
        .join(Order, Order.id == ProductReview.order_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(Customer, Customer.id == ProductReview.customer_id)
        .where(ProductReview.merchant_id == merchant_id)
    )
    if status != "all":
        stmt = stmt.where(ProductReview.status == status)
    rows = session.execute(
        stmt.order_by(ProductReview.id.desc()).limit(200)
    ).all()
    return [
        _view(review, order, item, customer)
        for review, order, item, customer in rows
    ]


def public_review_image(
    session: Session, review_id: int, attachment_id: int
) -> Attachment:
    review = session.execute(
        select(ProductReview).where(
            ProductReview.id == review_id,
            ProductReview.status == "visible",
        )
    ).scalar_one_or_none()
    if review is None:
        raise NotFoundError("评价图片不存在")
    allowed = set(review.attachment_ids or []) | set(
        review.follow_up_attachment_ids or []
    )
    if attachment_id not in allowed:
        raise NotFoundError("评价图片不存在")
    attachment = session.execute(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.merchant_id == review.merchant_id,
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFoundError("评价图片不存在")
    attachment_path(attachment)
    return attachment


def reply_to_review(
    session: Session,
    merchant_id: int,
    user_id: int,
    review_id: int,
    reply: str,
) -> ProductReviewView:
    reply_text = reply.strip()
    if len(reply_text) > 1000:
        raise ValidationError("商家回复不能超过 1000 个字符")
    try:
        row = session.execute(
            select(ProductReview, Order, OrderItem, Customer)
            .join(Order, Order.id == ProductReview.order_id)
            .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
            .join(Customer, Customer.id == ProductReview.customer_id)
            .where(
                ProductReview.id == review_id,
                ProductReview.merchant_id == merchant_id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("评价不存在")
        review, order, item, customer = row
        previous = review.merchant_reply
        if previous == reply_text:
            return _view(review, order, item, customer)
        review.merchant_reply = reply_text
        review.replied_by = user_id if reply_text else None
        review.replied_at = datetime.now(UTC) if reply_text else None
        append_event(
            session,
            merchant_id,
            "review_replied",
            {
                "review_id": review.id,
                "order_no": order.order_no,
                "sku": item.sku,
                "has_reply": bool(reply_text),
                "previous_length": len(previous),
                "reply_length": len(reply_text),
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(review, order, item, customer)
    except Exception:
        session.rollback()
        raise


def set_review_status(
    session: Session,
    merchant_id: int,
    user_id: int,
    review_id: int,
    status: str,
) -> ProductReviewView:
    if status not in ("visible", "hidden"):
        raise ValidationError("未知评价状态")
    try:
        row = session.execute(
            select(ProductReview, Order, OrderItem, Customer)
            .join(Order, Order.id == ProductReview.order_id)
            .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
            .join(Customer, Customer.id == ProductReview.customer_id)
            .where(
                ProductReview.id == review_id,
                ProductReview.merchant_id == merchant_id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise NotFoundError("评价不存在")
        review, order, item, customer = row
        review.status = status
        append_event(
            session,
            merchant_id,
            "review_visibility_changed",
            {
                "review_id": review.id,
                "order_no": order.order_no,
                "sku": item.sku,
                "status": status,
                "operator_user_id": user_id,
            },
        )
        session.commit()
        return _view(review, order, item, customer)
    except Exception:
        session.rollback()
        raise
