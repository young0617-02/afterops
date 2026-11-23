from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Attachment, Customer
from app.services.errors import NotFoundError, ValidationError
from app.services.images import ALLOWED_IMAGE_TYPES, detect_image_type
from app.settings import settings

ALLOWED_TYPES = ALLOWED_IMAGE_TYPES
MAX_ATTACHMENTS = 3


def _safe_name(name: str) -> str:
    value = Path(name or "").name.strip().replace('"', "'")
    return value[:255] or "evidence"


def _storage_dir() -> Path:
    directory = Path(settings.upload_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _path_for(attachment: Attachment) -> Path:
    directory = _storage_dir()
    path = (directory / attachment.storage_name).resolve()
    if not path.is_relative_to(directory):
        raise NotFoundError("附件不存在")
    return path


def _save(
    session: Session,
    *,
    merchant_id: int,
    customer_id: int,
    uploaded_by_user_id: int | None,
    original_name: str,
    data: bytes,
) -> Attachment:
    if not data:
        raise ValidationError("附件为空")
    if len(data) > settings.upload_max_bytes:
        raise ValidationError(
            f"附件不能超过 {settings.upload_max_bytes // (1024 * 1024)}MB"
        )
    content_type = detect_image_type(data)
    if content_type is None:
        raise ValidationError("只支持 PNG、JPEG、WebP 图片")
    directory = _storage_dir()
    storage_name = f"{uuid.uuid4().hex}{ALLOWED_TYPES[content_type]}"
    path = directory / storage_name
    try:
        path.write_bytes(data)
        attachment = Attachment(
            merchant_id=merchant_id,
            customer_id=customer_id,
            uploaded_by_user_id=uploaded_by_user_id,
            original_name=_safe_name(original_name),
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_name=storage_name,
        )
        session.add(attachment)
        session.commit()
        return attachment
    except Exception:
        path.unlink(missing_ok=True)
        session.rollback()
        raise


def save_attachment(
    session: Session,
    customer: Customer,
    *,
    original_name: str,
    data: bytes,
) -> Attachment:
    return _save(
        session,
        merchant_id=customer.merchant_id,
        customer_id=customer.id,
        uploaded_by_user_id=None,
        original_name=original_name,
        data=data,
    )


def save_staff_attachment(
    session: Session,
    *,
    merchant_id: int,
    customer_id: int,
    user_id: int,
    original_name: str,
    data: bytes,
) -> Attachment:
    return _save(
        session,
        merchant_id=merchant_id,
        customer_id=customer_id,
        uploaded_by_user_id=user_id,
        original_name=original_name,
        data=data,
    )


def get_customer_attachment(
    session: Session, customer: Customer, attachment_id: int
) -> Attachment:
    attachment = session.execute(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.merchant_id == customer.merchant_id,
            Attachment.customer_id == customer.id,
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFoundError("附件不存在")
    _path_for(attachment)
    return attachment


def get_admin_attachment(
    session: Session, merchant_id: int, attachment_id: int
) -> Attachment:
    attachment = session.execute(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.merchant_id == merchant_id,
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFoundError("附件不存在")
    _path_for(attachment)
    return attachment


def attachment_path(attachment: Attachment) -> Path:
    path = _path_for(attachment)
    if not path.is_file():
        raise NotFoundError("附件不存在")
    return path


def validate_attachment_ids(
    session: Session, customer: Customer, attachment_ids: list[int]
) -> list[int]:
    unique_ids = list(dict.fromkeys(attachment_ids))
    if len(unique_ids) > MAX_ATTACHMENTS:
        raise ValidationError(f"最多上传 {MAX_ATTACHMENTS} 张图片")
    if not unique_ids:
        return []
    rows = session.execute(
        select(Attachment.id).where(
            Attachment.id.in_(unique_ids),
            Attachment.merchant_id == customer.merchant_id,
            Attachment.customer_id == customer.id,
        )
    ).scalars().all()
    if set(rows) != set(unique_ids):
        raise ValidationError("附件不存在或不属于当前顾客")
    return unique_ids


def validate_staff_attachment_ids(
    session: Session,
    merchant_id: int,
    customer_id: int,
    attachment_ids: list[int],
) -> list[int]:
    unique_ids = list(dict.fromkeys(attachment_ids))
    if len(unique_ids) > MAX_ATTACHMENTS:
        raise ValidationError(f"最多上传 {MAX_ATTACHMENTS} 张图片")
    if not unique_ids:
        return []
    rows = session.execute(
        select(Attachment.id).where(
            Attachment.id.in_(unique_ids),
            Attachment.merchant_id == merchant_id,
            Attachment.customer_id == customer_id,
            Attachment.uploaded_by_user_id.is_not(None),
        )
    ).scalars().all()
    if set(rows) != set(unique_ids):
        raise ValidationError("附件不存在或不是员工上传")
    return unique_ids
