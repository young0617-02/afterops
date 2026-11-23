from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import (
    ActionProposal,
    AfterSalesCase,
    AuditEvent,
    Cart,
    CartItem,
    Conversation,
    Customer,
    IdempotencyKey,
    Inventory,
    InventoryReservation,
    Merchant,
    Message,
    Order,
    OrderItem,
    Outbox,
    Payment,
    PolicyEntry,
    Product,
    Shipment,
    ShipmentEvent,
    User,
    WebhookEvent,
)


def _seed(session: Session) -> dict[str, int]:
    now = datetime.now(UTC)
    today = now.date()
    merchant = Merchant(name="测试 3C 店")
    session.add(merchant)
    session.flush()

    user = User(
        merchant_id=merchant.id,
        email="owner@example.com",
        password_hash="hashed",
        role="owner",
    )
    customer = Customer(merchant_id=merchant.id, phone="13800000000", name="顾客 A")
    product = Product(
        merchant_id=merchant.id,
        sku="SKU-EAR-001",
        title="降噪耳机",
        price_cents=19900,
    )
    session.add_all([user, customer, product])
    session.flush()

    inventory = Inventory(
        merchant_id=merchant.id,
        product_id=product.id,
        qty_available=10,
        qty_reserved=0,
    )
    cart = Cart(merchant_id=merchant.id, customer_id=customer.id)
    session.add_all([inventory, cart])
    session.flush()

    cart_item = CartItem(
        cart_id=cart.id,
        product_id=product.id,
        qty=1,
        unit_price_cents=product.price_cents,
    )
    order = Order(
        order_no="ORD-2026-000001",
        merchant_id=merchant.id,
        customer_id=customer.id,
        item_total_cents=product.price_cents,
        payable_cents=product.price_cents,
    )
    session.add_all([cart_item, order])
    session.flush()

    order_item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        sku=product.sku,
        title_snapshot=product.title,
        qty=1,
        unit_price_cents=product.price_cents,
    )
    payment = Payment(
        order_id=order.id,
        merchant_id=merchant.id,
        amount_cents=order.payable_cents,
        idempotency_key="pay-ord-1",
    )
    reservation = InventoryReservation(
        merchant_id=merchant.id,
        order_id=order.id,
        product_id=product.id,
        qty=1,
    )
    shipment = Shipment(
        order_id=order.id,
        merchant_id=merchant.id,
        tracking_no="MOCK-0001",
        status="in_transit",
    )
    session.add_all([order_item, payment, reservation, shipment])
    session.flush()

    shipment_event = ShipmentEvent(
        shipment_id=shipment.id,
        happened_at=now,
        node="运输中",
        description="已到达深圳分拨中心",
    )
    conversation = Conversation(merchant_id=merchant.id, customer_id=customer.id)
    policy = PolicyEntry(
        merchant_id=merchant.id,
        entry_id="POLICY-RETURN",
        topic="return",
        title="退货政策",
        body="签收后 7 天内可申请无理由退货。",
        version="v1",
        valid_from=today,
        published_at=today,
        source="测试政策",
    )
    session.add_all([shipment_event, conversation, policy])
    session.flush()

    message = Message(conversation_id=conversation.id, role="user", content="耳机有杂音，我要退款")
    case = AfterSalesCase(
        merchant_id=merchant.id,
        order_id=order.id,
        type="refund",
        reason="商品有杂音",
        amount_cents=order.payable_cents,
    )
    session.add_all([message, case])
    session.flush()

    proposal = ActionProposal(
        merchant_id=merchant.id,
        conversation_id=conversation.id,
        after_sales_case_id=case.id,
        action_type="refund",
        payload_json={"amount_cents": order.payable_cents, "reason": "商品有杂音"},
        risk_level="L2",
        idempotency_key="proposal-ord-1",
    )
    session.add(proposal)
    session.flush()

    audit = AuditEvent(
        merchant_id=merchant.id,
        conversation_id=conversation.id,
        proposal_id=proposal.id,
        event_type="proposal_created",
        payload_json={"amount_cents": order.payable_cents},
        prev_hash="GENESIS",
        row_hash="hash-1",
    )
    outbox = Outbox(
        merchant_id=merchant.id,
        event_type="proposal.created",
        aggregate_type="action_proposal",
        aggregate_id=str(proposal.id),
        payload_json={"proposal_id": proposal.id},
    )
    idem = IdempotencyKey(key="checkout-1", scope="checkout")
    webhook = WebhookEvent(
        provider="mock",
        event_id="evt-1",
        event_type="payment.paid",
        payload_json={"order_id": order.id},
    )
    session.add_all([audit, outbox, idem, webhook])
    session.commit()
    return {
        "merchant": merchant.id,
        "user": user.id,
        "customer": customer.id,
        "product": product.id,
        "order": order.id,
        "proposal": proposal.id,
        "case": case.id,
        "conversation": conversation.id,
    }


def test_minimal_graph_persists(db_session: Session) -> None:
    ids = _seed(db_session)
    assert all(ids.values())

    order = db_session.get(Order, ids["order"])
    assert order is not None
    assert order.payable_cents == 19900
    assert order.status == "pending_payment"

    proposal = db_session.get(ActionProposal, ids["proposal"])
    assert proposal is not None
    assert proposal.status == "pending"
    assert proposal.payload_json["amount_cents"] == 19900


def test_duplicate_sku_rejected(db_session: Session) -> None:
    ids = _seed(db_session)
    db_session.add(
        Product(
            merchant_id=ids["merchant"],
            sku="SKU-EAR-001",
            title="重复 SKU",
            price_cents=100,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_negative_price_rejected(db_session: Session) -> None:
    ids = _seed(db_session)
    db_session.add(
        Product(
            merchant_id=ids["merchant"],
            sku="SKU-NEG-001",
            title="负价商品",
            price_cents=-1,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_invalid_order_status_rejected(db_session: Session) -> None:
    ids = _seed(db_session)
    db_session.add(
        Order(
            order_no="ORD-BAD-STATUS",
            merchant_id=ids["merchant"],
            customer_id=ids["customer"],
            status="bogus",
            item_total_cents=100,
            payable_cents=100,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_proposal_idempotency_key_unique(db_session: Session) -> None:
    ids = _seed(db_session)
    db_session.add(
        ActionProposal(
            merchant_id=ids["merchant"],
            action_type="refund",
            payload_json={"amount_cents": 100},
            risk_level="L2",
            idempotency_key="proposal-ord-1",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_policy_validity_order_rejected(db_session: Session) -> None:
    ids = _seed(db_session)
    db_session.add(
        PolicyEntry(
            merchant_id=ids["merchant"],
            entry_id="POLICY-BAD",
            topic="return",
            title="错误有效期",
            body="...",
            version="v1",
            valid_from=date(2026, 1, 1),
            valid_until=date(2025, 12, 31),
            published_at=date(2026, 1, 1),
            source="测试",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
