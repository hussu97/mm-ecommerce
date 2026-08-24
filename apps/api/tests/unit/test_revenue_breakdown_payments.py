"""
Splitting the payment breakdown into the two questions it was answering badly.

`{stripe, cod}` read naturally as "how did customers pay" for exactly as long as
there was one card processor. With two, `{stripe, ziina, cod}` puts a commercial
question and an operational one on the same axis and answers neither: the card
share of takings now moves whenever traffic is routed differently, which is a
number nobody wanted to depend on the gateway.

The subtlety worth pinning is the backfill's long tail. Every card order written
before methods and gateways were split holds `stripe` in `payment_method` — the
089 migration rewrote the rows that existed, but the *reading* still has to
tolerate one, because a restored backup or a replayed export can put one back.
Left un-normalised it draws a phantom third slice that shrinks as old orders age
out of the window, which looks exactly like a real trend.
"""

from __future__ import annotations

import pytest

from app.api.v1.analytics.commerce import _merged, _payment_method_label


class TestTheMethodLabel:
    @pytest.mark.parametrize("stored", ["card", "stripe", "ziina", "CARD", " Stripe "])
    def test_every_card_word_is_one_slice(self, stored):
        assert _payment_method_label(stored) == "card"

    @pytest.mark.parametrize("stored", ["cod", "COD", " cod "])
    def test_cash_is_cash(self, stored):
        assert _payment_method_label(stored) == "cod"

    def test_nothing_stored_is_named_rather_than_dropped(self):
        assert _payment_method_label(None) == "unknown"
        assert _payment_method_label("") == "unknown"

    def test_an_unrecognised_value_is_shown_as_itself(self):
        """
        Deliberately not an exception, unlike `normalise_method` at the edge of
        the system. A breakdown that hides a row it did not expect is a
        breakdown whose total quietly stops adding up — and `tabby` turning up
        here would be a real thing somebody needs to see, not a bug to raise on.
        """
        assert _payment_method_label("tabby") == "tabby"


class TestMerging:
    def test_legacy_and_current_card_rows_become_one(self):
        """
        Two GROUP BY rows land on the same label. Un-merged, the chart shows
        "card" twice and the split falls wherever the deploy happened to land.
        """
        merged = _merged(
            [
                ("card", 10, 1000.0),
                ("cod", 3, 150.0),
                ("card", 5, 500.0),  # the legacy `stripe` rows, relabelled
            ]
        )

        assert [(i.label, i.orders, i.revenue) for i in merged] == [
            ("card", 15, 1500.0),
            ("cod", 3, 150.0),
        ]

    def test_biggest_revenue_first(self):
        merged = _merged([("cod", 1, 10.0), ("card", 1, 900.0)])

        assert [i.label for i in merged] == ["card", "cod"]

    def test_nothing_in_the_window_is_an_empty_list(self):
        assert _merged([]) == []


def test_cash_is_not_a_gateway():
    """
    The gateway chart is card-only. A `cod` slice in a chart about processors is
    the same conflation this whole split was made to remove — and `none`, which
    a fully-discounted zero-total order writes, is not one either.
    """
    from app.api.v1.analytics.commerce import _NOT_A_GATEWAY

    assert "cod" in _NOT_A_GATEWAY
    assert "none" in _NOT_A_GATEWAY
    assert "stripe" not in _NOT_A_GATEWAY
    assert "ziina" not in _NOT_A_GATEWAY
