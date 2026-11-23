from __future__ import annotations

import uuid
from pathlib import Path

from app.services.errors import NotFoundError, ValidationError
from app.settings import settings

ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def detect_image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image(data: bytes) -> str:
    if not data:
        raise ValidationError("图片为空")
    if len(data) > settings.upload_max_bytes:
        raise ValidationError(
            f"图片不能超过 {settings.upload_max_bytes // (1024 * 1024)}MB"
        )
    content_type = detect_image_type(data)
    if content_type is None:
        raise ValidationError("只支持 PNG、JPEG、WebP 图片")
    return content_type


def image_dir(subdir: str = "") -> Path:
    directory = (Path(settings.upload_dir).resolve() / subdir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_image_file(subdir: str, data: bytes, content_type: str) -> str:
    filename = f"{uuid.uuid4().hex}{ALLOWED_IMAGE_TYPES[content_type]}"
    path = image_dir(subdir) / filename
    path.write_bytes(data)
    return filename


def image_file_path(subdir: str, filename: str) -> Path:
    directory = image_dir(subdir)
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise NotFoundError("图片不存在")
    return path


def delete_image_file(subdir: str, filename: str) -> None:
    directory = image_dir(subdir)
    path = (directory / filename).resolve()
    if path.is_relative_to(directory):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
