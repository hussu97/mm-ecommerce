"""
The register opens counter checks only, and closes only what is settled.

Two halves of one hole (F-POS-2): the open request accepted `source="online"`,
letting a terminal mint a check the close guard then treated as prepaid and
closed for free (fees stamped, stock depleted, counted as revenue). The schema
now pins the open `source` to `cashier`, and the close guard is POSITIVE — a
check that still owes money does not close unless it settled at its own channel.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.pos_order import OpenOrderRequest
from app.services.pos import pos_order_service


class TestOpenSourceIsCashierOnly:
    def test_cashier_is_accepted(self):
        req = OpenOrderRequest(branch_id=uuid.uuid4(), source="cashier")
        assert req.source == "cashier"

    def test_default_is_cashier(self):
        assert OpenOrderRequest(branch_id=uuid.uuid4()).source == "cashier"

    @pytest.mark.parametrize("channel", ["online", "aggregator", "api", "call_center"])
    def test_a_non_cashier_source_is_rejected(self, channel):
        with pytest.raises(ValidationError):
            OpenOrderRequest(branch_id=uuid.uuid4(), source=channel)


class TestCloseBalanceGuard:
    def _order(self, *, source, balance, is_pos=True):
        return SimpleNamespace(
            source=source,
            is_pos=is_pos,
            balance_due=Decimal(str(balance)),
        )

    def test_cashier_check_with_balance_is_blocked(self):
        order = self._order(source="cashier", balance="50.00")
        assert pos_order_service._close_balance_blocked(order) is True

    def test_cashier_check_fully_paid_closes(self):
        order = self._order(source="cashier", balance="0.00")
        assert pos_order_service._close_balance_blocked(order) is False

    @pytest.mark.parametrize("channel", ["online", "aggregator"])
    def test_externally_settled_check_closes_despite_full_balance(self, channel):
        # An online/aggregator order writes no OrderPayment, so balance_due is the
        # whole total forever — it must still close.
        order = self._order(source=channel, balance="120.00")
        assert pos_order_service._close_balance_blocked(order) is False
        assert pos_order_service._settled_externally(order) is True
