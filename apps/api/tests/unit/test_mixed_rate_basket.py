"""
A mixed-rate basket stamps `orders.vat_rate` at four places, not two.

`orders.vat_rate` is `Numeric(5,4)` — a fraction with four decimal places. A
basket that mixes tax rates has no single rate, so the column carries the
*blended* effective rate instead. That figure was being quantised through
`money()` (two places), which rounds a rate like 0.0350 to 0.04 — a 14% error in
the stored rate, and enough to make the order's three VAT columns stop
reconciling: `total_excl_vat * vat_rate` no longer lands on `vat_amount`.

A rate is not a money figure. `money.rate()` keeps the four places the column
was built for, and the reconciliation holds.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.services.orders import order_service
from tests.unit.test_website_tax_groups import _Db, _Group, _Tax

STANDARD = uuid.uuid4()
REDUCED = uuid.uuid4()


def _db():
    return _Db(
        {
            STANDARD: _Group(_Tax(Decimal("0.05"))),
            REDUCED: _Group(_Tax(Decimal("0.02"), name="Reduced")),
        }
    )


async def test_a_blended_rate_keeps_four_decimal_places():
    """
    Two rates, blended to exactly 0.035. Under `money()` that stored as 0.04.
    Under `money.rate()` it keeps 0.0350 — the shape `Numeric(5,4)` was made for.
    """
    result = await order_service.tax_breakdown(
        _db(),
        # 105 @ 5% inclusive → 5.00 tax; 102 @ 2% inclusive → 2.00 tax.
        lines=[(STANDARD, Decimal("105.00")), (REDUCED, Decimal("102.00"))],
        discount_amount=Decimal("0"),
    )

    assert len(result.lines) == 2, "two rates are two invoice rows"
    assert result.total == Decimal("7.00")
    # 7 / (207 - 7) = 0.035. Quantised to four places, not rounded to the cent.
    assert result.rate == Decimal("0.0350")
    assert result.rate != Decimal("0.04"), "money() would have rounded it here"


async def test_the_three_vat_figures_reconcile():
    """
    The whole point of storing four places: `total_excl_vat * vat_rate` must land
    back on `vat_amount`. At two places (0.04) it would not.
    """
    result = await order_service.tax_breakdown(
        _db(),
        lines=[(STANDARD, Decimal("105.00")), (REDUCED, Decimal("102.00"))],
        discount_amount=Decimal("0"),
    )

    gross = Decimal("207.00")
    vat_amount = result.total
    total_excl_vat = gross - vat_amount  # the net the order stores

    # The rate is exactly what turns the net back into the tax.
    reconstructed = (total_excl_vat * result.rate).quantize(Decimal("0.01"))
    assert reconstructed == vat_amount

    # And the rounded-to-cent rate would break it, which is the bug.
    broken = (total_excl_vat * Decimal("0.04")).quantize(Decimal("0.01"))
    assert broken != vat_amount
