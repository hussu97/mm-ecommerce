"""
Sending money back, and the two ways that goes wrong.

Refunds are the only thing in this codebase that moves money *outward*, and
both failure modes are expensive in opposite directions: refunding twice pays a
customer who was owed once, and refunding the fees pays for a van that was
booked and often already drove. Everything here is about those two.

Written against MM-20260815-002, which was cancelled on 15 Aug 2026 with the
customer's AED 45 still charged, because until this existed nothing in the
application could call a gateway back.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

from app.services import payment_service


def _order(
    total="105.00",
    delivery_fee="20.00",
    low_order_fee="0.00",
    refunded="0.00",
    payment_method="card",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260815-002",
        total=Decimal(total),
        delivery_fee=Decimal(delivery_fee),
        low_order_fee=Decimal(low_order_fee),
        refunded_amount=Decimal(refunded),
        payment_method=payment_method,
        currency="AED",
        payment_transactions=[],
    )


# ── what comes back ───────────────────────────────────────────────────────────


def test_the_customer_gets_the_goods_back_and_not_the_fees():
    # 105 charged: 85 of cake, 20 of van. The van happened.
    assert payment_service.refundable_amount(_order()) == Decimal("85.00")


def test_a_low_order_fee_is_kept_too():
    order = _order(total="60.00", delivery_fee="20.00", low_order_fee="5.00")
    assert payment_service.refundable_amount(order) == Decimal("35.00")


def test_a_free_delivery_order_refunds_the_whole_thing():
    order = _order(total="45.00", delivery_fee="0.00")
    assert payment_service.refundable_amount(order) == Decimal("45.00")


def test_a_discount_is_already_in_the_total_and_is_not_added_back():
    """
    The refund is what the card was charged for goods, not what the goods list
    at. Reading `total` rather than recomputing from subtotal is what keeps
    those the same number — any drift shows up as a refund the customer
    disputes.
    """
    # 100 of cake, 15 off, 20 delivery → 105 charged, 85 of it cake.
    order = _order(total="105.00", delivery_fee="20.00")
    assert payment_service.refundable_amount(order) == Decimal("85.00")


def test_a_partial_refund_already_made_is_taken_off():
    # Somebody refunded 30 in the Stripe dashboard first.
    order = _order(refunded="30.00")
    assert payment_service.refundable_amount(order) == Decimal("75.00")


def test_it_never_overdraws_the_charge():
    """
    A dashboard refund larger than the goods value must not leave this
    computing a second refund on top of it.
    """
    order = _order(refunded="100.00")
    assert payment_service.refundable_amount(order) == Decimal("5.00")

    order = _order(refunded="105.00")
    assert payment_service.refundable_amount(order) == Decimal("0.00")


def test_fees_larger_than_the_total_do_not_produce_a_negative_refund():
    # Not reachable through the checkout, but a refund of minus twelve dirhams
    # is the kind of thing that reaches a bank before anybody notices.
    order = _order(total="10.00", delivery_fee="20.00")
    assert payment_service.refundable_amount(order) == Decimal("0.00")


# ── asking twice ──────────────────────────────────────────────────────────────


def test_the_idempotency_key_is_the_same_for_the_same_refund():
    """
    The property the whole design rests on. Two callers that cannot see each
    other — a status transition and the retry behind it — must produce one
    refund, and they do it by producing one key. Stripe takes it as its
    idempotency header; Ziina takes it as the refund's own primary key, which is
    stronger.
    """
    order = _order()
    first = payment_service._refund_key(order, Decimal("85.00"))
    second = payment_service._refund_key(_order(), Decimal("85.00"))
    assert first == second
    assert order.order_number in first


def test_a_different_amount_is_a_different_refund():
    order = _order()
    assert payment_service._refund_key(order, Decimal("85.00")) != (
        payment_service._refund_key(order, Decimal("40.00"))
    )


# ── what is not refunded at all ───────────────────────────────────────────────


async def test_a_cash_order_refunds_nothing_here():
    """There is no gateway to call. The shop hands the money back."""
    order = _order(payment_method="cod")
    assert await payment_service.refund_order(None, order) == Decimal("0")


async def test_an_order_with_no_settled_attempt_refunds_nothing():
    """
    A card order whose payment never reached a handle — an abandoned checkout
    that was cancelled later. There is nothing to refund against.
    """
    order = _order()
    order.payment_transactions = [
        SimpleNamespace(
            is_settled=False, payment_id=None, gateway="stripe", refund_id=None
        )
    ]
    assert await payment_service.refund_order(None, order) == Decimal("0")


async def test_an_order_already_refunded_through_here_is_left_alone():
    order = _order()
    order.payment_transactions = [
        SimpleNamespace(
            is_settled=True, payment_id="pi_123", gateway="stripe", refund_id="re_123"
        )
    ]
    assert await payment_service.refund_order(None, order) == Decimal("0")


async def test_a_gateway_this_build_cannot_refund_does_not_raise():
    """
    Cancelling is a fact about the kitchen; refunding is a fact about a bank.
    A shop must never be unable to stop making a cake because the second one
    failed.
    """
    order = _order()
    order.payment_transactions = [
        SimpleNamespace(
            is_settled=True, payment_id="x", gateway="tabby", refund_id=None
        )
    ]
    assert await payment_service.refund_order(None, order) == Decimal("0")
