from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Customer, Notification, Outbox, User
from app.settings import settings


def _email_preference_field(notification_type: str) -> str:
    if notification_type.startswith("shipment_"):
        return "notify_email_shipment"
    if notification_type.startswith("return_"):
        return "notify_email_return"
    return "notify_email_after_sales"


def notify_customer(
    session: Session,
    merchant_id: int,
    customer_id: int,
    *,
    notification_type: str,
    title: str,
    body: str = "",
    link: str = "",
) -> Notification:
    notification = Notification(
        merchant_id=merchant_id,
        recipient_type="customer",
        customer_id=customer_id,
        type=notification_type,
        title=title,
        body=body,
        link=link,
    )
    session.add(notification)
    customer = session.get(Customer, customer_id)
    preference_enabled = (
        customer is not None
        and bool(
            getattr(
                customer,
                _email_preference_field(notification_type),
                True,
            )
        )
    )
    if (
        customer is not None
        and customer.email
        and settings.customer_email_notify_enabled
        and preference_enabled
    ):
        detail = (
            f"\n\n查看详情：{settings.admin_base_url.rstrip('/')}{link}"
            if link
            else ""
        )
        session.add(
            Outbox(
                merchant_id=merchant_id,
                event_type="customer_notification_email",
                aggregate_type="notification",
                aggregate_id=f"customer:{customer_id}",
                payload_json={
                    "to": customer.email,
                    "subject": f"[AfterOps] {title}",
                    "body": (
                        f"{body}{detail}\n\n"
                        "如需停用邮件通知，请登录售后进度页清除通知邮箱。"
                    ),
                },
            )
        )
    return notification


def notify_user(
    session: Session,
    merchant_id: int,
    user: User,
    *,
    notification_type: str,
    title: str,
    body: str = "",
    link: str = "",
) -> Notification:
    notification = Notification(
        merchant_id=merchant_id,
        recipient_type="user",
        user_id=user.id,
        type=notification_type,
        title=title,
        body=body,
        link=link,
    )
    session.add(notification)
    if settings.email_notify_enabled and user.email:
        session.add(
            Outbox(
                merchant_id=merchant_id,
                event_type="staff_notification_email",
                aggregate_type="notification",
                aggregate_id=f"user:{user.id}",
                payload_json={
                    "to": user.email,
                    "subject": f"[AfterOps] {title}",
                    "body": f"{body}\n\n请登录商家后台处理：{settings.admin_base_url}/admin",
                },
            )
        )
    return notification


def notify_staff(
    session: Session,
    merchant_id: int,
    *,
    notification_type: str,
    title: str,
    body: str = "",
    link: str = "",
) -> None:
    users = session.execute(
        select(User).where(
            User.merchant_id == merchant_id,
            User.status == "active",
        )
    ).scalars().all()
    for user in users:
        notify_user(
            session,
            merchant_id,
            user,
            notification_type=notification_type,
            title=title,
            body=body,
            link=link,
        )
