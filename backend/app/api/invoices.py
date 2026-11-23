from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.admin import AdminDep, OwnerDep
from app.api.deps import CurrentCustomer, SessionDep
from app.services.invoices import (
    InvoiceView,
    create_invoice,
    issue_invoice,
    list_invoices_for_admin,
    list_invoices_for_customer,
    reject_invoice,
    void_invoice,
)

router = APIRouter(tags=["invoices"])


class InvoiceOut(BaseModel):
    id: int
    order_no: str
    invoice_type: str
    title: str
    tax_no: str
    email: str
    amount_cents: int
    net_paid_cents: int
    adjustment_required: bool
    status: str
    invoice_no: str
    reject_reason: str
    void_reason: str
    created_at: datetime
    issued_at: datetime | None
    voided_at: datetime | None


class CreateInvoiceIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=32)
    invoice_type: Literal["personal", "company"]
    title: str = Field(min_length=1, max_length=120)
    tax_no: str = Field(default="", max_length=32)
    email: str = Field(default="", max_length=255)


class IssueInvoiceIn(BaseModel):
    invoice_no: str = Field(min_length=1, max_length=64)


class RejectInvoiceIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class VoidInvoiceIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def _out(view: InvoiceView) -> InvoiceOut:
    return InvoiceOut(
        id=view.id,
        order_no=view.order_no,
        invoice_type=view.invoice_type,
        title=view.title,
        tax_no=view.tax_no,
        email=view.email,
        amount_cents=view.amount_cents,
        net_paid_cents=view.net_paid_cents,
        adjustment_required=view.adjustment_required,
        status=view.status,
        invoice_no=view.invoice_no,
        reject_reason=view.reject_reason,
        void_reason=view.void_reason,
        created_at=view.created_at,
        issued_at=view.issued_at,
        voided_at=view.voided_at,
    )


@router.get("/api/store/invoices", response_model=list[InvoiceOut])
def customer_invoices(
    customer: CurrentCustomer, session: SessionDep
) -> list[InvoiceOut]:
    return [
        _out(view) for view in list_invoices_for_customer(session, customer)
    ]


@router.post("/api/store/invoices", response_model=InvoiceOut)
def customer_create_invoice(
    payload: CreateInvoiceIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> InvoiceOut:
    return _out(
        create_invoice(
            session,
            customer,
            payload.order_no,
            payload.invoice_type,
            payload.title,
            payload.tax_no,
            payload.email,
        )
    )


@router.get("/api/admin/invoices", response_model=list[InvoiceOut])
def admin_invoices(
    admin: AdminDep,
    session: SessionDep,
    status: Annotated[
        Literal[
            "all",
            "requested",
            "issued",
            "rejected",
            "voided",
            "needs_adjustment",
        ],
        Query(),
    ] = "all",
) -> list[InvoiceOut]:
    return [
        _out(view)
        for view in list_invoices_for_admin(
            session, admin.merchant_id, status
        )
    ]


@router.post("/api/admin/invoices/{invoice_id}/issue", response_model=InvoiceOut)
def admin_issue_invoice(
    invoice_id: int,
    payload: IssueInvoiceIn,
    admin: OwnerDep,
    session: SessionDep,
) -> InvoiceOut:
    return _out(
        issue_invoice(
            session,
            admin.merchant_id,
            admin.user_id,
            invoice_id,
            payload.invoice_no,
        )
    )


@router.post("/api/admin/invoices/{invoice_id}/void", response_model=InvoiceOut)
def admin_void_invoice(
    invoice_id: int,
    payload: VoidInvoiceIn,
    admin: OwnerDep,
    session: SessionDep,
) -> InvoiceOut:
    return _out(
        void_invoice(
            session,
            admin.merchant_id,
            admin.user_id,
            invoice_id,
            payload.reason,
        )
    )


@router.post("/api/admin/invoices/{invoice_id}/reject", response_model=InvoiceOut)
def admin_reject_invoice(
    invoice_id: int,
    payload: RejectInvoiceIn,
    admin: OwnerDep,
    session: SessionDep,
) -> InvoiceOut:
    return _out(
        reject_invoice(
            session,
            admin.merchant_id,
            admin.user_id,
            invoice_id,
            payload.reason,
        )
    )
