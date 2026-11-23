from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.admin import AdminDep
from app.api.deps import CurrentCustomer, SessionDep
from app.services.attachments import (
    attachment_path,
    get_admin_attachment,
    get_customer_attachment,
    save_attachment,
)
from app.services.handoffs import add_ticket_attachment
from app.settings import settings

router = APIRouter(tags=["attachments"])


class AttachmentOut(BaseModel):
    id: int
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str


def _out(attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        original_name=attachment.original_name,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        sha256=attachment.sha256,
    )


def _file(attachment) -> FileResponse:
    encoded = quote(attachment.original_name)
    return FileResponse(
        attachment_path(attachment),
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": (
                f'inline; filename="evidence"; filename*=UTF-8\'\'{encoded}'
            )
        },
    )


@router.post("/api/store/attachments", response_model=AttachmentOut)
async def upload_attachment(
    file: Annotated[UploadFile, File()],
    customer: CurrentCustomer,
    session: SessionDep,
) -> AttachmentOut:
    data = await file.read(settings.upload_max_bytes + 1)
    attachment = save_attachment(
        session,
        customer,
        original_name=file.filename or "evidence",
        data=data,
    )
    return _out(attachment)


@router.post(
    "/api/admin/handoffs/{ticket_id}/attachments",
    response_model=AttachmentOut,
)
async def upload_handoff_attachment(
    ticket_id: int,
    file: Annotated[UploadFile, File()],
    admin: AdminDep,
    session: SessionDep,
) -> AttachmentOut:
    data = await file.read(settings.upload_max_bytes + 1)
    attachment = add_ticket_attachment(
        session,
        admin.merchant_id,
        admin.user_id,
        ticket_id,
        original_name=file.filename or "evidence",
        data=data,
    )
    return _out(attachment)


@router.get("/api/store/attachments/{attachment_id}")
def customer_attachment(
    attachment_id: int,
    customer: CurrentCustomer,
    session: SessionDep,
) -> FileResponse:
    return _file(get_customer_attachment(session, customer, attachment_id))


@router.get("/api/admin/attachments/{attachment_id}")
def admin_attachment(
    attachment_id: int,
    admin: AdminDep,
    session: SessionDep,
) -> FileResponse:
    return _file(get_admin_attachment(session, admin.merchant_id, attachment_id))
