from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.addresses import router as addresses_router
from app.api.admin import router as admin_router
from app.api.admin_actions import router as admin_actions_router
from app.api.admin_audit_anchors import router as admin_audit_anchors_router
from app.api.admin_coupons import router as admin_coupons_router
from app.api.admin_customers import router as admin_customers_router
from app.api.admin_finance import router as admin_finance_router
from app.api.admin_handoff_settings import router as admin_handoff_settings_router
from app.api.admin_inventory import router as admin_inventory_router
from app.api.admin_inventory_transfers import (
    router as admin_inventory_transfers_router,
)
from app.api.admin_orders import router as admin_orders_router
from app.api.admin_outbox import router as admin_outbox_router
from app.api.admin_performance import router as admin_performance_router
from app.api.admin_policies import router as admin_policies_router
from app.api.admin_products import router as admin_products_router
from app.api.admin_quick_replies import router as admin_quick_replies_router
from app.api.admin_staff import router as admin_staff_router
from app.api.after_sales import router as after_sales_router
from app.api.attachments import router as attachments_router
from app.api.cart import router as cart_router
from app.api.catalog import router as catalog_router
from app.api.chat import router as chat_router
from app.api.checkout import router as checkout_router
from app.api.coupon_claims import router as coupon_claims_router
from app.api.help import router as help_router
from app.api.invoices import router as invoices_router
from app.api.notifications import router as notifications_router
from app.api.orders import router as orders_router
from app.api.overview import router as overview_router
from app.api.payments import router as payments_router
from app.api.policies import router as policies_router
from app.api.repairs import router as repairs_router
from app.api.returns import router as returns_router
from app.api.reviews import router as reviews_router
from app.api.session import router as session_router
from app.api.shipping import router as shipping_router
from app.api.webhooks import router as webhooks_router
from app.db.session import database_ready
from app.observability import configure_tracing
from app.observability import install as install_observability
from app.security import install_security
from app.services.errors import RateLimitError, ServiceError
from app.settings import settings

app = FastAPI(title="AfterOps API", version="0.1.0")
install_observability(app)
install_security(app)
app.include_router(addresses_router)
app.include_router(admin_router)
app.include_router(attachments_router)
app.include_router(admin_products_router)
app.include_router(admin_actions_router)
app.include_router(admin_audit_anchors_router)
app.include_router(admin_coupons_router)
app.include_router(admin_customers_router)
app.include_router(admin_finance_router)
app.include_router(admin_handoff_settings_router)
app.include_router(admin_inventory_router)
app.include_router(admin_inventory_transfers_router)
app.include_router(admin_orders_router)
app.include_router(admin_outbox_router)
app.include_router(admin_performance_router)
app.include_router(admin_policies_router)
app.include_router(admin_quick_replies_router)
app.include_router(admin_staff_router)
app.include_router(after_sales_router)
app.include_router(catalog_router)
app.include_router(coupon_claims_router)
app.include_router(session_router)
app.include_router(cart_router)
app.include_router(chat_router)
app.include_router(help_router)
app.include_router(invoices_router)
app.include_router(checkout_router)
app.include_router(notifications_router)
app.include_router(orders_router)
app.include_router(overview_router)
app.include_router(payments_router)
app.include_router(policies_router)
app.include_router(repairs_router)
app.include_router(returns_router)
app.include_router(reviews_router)
app.include_router(shipping_router)
app.include_router(webhooks_router)
configure_tracing(app)


@app.exception_handler(ServiceError)
async def service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
    headers = None
    if isinstance(exc, RateLimitError) and exc.retry_after:
        headers = {"Retry-After": str(exc.retry_after)}
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
        headers=headers,
    )


@app.get("/")
def root() -> dict:
    return {"service": "afterops-api", "docs": "/docs"}


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "afterops-api",
        "env": settings.app_env,
        "version": "0.1.0",
    }


@app.get("/health/ready")
def readiness() -> JSONResponse:
    ok, detail = database_ready()
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "unavailable", "database": detail},
    )

