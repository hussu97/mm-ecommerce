"""
"Has this order been paid for?" — the question that stopped having a cheap answer.

For as long as there was one processor, the answer was a string prefix. A Stripe
Checkout Session is `cs_…` and the Payment Intent it becomes is `pi_…`, so
`order.payment_id.startswith("pi_")` meant "the customer got to the end". It was
inference, but it was correct inference, and it guarded the one thing that must
not be got wrong: whether a second payment session may be opened.

Ziina breaks it. Their payment intent is minted at *session creation*, so `pi_…`
is sitting on `order.payment_id` the instant the customer is redirected — before
they have typed a card number. Under the old rule every abandoned Ziina checkout
reads as paid.

So paid-ness became a settled `payment_transactions` row, and the prefix check
survives only for orders written before that table existed. These tests pin both
halves, and the trap between them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.payment_transaction import PaymentTransactionStatusEnum
from app.services.payment_service import _is_paid


def _order(*, provider=None, payment_id=None, transactions=()):
    return SimpleNamespace(
        payment_provider=provider,
        payment_id=payment_id,
        payment_transactions=list(transactions),
    )


def _txn(status: PaymentTransactionStatusEnum, gateway="stripe"):
    return SimpleNamespace(
        gateway=gateway,
        status=status.value,
        is_settled=status is PaymentTransactionStatusEnum.SUCCEEDED,
    )


class TestTheTransactionIsTheAnswer:
    def test_a_settled_attempt_means_paid(self):
        order = _order(
            provider="ziina",
            payment_id="pi_ziina_1",
            transactions=[_txn(PaymentTransactionStatusEnum.SUCCEEDED, "ziina")],
        )

        assert _is_paid(order)

    @pytest.mark.parametrize(
        "status",
        [
            PaymentTransactionStatusEnum.PENDING,
            PaymentTransactionStatusEnum.FAILED,
            PaymentTransactionStatusEnum.CANCELLED,
        ],
    )
    def test_an_unsettled_attempt_does_not(self, status):
        order = _order(
            provider="ziina",
            payment_id="pi_ziina_1",
            transactions=[_txn(status, "ziina")],
        )

        assert not _is_paid(order)

    def test_one_settled_attempt_among_failures_is_enough(self):
        """A failover leaves the abandoned attempt behind; it must not vote."""
        order = _order(
            provider="ziina",
            payment_id="pi_ziina_1",
            transactions=[
                _txn(PaymentTransactionStatusEnum.FAILED, "stripe"),
                _txn(PaymentTransactionStatusEnum.SUCCEEDED, "ziina"),
            ],
        )

        assert _is_paid(order)


class TestTheZiinaTrap:
    def test_a_pending_ziina_intent_is_not_a_payment(self):
        """
        The bug this whole redesign exists to prevent.

        Under the old prefix rule this order reads as paid — `payment_id` starts
        with `pi_` — and the customer, who abandoned the checkout, can never
        open another payment session for it. The prefix check is scoped to
        Stripe for exactly this reason.
        """
        order = _order(
            provider="ziina",
            payment_id="pi_ziina_abandoned",
            transactions=[_txn(PaymentTransactionStatusEnum.PENDING, "ziina")],
        )

        assert not _is_paid(order)

    def test_even_with_no_transaction_row_at_all(self):
        order = _order(provider="ziina", payment_id="pi_ziina_abandoned")

        assert not _is_paid(order)


class TestTheLegacyOrders:
    """
    Orders written before `payment_transactions` existed have no row and nothing
    but the prefix to recover their state from. Dropping the fallback would
    silently reopen a payment window on every one of them.
    """

    def test_a_confirmed_stripe_intent_still_reads_as_paid(self):
        assert _is_paid(_order(provider="stripe", payment_id="pi_live_123"))

    def test_a_pending_stripe_session_does_not(self):
        assert not _is_paid(_order(provider="stripe", payment_id="cs_test_456"))

    def test_a_null_provider_is_treated_as_stripe(self):
        """The only gateway that existed when those rows were written."""
        assert _is_paid(_order(provider=None, payment_id="pi_live_123"))

    def test_an_order_with_no_payment_at_all_is_not_paid(self):
        assert not _is_paid(_order())
