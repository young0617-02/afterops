from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import AdminDep
from app.api.deps import SessionDep
from app.services.audit_anchors import read_audit_anchors

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/audit/anchors")
def admin_audit_anchors(
    admin: AdminDep,
    session: SessionDep,
) -> dict:
    return read_audit_anchors(session, admin.merchant_id)
