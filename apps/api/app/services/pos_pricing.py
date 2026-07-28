"""
Order arithmetic for the POS.

Pure functions with no database or ORM dependency, so the money maths can be
tested exhaustively and reasoned about in one place.

Two tax conventions are supported:

* **inclusive** (the UAE default) — the menu price already contains the tax.
  A 105.00 item at 5% VAT is 100.00 net + 5.00 tax.
* **exclusive** — the tax is added on top at checkout.

Rounding is applied once per line and once per total using banker's-rounding-free
`ROUND_HALF_UP`, which is what a customer expects to see on a receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

__all__ = [
    "CENT",
    "ChargeInput",
    "DiscountInput",
    "LineInput",
    "OrderTotals",
    "TaxLine",
    "apply_cash_rounding",
    "calculate_order",
    "money",
    "split_inclusive_tax",
]

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | int | float | str | None) -> Decimal:
    """Quantise to two decimal places, rounding half away from zero."""
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def split_inclusive_tax(gross: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """
    Split a tax-inclusive amount into (net, tax).

    `rate` is a fraction (0.05 == 5%). A zero rate returns the gross untouched
    rather than dividing by one, which keeps zero-rated items exact.
    """
    gross = money(gross)
    if rate <= 0:
        return gross, ZERO
    net = money(gross / (Decimal("1") + rate))
    return net, money(gross - net)


@dataclass(slots=True)
class DiscountInput:
    """A discount to apply. Percentages are fractions (0.10 == 10%)."""

    name: str
    source: str = "open"
    is_percentage: bool = False
    value: Decimal = ZERO
    #: Optional cap for percentage discounts.
    maximum_amount: Decimal | None = None
    reference_id: str | None = None

    def amount_on(self, base: Decimal) -> Decimal:
        base = money(base)
        if base <= 0:
            return ZERO
        raw = base * self.value if self.is_percentage else money(self.value)
        if self.is_percentage and self.maximum_amount is not None:
            raw = min(raw, money(self.maximum_amount))
        # Never discount more than the thing being discounted.
        return money(min(money(raw), base))


@dataclass(slots=True)
class LineInput:
    """One order line, priced tax-inclusively unless `tax_is_inclusive` is False."""

    quantity: int
    unit_price: Decimal
    #: Sum of the selected modifier option prices, per unit.
    options_price: Decimal = ZERO
    tax_rate: Decimal = ZERO
    tax_is_inclusive: bool = True
    tax_id: str | None = None
    tax_name: str = "VAT"
    discounts: list[DiscountInput] = field(default_factory=list)
    #: Quantity returned after the fact; those units stop counting toward the bill.
    returned_quantity: int = 0
    is_non_revenue: bool = False

    @property
    def billable_quantity(self) -> int:
        return max(self.quantity - self.returned_quantity, 0)

    @property
    def gross_unit_price(self) -> Decimal:
        return money(self.unit_price) + money(self.options_price)


@dataclass(slots=True)
class ChargeInput:
    """A service/delivery charge. Percentage charges are a fraction of the net sale."""

    name: str
    type: str  # percentage | fixed | open
    value: Decimal = ZERO
    tax_rate: Decimal = ZERO
    tax_is_inclusive: bool = True
    tax_id: str | None = None
    tax_name: str = "VAT"
    charge_id: str | None = None


@dataclass(slots=True)
class TaxLine:
    tax_id: str | None
    name: str
    rate: Decimal
    taxable_amount: Decimal
    amount: Decimal


@dataclass(slots=True)
class LineTotals:
    gross: Decimal
    discount: Decimal
    net_of_discount: Decimal
    tax: Decimal
    tax_exclusive: Decimal
    unit_tax_exclusive: Decimal


@dataclass(slots=True)
class OrderTotals:
    subtotal: Decimal
    """Line total before discounts, tax-inclusive."""
    line_discounts: Decimal
    order_discount: Decimal
    discount_total: Decimal
    charges: Decimal
    tax_total: Decimal
    total_excl_tax: Decimal
    rounding: Decimal
    total: Decimal
    lines: list[LineTotals]
    taxes: list[TaxLine]
    charge_amounts: list[Decimal]


def _line_totals(line: LineInput) -> LineTotals:
    gross = money(line.gross_unit_price * line.billable_quantity)

    discount = ZERO
    remaining = gross
    for d in line.discounts:
        applied = d.amount_on(remaining)
        discount += applied
        remaining -= applied
    discount = money(discount)
    net_of_discount = money(gross - discount)

    if line.is_non_revenue:
        # Staff meals and comps carry no tax and no revenue.
        return LineTotals(gross, discount, net_of_discount, ZERO, net_of_discount, ZERO)

    if line.tax_is_inclusive:
        tax_exclusive, tax = split_inclusive_tax(net_of_discount, line.tax_rate)
    else:
        tax_exclusive = net_of_discount
        tax = money(net_of_discount * line.tax_rate)

    unit_excl = (
        money(tax_exclusive / line.billable_quantity)
        if line.billable_quantity
        else ZERO
    )
    return LineTotals(gross, discount, net_of_discount, tax, tax_exclusive, unit_excl)


def _accumulate_tax(
    buckets: dict[tuple[str | None, str, Decimal], TaxLine],
    tax_id: str | None,
    name: str,
    rate: Decimal,
    taxable: Decimal,
    amount: Decimal,
) -> None:
    if rate <= 0 and amount == 0:
        return
    key = (tax_id, name, rate)
    bucket = buckets.get(key)
    if bucket is None:
        buckets[key] = TaxLine(tax_id, name, rate, money(taxable), money(amount))
    else:
        bucket.taxable_amount = money(bucket.taxable_amount + taxable)
        bucket.amount = money(bucket.amount + amount)


def apply_cash_rounding(total: Decimal, step: Decimal) -> Decimal:
    """
    Round a total to the nearest `step` (e.g. 0.25 AED because 1-fil coins are
    not in circulation). A step of zero disables rounding.
    """
    total = money(total)
    if step is None or step <= 0:
        return total
    step = Decimal(str(step))
    units = (total / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return money(units * step)


def calculate_order(
    lines: list[LineInput],
    *,
    charges: list[ChargeInput] | None = None,
    order_discount: DiscountInput | None = None,
    cash_rounding_step: Decimal = ZERO,
) -> OrderTotals:
    """
    Price a whole order.

    Order of operations matters and follows normal POS convention:
    line discounts → order discount (spread across lines pro rata so each line's
    tax stays correct) → charges → tax → cash rounding.
    """
    charges = charges or []

    line_results = [_line_totals(line) for line in lines]
    subtotal = money(sum((lr.gross for lr in line_results), start=ZERO))
    line_discounts = money(sum((lr.discount for lr in line_results), start=ZERO))
    net_after_line_discounts = money(
        sum((lr.net_of_discount for lr in line_results), start=ZERO)
    )

    # An order-level discount is spread proportionally so that per-tax-rate totals
    # stay correct on a mixed-rate check. Without this, a 10% off order containing
    # a zero-rated and a 5% item would misreport VAT.
    order_discount_amount = ZERO
    if order_discount is not None and net_after_line_discounts > 0:
        order_discount_amount = order_discount.amount_on(net_after_line_discounts)

    taxable_lines = [
        lr for lr, line in zip(line_results, lines) if not line.is_non_revenue
    ]
    taxable_base = money(sum((lr.net_of_discount for lr in taxable_lines), start=ZERO))

    tax_buckets: dict[tuple[str | None, str, Decimal], TaxLine] = {}
    total_excl_tax = ZERO
    tax_total = ZERO
    allocated = ZERO

    for index, (line, lr) in enumerate(zip(lines, line_results)):
        share = ZERO
        if order_discount_amount > 0 and taxable_base > 0 and not line.is_non_revenue:
            if index == len(lines) - 1 or lr is taxable_lines[-1]:
                # Give the final taxable line the remainder so the parts sum exactly.
                share = money(order_discount_amount - allocated)
            else:
                share = money(order_discount_amount * lr.net_of_discount / taxable_base)
            allocated = money(allocated + share)

        net = money(lr.net_of_discount - share)
        if line.is_non_revenue:
            total_excl_tax = money(total_excl_tax + net)
            continue

        if line.tax_is_inclusive:
            excl, tax = split_inclusive_tax(net, line.tax_rate)
        else:
            excl, tax = net, money(net * line.tax_rate)

        total_excl_tax = money(total_excl_tax + excl)
        tax_total = money(tax_total + tax)
        _accumulate_tax(
            tax_buckets, line.tax_id, line.tax_name, line.tax_rate, excl, tax
        )

    # Charges are computed on the discounted, tax-exclusive sale value.
    # The base is snapshotted before the loop so that two percentage charges
    # are each taken on the sale, not on the sale plus the previous charge.
    charge_base = total_excl_tax
    charge_amounts: list[Decimal] = []
    charges_total = ZERO
    for charge in charges:
        if charge.type == "percentage":
            gross_charge = money(charge_base * charge.value)
        else:
            gross_charge = money(charge.value)
        charge_amounts.append(gross_charge)
        charges_total = money(charges_total + gross_charge)

        if charge.tax_is_inclusive:
            excl, tax = split_inclusive_tax(gross_charge, charge.tax_rate)
        else:
            excl, tax = gross_charge, money(gross_charge * charge.tax_rate)
        total_excl_tax = money(total_excl_tax + excl)
        tax_total = money(tax_total + tax)
        _accumulate_tax(
            tax_buckets, charge.tax_id, charge.tax_name, charge.tax_rate, excl, tax
        )

    raw_total = money(total_excl_tax + tax_total)
    rounded_total = apply_cash_rounding(raw_total, cash_rounding_step)
    rounding = money(rounded_total - raw_total)

    return OrderTotals(
        subtotal=subtotal,
        line_discounts=line_discounts,
        order_discount=order_discount_amount,
        discount_total=money(line_discounts + order_discount_amount),
        charges=charges_total,
        tax_total=tax_total,
        total_excl_tax=total_excl_tax,
        rounding=rounding,
        total=rounded_total,
        lines=line_results,
        taxes=sorted(tax_buckets.values(), key=lambda t: (t.name, t.rate)),
        charge_amounts=charge_amounts,
    )
