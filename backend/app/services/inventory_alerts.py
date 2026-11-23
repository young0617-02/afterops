from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.models import Product
from app.services.notifications import notify_staff
from app.settings import settings


def maybe_notify_low_stock(
    session: Session,
    product: Product,
    warehouse_id: str,
    *,
    previous_qty: int,
    current_qty: int,
) -> bool:
    threshold = settings.low_stock_threshold
    crossed = previous_qty > threshold >= current_qty
    new_low = previous_qty == 0 and 0 < current_qty <= threshold
    if not (crossed or new_low):
        return False
    notify_staff(
        session,
        product.merchant_id,
        notification_type="low_stock",
        title=f"库存不足：{product.sku}",
        body=(
            f"{product.title} / {warehouse_id} 可售 {current_qty}，"
            f"阈值 {threshold}"
        ),
        link="/admin",
    )
    return True
