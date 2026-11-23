from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import CurrentCustomer, SessionDep
from app.domain.models import Product
from app.services.attachments import attachment_path
from app.services.errors import NotFoundError
from app.services.merchant import demo_merchant_id
from app.services.reviews import (
    ProductReviewView,
    add_review_follow_up,
    admin_reviews,
    create_review,
    my_reviews,
    product_review_summary,
    public_review_image,
    reply_to_review,
    set_review_status,
)

router = APIRouter(tags=["reviews"])


class PublicReviewOut(BaseModel):
    id: int
    rating: int
    content: str
    customer_name: str
    created_at: datetime
    image_urls: list[str]
    merchant_reply: str
    replied_at: datetime | None
    follow_up_content: str
    follow_up_image_urls: list[str]
    followed_up_at: datetime | None


class ProductReviewsOut(BaseModel):
    average: float | None
    count: int
    reviews: list[PublicReviewOut]


class MyReviewOut(BaseModel):
    id: int
    order_no: str
    sku: str
    product_title: str
    rating: int
    content: str
    status: str
    created_at: datetime
    image_urls: list[str]
    merchant_reply: str
    replied_at: datetime | None
    follow_up_content: str
    follow_up_image_urls: list[str]
    followed_up_at: datetime | None


class CreateReviewIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=32)
    sku: str = Field(min_length=1, max_length=64)
    rating: int = Field(ge=1, le=5)
    content: str = Field(default="", max_length=2000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=3)


class AdminReviewOut(BaseModel):
    id: int
    order_no: str
    sku: str
    product_title: str
    customer_name: str
    customer_phone: str | None
    rating: int
    content: str
    status: str
    created_at: datetime
    image_urls: list[str]
    merchant_reply: str
    replied_at: datetime | None
    follow_up_content: str
    follow_up_image_urls: list[str]
    followed_up_at: datetime | None


class ReviewStatusIn(BaseModel):
    status: Literal["visible", "hidden"]


class ReviewReplyIn(BaseModel):
    reply: str = Field(default="", max_length=1000)


class FollowUpReviewIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=3)


def _admin_out(view: ProductReviewView) -> AdminReviewOut:
    return AdminReviewOut(
        id=view.id,
        order_no=view.order_no,
        sku=view.sku,
        product_title=view.product_title,
        customer_name=view.customer_name,
        customer_phone=view.customer_phone,
        rating=view.rating,
        content=view.content,
        status=view.status,
        created_at=view.created_at,
        image_urls=[
            f"/api/admin/attachments/{attachment_id}"
            for attachment_id in view.attachment_ids
        ],
        merchant_reply=view.merchant_reply,
        replied_at=view.replied_at,
        follow_up_content=view.follow_up_content,
        follow_up_image_urls=[
            f"/api/admin/attachments/{attachment_id}"
            for attachment_id in view.follow_up_attachment_ids
        ],
        followed_up_at=view.followed_up_at,
    )


def _public_image_urls(view: ProductReviewView) -> list[str]:
    return [
        f"/api/store/reviews/{view.id}/images/{attachment_id}"
        for attachment_id in view.attachment_ids
    ]


def _customer_image_urls(view: ProductReviewView) -> list[str]:
    return [
        f"/api/store/attachments/{attachment_id}"
        for attachment_id in view.attachment_ids
    ]


def _public_follow_up_urls(view: ProductReviewView) -> list[str]:
    return [
        f"/api/store/reviews/{view.id}/images/{attachment_id}"
        for attachment_id in view.follow_up_attachment_ids
    ]


def _customer_follow_up_urls(view: ProductReviewView) -> list[str]:
    return [
        f"/api/store/attachments/{attachment_id}"
        for attachment_id in view.follow_up_attachment_ids
    ]


@router.get(
    "/api/store/products/{sku}/reviews", response_model=ProductReviewsOut
)
def public_product_reviews(
    sku: str, session: SessionDep
) -> ProductReviewsOut:
    merchant_id = demo_merchant_id(session)
    product = session.execute(
        select(Product).where(
            Product.merchant_id == merchant_id,
            Product.sku == sku.strip().upper(),
        )
    ).scalar_one_or_none()
    if product is None:
        raise NotFoundError("商品不存在")
    summary = product_review_summary(session, merchant_id, product.id)
    return ProductReviewsOut(
        average=summary.average,
        count=summary.count,
        reviews=[
            PublicReviewOut(
                id=review.id,
                rating=review.rating,
                content=review.content,
                customer_name=review.customer_name,
                created_at=review.created_at,
                image_urls=_public_image_urls(review),
                merchant_reply=review.merchant_reply,
                replied_at=review.replied_at,
                follow_up_content=review.follow_up_content,
                follow_up_image_urls=_public_follow_up_urls(review),
                followed_up_at=review.followed_up_at,
            )
            for review in summary.reviews
        ],
    )


