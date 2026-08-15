"""
The storefront charges VAT from each product's tax group, like the counter does.

It used to read a module constant and write no `order_taxes` rows at all. Two
consequences, one visible and one not: a website receipt printed no VAT
breakdown while a counter receipt printed one from the same table, and a
zero-rated product would have been charged 5% anyway. Tax groups were never
counter-specific; only the code that read them was.

Every active product is in one 5% inclusive group today, so none of this changes
a price. It changes what happens when one of them is not.
"""

from __future__ import annotations

import uuid
from decimal import Decimal


from app.services import order_service, pos_pricing


class _Tax:
    def __init__(self, rate, name="VAT", is_active=True, kind="inclusive"):
        self.id = uuid.uuid4()
        self.rate = rate
        self.name = name
        self.is_active = is_active
        self.type = kind


class _Link:
    def __init__(self, tax):
        self.tax = tax


class _Group:
    def __init__(self, *taxes):
        self.taxes = [_Link(t) for t in taxes]


class _Db:
    """Answers the one query `_resolve_tax` makes, per group id."""

    def __init__(self, groups: dict):
        self.groups = groups

    async def execute(self, statement):
        # The group id is the only bound parameter that matters here.
        wanted = list(statement.compile().params.values())[0]

        class _R:
            def __init__(self, group):
                self._group = group

            def scalars(self):
                return self

            def unique(self):
                return self

            def one_or_none(self):
                return self._group

        return _R(self.groups.get(wanted))


STANDARD = uuid.uuid4()
ZERO_RATED = uuid.uuid4()


def _db():
    return _Db(
        {
            STANDARD: _Group(_Tax(Decimal("0.05"))),
            ZERO_RATED: _Group(_Tax(Decimal("0"), name="Zero-rated")),
        }
    )


# ── the ordinary case ─────────────────────────────────────────────────────────


async def test_one_group_gives_one_invoice_row_at_the_groups_rate():
    result = await order_service.tax_breakdown(
        _db(),
        lines=[(STANDARD, Decimal("100.00")), (STANDARD, Decimal("50.00"))],
        discount_amount=Decimal("0"),
    )
    assert len(result.lines) == 1, "four lines on one rate are one invoice row"
    assert result.total == Decimal("7.14")  # 150 * 0.05 / 1.05
    assert result.rate == Decimal("0.05")


async def test_the_arithmetic_matches_what_the_constant_used_to_produce():
    """
    The safety property of the whole change: today's catalogue is one 5%
    inclusive group, so nothing about today's prices moves.
    """
    via_groups = await order_service.tax_breakdown(
        _db(), lines=[(STANDARD, Decimal("100.00"))], discount_amount=Decimal("0")
    )
    # What the flat-rate path did, written out rather than called: the function
    # that used to hold it is gone, because the groups are the only place the
    # rate is read from now. This is the arithmetic it performed.
    _, via_constant = pos_pricing.split_inclusive_tax(
        Decimal("100.00"), order_service.VAT_RATE
    )
    assert via_groups.total == via_constant


# ── the cases it exists for ───────────────────────────────────────────────────


async def test_a_zero_rated_group_is_honoured_rather_than_charged_anyway():
    result = await order_service.tax_breakdown(
        _db(), lines=[(ZERO_RATED, Decimal("100.00"))], discount_amount=Decimal("0")
    )
    assert result.total == Decimal("0")
    assert result.lines == [], "nothing to declare is not a row saying zero"


async def test_a_mixed_basket_declares_each_rate_separately():
    result = await order_service.tax_breakdown(
        _db(),
        lines=[(STANDARD, Decimal("105.00")), (ZERO_RATED, Decimal("100.00"))],
        discount_amount=Decimal("0"),
    )
    assert result.total == Decimal("5.00"), "only the standard line is taxed"
    assert len(result.lines) == 1


async def test_a_discount_is_spread_pro_rata_across_rates():
    """
    The reason the discount cannot simply come off the taxable base. Knocking 20
    off a basket of one standard and one zero-rated line must reduce the
    standard line's base by its share and no more, or the shop pays VAT on money
    it never received.
    """
    result = await order_service.tax_breakdown(
        _db(),
        lines=[(STANDARD, Decimal("105.00")), (ZERO_RATED, Decimal("105.00"))],
        discount_amount=Decimal("20.00"),
    )
    # Half the discount lands on the standard line: 105 - 10 = 95 gross.
    assert result.total == Decimal("4.52")  # 95 * 0.05 / 1.05


# ── the guard ─────────────────────────────────────────────────────────────────


async def test_a_product_with_no_group_is_charged_rather_than_sold_vat_free():
    """
    Deliberately different from the counter, which reads a missing group as "no
    tax". A storefront price is advertised VAT-inclusive to the public: a
    product that slipped through without a group would sell at that price having
    collected nothing, leaving the shop owing 5% it never took — silently, until
    an audit.
    """
    result = await order_service.tax_breakdown(
        _db(), lines=[(None, Decimal("105.00"))], discount_amount=Decimal("0")
    )
    assert result.total == Decimal("5.00")


async def test_an_inactive_tax_is_not_charged_but_the_line_still_is():
    """
    A group whose only tax was switched off resolves to nothing identifiable,
    which is the gap case rather than an exemption.
    """
    db = _Db({STANDARD: _Group(_Tax(Decimal("0.05"), is_active=False))})
    result = await order_service.tax_breakdown(
        db, lines=[(STANDARD, Decimal("105.00"))], discount_amount=Decimal("0")
    )
    assert result.total == Decimal("5.00")


async def test_an_empty_basket_declares_nothing():
    result = await order_service.tax_breakdown(
        _db(), lines=[], discount_amount=Decimal("0")
    )
    assert result.lines == []
    assert result.total == Decimal("0")