@router.get("/api/store/reviews/{review_id}/images/{attachment_id}")
def public_review_image_file(
    review_id: int,
    attachment_id: int,
    session: SessionDep,
) -> FileResponse:
    attachment = public_review_image(session, review_id, attachment_id)
    return FileResponse(
        attachment_path(attachment),
        media_type=attachment.content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/store/reviews/mine", response_model=list[MyReviewOut])
def customer_reviews(
    customer: CurrentCustomer, session: SessionDep
) -> list[MyReviewOut]:
    return [
        MyReviewOut(
            id=view.id,
            order_no=view.order_no,
            sku=view.sku,
            product_title=view.product_title,
            rating=view.rating,
            content=view.content,
            status=view.status,
            created_at=view.created_at,
            image_urls=_customer_image_urls(view),
            merchant_reply=view.merchant_reply,
            replied_at=view.replied_at,
            follow_up_content=view.follow_up_content,
            follow_up_image_urls=_customer_follow_up_urls(view),
            followed_up_at=view.followed_up_at,
        )
        for view in my_reviews(session, customer)
    ]


@router.post("/api/store/reviews", response_model=MyReviewOut)
def customer_create_review(
    payload: CreateReviewIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> MyReviewOut:
    view = create_review(
        session,
        customer,
        payload.order_no,
        payload.sku,
        payload.rating,
        payload.content,
        payload.attachment_ids,
    )
    return MyReviewOut(
        id=view.id,
        order_no=view.order_no,
        sku=view.sku,
        product_title=view.product_title,
        rating=view.rating,
        content=view.content,
        status=view.status,
        created_at=view.created_at,
        image_urls=_customer_image_urls(view),
        merchant_reply=view.merchant_reply,
        replied_at=view.replied_at,
        follow_up_content=view.follow_up_content,
        follow_up_image_urls=_customer_follow_up_urls(view),
        followed_up_at=view.followed_up_at,
    )


@router.post(
    "/api/store/reviews/{review_id}/follow-up", response_model=MyReviewOut
)
def customer_review_follow_up(
    review_id: int,
    payload: FollowUpReviewIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> MyReviewOut:
    view = add_review_follow_up(
        session,
        customer,
        review_id,
        payload.content,
        payload.attachment_ids,
    )
    return MyReviewOut(
        id=view.id,
        order_no=view.order_no,
        sku=view.sku,
        product_title=view.product_title,
        rating=view.rating,
        content=view.content,
        status=view.status,
        created_at=view.created_at,
        image_urls=_customer_image_urls(view),
        merchant_reply=view.merchant_reply,
        replied_at=view.replied_at,
        follow_up_content=view.follow_up_content,
        follow_up_image_urls=_customer_follow_up_urls(view),
        followed_up_at=view.followed_up_at,
    )


@router.get("/api/admin/reviews", response_model=list[AdminReviewOut])
def admin_product_reviews(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[
        Literal["all", "visible", "hidden"], Query()
    ] = "all",
) -> list[AdminReviewOut]:
    return [
        _admin_out(view)
        for view in admin_reviews(session, admin.merchant_id, status)
    ]


@router.patch(
    "/api/admin/reviews/{review_id}/reply", response_model=AdminReviewOut
)
def admin_review_reply(
    review_id: int,
    payload: ReviewReplyIn,
    admin: OwnerDep,
    session: SessionDep,
) -> AdminReviewOut:
    return _admin_out(
        reply_to_review(
            session,
            admin.merchant_id,
            admin.user_id,
            review_id,
            payload.reply,
        )
    )


@router.patch(
    "/api/admin/reviews/{review_id}", response_model=AdminReviewOut
)
def admin_review_status(
    review_id: int,
    payload: ReviewStatusIn,
    admin: OwnerDep,
    session: SessionDep,
) -> AdminReviewOut:
    return _admin_out(
        set_review_status(
            session,
            admin.merchant_id,
            admin.user_id,
            review_id,
            payload.status,
        )
    )
