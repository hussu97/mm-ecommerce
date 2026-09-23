"""
The counter's pricing engine as one pure function — the one the register ports.

Local-first counter checkout (see `counter_ingest_service`) lets the iPad price,
take the tender for and print a counter sale by itself. CLAUDE.md rule 10 says
money is computed server-side; the exception this module makes is narrow and
checked:

* The server still owns every **input**. The register can only price from a
  server-published, content-addressed config bundle (`counter_bundle_service`),
  and the bundle carries the prices, tax groups, legal-entity VAT status,
  promotions and rounding step exactly as this module reads them.
* There is **one** engine. `price_check` / `price_lines` here and the Swift port
  (`mm-pos/MMPos/Features/Register/Counter/CounterPricing.swift`) are both
  asserted against the same golden vectors
  (`tests/fixtures/counter_pricing_vectors.json`, written by
  `scripts/export_counter_pricing_vectors.py`).
* Every synced sale is **re-priced** here against the bundle it cites, and a
  difference is persisted, flagged and emailed — never silent.

It is composed from the pieces the server-authoritative path already uses, so
the two cannot disagree by construction:

* `pos_pricing.calculate_order` — the arithmetic;
* `tax_tuple` — the same active-tax filter `pos_order_service._resolve_tax`
  applies (a deactivated tax is not charged; a group's rates are summed; the
  first live tax names the line);
* the unregistered-entity rule — a counter trading under a licence that is not
  VAT-registered charges no VAT (the rate goes to zero; the inclusive price, and
  so the customer's total, is unchanged);
* `promotion_rules` — mode, scope, schedule, min spend, coupon-replaces-auto,
  and which lines a category-scoped promotion covers.

`recalculate(db, order, ctx=None)` behaves exactly as it always has;
`recalculate(db, order, ctx=<PricingContext>)` takes the same inputs from a
bundle instead of the database, and `test_counter_pricing_vectors.py` asserts
the two agree with `price_check` on seeded orders.

**Engine versioning.** `ENGINE_VERSION` is the version this module implements
and every bundle declares. A change to the arithmetic ships as: an app build that
supports N+1 → every terminal at or above that build → bump this to N+1.
Ingest re-prices with the version a sale cites and supports N and N−1
(`SUPPORTED_ENGINE_VERSIONS`); today there is only 1.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.core.money import ZERO, money
from app.services.pos import pos_pricing, promotion_rules
from app.services.pos.pos_pricing import DiscountInput, LineInput

#: The version of the arithmetic below. Bumped only as described in the module
#: docstring.
ENGINE_VERSION = 1

#: Versions ingest can re-price a synced sale with: N and N−1.
SUPPORTED_ENGINE_VERSIONS = frozenset({ENGINE_VERSION})

#: The source and order type every counter check is priced as. The register no
#: longer chooses an order type, and a register only ever rings up `cashier`.
COUNTER_SOURCE = "cashier"
COUNTER_ORDER_TYPE = "pickup"

#: The fraction precision `order_discounts.value` stores (`Numeric(10, 4)`).
_FRACTION = Decimal("0.0001")


# ─── Taxes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaxRow:
    """One tax in a tax group, as the bundle carries it."""

    id: str | None
    name: str
    #: A fraction (0.05 == 5%).
    rate: Decimal
    #: `inclusive` | `exclusive`.
    type: str = "inclusive"
    is_active: bool = True


@dataclass(frozen=True)
class TaxTuple:
    """What one line is taxed at: `(rate, name, tax_id, is_inclusive)`."""

    rate: Decimal
    name: str
    tax_id: str | None
    inclusive: bool


#: A line with no tax group, or whose group has no live tax.
NO_TAX = TaxTuple(Decimal("0"), "No tax", None, True)


def tax_tuple(taxes: Iterable[TaxRow]) -> TaxTuple:
    """A tax group reduced to the tuple a line is priced with.

    The filter `pos_order_service._resolve_tax` has always applied: a
    deactivated tax is not charged (a switched-off duplicate 5% would otherwise
    bill 10%), the live rates are summed, and the first live tax names the line.
    """
    live = [t for t in taxes if t.is_active]
    if not live:
        return NO_TAX
    first = live[0]
    rate = sum((Decimal(str(t.rate)) for t in live), Decimal("0"))
    return TaxTuple(rate, first.name, first.id, first.type == "inclusive")


def effective_tax(tax: TaxTuple, vat_registered: bool) -> TaxTuple:
    """The unregistered-entity rule: no VAT under a licence that is not
    VAT-registered. The rate goes to zero and nothing else changes, so an
    inclusive price — and the customer's total — stays the same."""
    if vat_registered:
        return tax
    return TaxTuple(Decimal("0"), tax.name, tax.tax_id, tax.inclusive)


# ─── Inputs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductFacts:
    """What the engine reads off a product. From the bundle, or the DB."""

    category_id: uuid.UUID | None = None
    tax_group_id: uuid.UUID | None = None
    is_non_revenue: bool = False


@dataclass(frozen=True)
class PricingContext:
    """Every pricing input a check needs, all from one config bundle.

    `tax_groups` holds each group already reduced by `tax_tuple` (NOT yet by
    `effective_tax` — that is applied per line against `vat_registered`).
    """

    branch_id: uuid.UUID
    timezone: str
    rounding_step: Decimal
    vat_registered: bool
    tax_groups: Mapping[uuid.UUID, TaxTuple]
    products: Mapping[uuid.UUID, ProductFacts]
    promotions: tuple[promotion_rules.PromoRule, ...]
    engine_version: int = ENGINE_VERSION
    #: The legal entity the counter trades under at this branch, stamped onto
    #: the order. Not a pricing input (`vat_registered` is).
    legal_entity_id: uuid.UUID | None = None
    source: str = COUNTER_SOURCE
    order_type: str = COUNTER_ORDER_TYPE

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def tax_for(self, product_id: uuid.UUID | None) -> TaxTuple:
        facts = self.products.get(product_id) if product_id else None
        group = facts.tax_group_id if facts else None
        if group is None:
            return NO_TAX
        return self.tax_groups.get(group, NO_TAX)

    def facts_for(self, product_id: uuid.UUID | None) -> ProductFacts:
        if product_id is None:
            return ProductFacts()
        return self.products.get(product_id) or ProductFacts()


@dataclass(frozen=True)
class CheckLine:
    """A check line as the register holds it (product attributes come from
    the context)."""

    id: Any
    product_id: uuid.UUID | None
    quantity: int
    #: The catalogue (or, for an open-price product, the typed) price per unit —
    #: per kilo for a product sold by weight.
    unit_price: Decimal
    #: The line's modifier charge per unit: the sum over its options of
    #: `option price × (quantity − free units)`, as `modifier_rules.resolve` and
    #: the register's `ModifierRules.optionsPrice` compute it.
    options_price: Decimal = ZERO
    #: Kilos on the scale for a product sold by weight; None otherwise.
    weight: Decimal | None = None
    returned_quantity: int = 0
    voided: bool = False


@dataclass(frozen=True)
class PricedLine:
    """A line with every pricing attribute resolved — the golden-vector unit."""

    id: Any
    quantity: int
    unit_price: Decimal
    options_price: Decimal = ZERO
    weight: Decimal | None = None
    #: The raw tax tuple (before the unregistered-entity rule).
    tax: TaxTuple = NO_TAX
    is_non_revenue: bool = False
    category_id: uuid.UUID | None = None
    returned_quantity: int = 0
    voided: bool = False


# ─── Outputs ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LinePricing:
    id: Any
    #: `unit_price` (× weight) at 2 dp — what `order_items.base_price` stores.
    base_price: Decimal
    options_price: Decimal
    #: `base_price + options_price` — `order_items.unit_price`.
    unit_price: Decimal
    #: `unit_price × billable quantity`, before discounts.
    gross: Decimal
    #: The line's own discounts (the promotion, when it is category-scoped).
    discount: Decimal
    #: `gross − discount` — `order_items.total_price`.
    total_price: Decimal
    #: Tax on `total_price` before any order-level discount share —
    #: `order_items.tax_amount`.
    tax_amount: Decimal
    tax_exclusive_total: Decimal
    tax_exclusive_unit: Decimal


@dataclass(frozen=True)
class AppliedPromotion:
    id: uuid.UUID
    name: str
    #: `auto` | `coupon` — how it runs at this branch.
    mode: str
    #: `order` (one order-level discount) | `lines` (one per matching line).
    scope: str
    is_percentage: bool
    #: A fraction for a percentage (0.15), AED for a fixed amount.
    value: Decimal
    #: What it took off the check, in total.
    amount: Decimal
    #: The lines it discounted (scope `lines`), in input order.
    line_ids: tuple = ()


@dataclass(frozen=True)
class CheckPricing:
    lines: list[LinePricing]
    promotion: AppliedPromotion | None
    subtotal: Decimal
    line_discounts: Decimal
    order_discount: Decimal
    discount_total: Decimal
    charges: Decimal
    tax_total: Decimal
    total_excl_tax: Decimal
    rounding: Decimal
    total: Decimal
    vat_rate: Decimal
    taxes: list[pos_pricing.TaxLine] = field(default_factory=list)


# ─── The engine ───────────────────────────────────────────────────────────────


def line_base_price(unit_price: Decimal, weight: Decimal | None) -> Decimal:
    """The line's base price: per-kilo price × weight for a weighed product,
    otherwise the unit price — at 2 dp, as `add_item` has always stored it."""
    if weight is not None:
        return money(Decimal(str(unit_price)) * Decimal(str(weight)))
    return money(unit_price)


def _billable_quantity(quantity: int, returned: int) -> int:
    return max(quantity - (returned or 0), 0)


def order_vat_rate(totals: pos_pricing.OrderTotals) -> Decimal:
    """`orders.vat_rate` as `recalculate` derives it: zero with no tax, the one
    bucket's exact rate, or the blended effective rate for a mixed basket.

    The blend is quantized to 4 dp **half-even** — the Decimal context default
    `recalculate` has always used here, spelled out so a port does not assume
    the half-up every money figure uses. It is an informational column, never
    summed into a total.
    """
    if not totals.taxes:
        return Decimal("0")
    if len(totals.taxes) == 1:
        return totals.taxes[0].rate
    base = totals.total_excl_tax
    if base <= 0:
        return Decimal("0")
    return (totals.tax_total / base).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_EVEN
    )


def promotion_discount(
    rule: promotion_rules.PromoRule,
) -> tuple[bool, Decimal]:
    """`(is_percentage, value)` the chosen promotion is applied with — the
    fraction held to the precision `order_discounts.value` stores, so the value
    priced in memory and the value read back from the row are the same."""
    is_percentage, value = promotion_rules.discount_value(rule)
    return is_percentage, Decimal(str(value))


def price_lines(
    lines: Sequence[PricedLine],
    *,
    promotions: Iterable[promotion_rules.PromoRule],
    local_dt: datetime,
    coupon_id: uuid.UUID | None,
    branch_id: uuid.UUID | None,
    rounding_step: Decimal,
    vat_registered: bool,
    source: str = COUNTER_SOURCE,
    order_type: str = COUNTER_ORDER_TYPE,
) -> CheckPricing:
    """Price a check whose lines carry every attribute. See `price_check`.

    `local_dt` is the shop's wall clock the promotion schedules are read at.
    Voided lines are dropped (they are not billed and not returned); a line
    whose units were all returned stays in, billing nothing.
    """
    promotions = list(promotions)
    live = [line for line in lines if not line.voided]
    base_prices = [line_base_price(line.unit_price, line.weight) for line in live]
    options_prices = [money(line.options_price) for line in live]

    # The spend a `spend` trigger is measured against: the pre-discount value of
    # the billable lines (`auto_promotion_service._spend_basis`).
    spend = Decimal("0")
    billable_facts: list[promotion_rules.LineFacts] = []
    for line, base, options in zip(live, base_prices, options_prices):
        billable = _billable_quantity(line.quantity, line.returned_quantity)
        if billable <= 0:
            continue
        spend += (base + options) * billable
        billable_facts.append(
            promotion_rules.LineFacts(id=line.id, category_id=line.category_id)
        )

    facts = promotion_rules.OrderFacts(
        source=source, branch_id=branch_id, order_type=order_type, spend=spend
    )
    chosen = promotion_rules.choose(promotions, facts, local_dt, coupon_id=coupon_id)

    targets: set | None = None
    promo_input: DiscountInput | None = None
    is_percentage = False
    value = Decimal("0")
    if chosen is not None:
        is_percentage, value = promotion_discount(chosen)
        promo_input = DiscountInput(
            name=chosen.name,
            source="promotion",
            is_percentage=is_percentage,
            value=value,
        )
        targets = (
            promotion_rules.per_line_targets(chosen, billable_facts) or set()
            if chosen.category_ids
            else None
        )
        if targets is not None and not targets:
            # A category-scoped promotion with no matching line writes no
            # discount row at all (`auto_promotion_service._apply_per_category`),
            # so the check has no promotion — not one worth 0.00.
            chosen = None
            promo_input = None
            targets = None

    inputs: list[LineInput] = []
    for line, base, options in zip(live, base_prices, options_prices):
        tax = effective_tax(line.tax, vat_registered)
        discounts = (
            [promo_input]
            if promo_input is not None and targets is not None and line.id in targets
            else []
        )
        inputs.append(
            LineInput(
                quantity=line.quantity,
                unit_price=base,
                options_price=options,
                tax_rate=tax.rate,
                tax_is_inclusive=tax.inclusive,
                tax_id=tax.tax_id,
                tax_name=tax.name,
                discounts=discounts,
                returned_quantity=line.returned_quantity or 0,
                is_non_revenue=line.is_non_revenue,
            )
        )

    totals = pos_pricing.calculate_order(
        inputs,
        order_discount=promo_input if (promo_input and targets is None) else None,
        cash_rounding_step=Decimal(str(rounding_step or 0)),
    )

    priced_lines = [
        LinePricing(
            id=line.id,
            base_price=base,
            options_price=options,
            unit_price=money(base + options),
            gross=lt.gross,
            discount=lt.discount,
            total_price=lt.net_of_discount,
            tax_amount=lt.tax,
            tax_exclusive_total=lt.tax_exclusive,
            tax_exclusive_unit=lt.unit_tax_exclusive,
        )
        for line, base, options, lt in zip(
            live, base_prices, options_prices, totals.lines
        )
    ]

    applied: AppliedPromotion | None = None
    if chosen is not None:
        if targets is None:
            amount = totals.order_discount
            line_ids: tuple = ()
        else:
            amount = money(
                sum(
                    (p.discount for p in priced_lines if p.id in targets),
                    Decimal("0"),
                )
            )
            line_ids = tuple(p.id for p in priced_lines if p.id in targets)
        applied = AppliedPromotion(
            id=chosen.id,
            name=chosen.name,
            mode=promotion_rules.mode_at(chosen, branch_id) or "auto",
            scope="order" if targets is None else "lines",
            is_percentage=is_percentage,
            value=value,
            amount=amount,
            line_ids=line_ids,
        )

    return CheckPricing(
        lines=priced_lines,
        promotion=applied,
        subtotal=totals.subtotal,
        line_discounts=totals.line_discounts,
        order_discount=totals.order_discount,
        discount_total=totals.discount_total,
        charges=totals.charges,
        tax_total=totals.tax_total,
        total_excl_tax=totals.total_excl_tax,
        rounding=totals.rounding,
        total=totals.total,
        vat_rate=order_vat_rate(totals),
        taxes=list(totals.taxes),
    )


def resolve_line(ctx: PricingContext, line: CheckLine) -> PricedLine:
    """A register line with its product's tax, category and revenue flag
    filled in from the bundle. An unknown product prices like
    `recalculate` prices a line whose product row is gone: no tax, no
    category, revenue."""
    facts = ctx.facts_for(line.product_id)
    return PricedLine(
        id=line.id,
        quantity=line.quantity,
        unit_price=line.unit_price,
        options_price=line.options_price,
        weight=line.weight,
        tax=ctx.tax_for(line.product_id),
        is_non_revenue=facts.is_non_revenue,
        category_id=facts.category_id,
        returned_quantity=line.returned_quantity,
        voided=line.voided,
    )


def price_check(
    ctx: PricingContext,
    lines: Sequence[CheckLine],
    at: datetime,
    coupon_id: uuid.UUID | None = None,
) -> CheckPricing:
    """Price a counter check against a config bundle, as of the instant `at`.

    The single function the register's engine is a port of. `at` is an aware
    instant; the promotion schedules read it on the bundle's time zone.
    """
    return price_lines(
        [resolve_line(ctx, line) for line in lines],
        promotions=ctx.promotions,
        local_dt=at.astimezone(ctx.zone),
        coupon_id=coupon_id,
        branch_id=ctx.branch_id,
        rounding_step=ctx.rounding_step,
        vat_registered=ctx.vat_registered,
        source=ctx.source,
        order_type=ctx.order_type,
    )


# ─── Wire forms (bundle + golden vectors) ─────────────────────────────────────


def fmt_money(value: Decimal) -> str:
    """Money on the wire: always two decimals, as a string."""
    return f"{money(value):.2f}"


def fmt_rate(value: Decimal) -> str:
    """A rate or fraction on the wire: four decimals, as a string."""
    return f"{Decimal(str(value)).quantize(_FRACTION):.4f}"


def promo_rule_from_wire(
    data: Mapping[str, Any], branch_id: uuid.UUID
) -> promotion_rules.PromoRule:
    """A bundle/vector promotion (see `CounterBundlePromotion`) as a
    `PromoRule`. `mode` becomes the branch's membership of the auto/coupon
    list and `rank` the priority (the bundle is already sorted best-first)."""
    mode = data.get("mode", "auto")
    weekdays = tuple(bool(w) for w in data.get("weekdays", [True] * 7))
    if len(weekdays) != 7:
        raise ValueError("weekdays must list Monday..Sunday")

    def _date(v):
        return date.fromisoformat(v) if v else None

    return promotion_rules.PromoRule(
        id=uuid.UUID(str(data["id"])),
        name=str(data["name"]),
        reward=str(data["reward"]),
        reward_value=Decimal(str(data["reward_value"])),
        trigger=str(data.get("trigger", "spend")),
        trigger_value=Decimal(str(data.get("trigger_value", "0"))),
        category_ids=frozenset(uuid.UUID(str(c)) for c in data.get("category_ids", [])),
        branch_ids=frozenset(uuid.UUID(str(b)) for b in data.get("branch_ids", [])),
        order_types=frozenset(data.get("order_types", [])),
        sources=frozenset(data.get("sources", [])),
        auto_branch_ids=frozenset({branch_id}) if mode == "auto" else frozenset(),
        coupon_branch_ids=frozenset({branch_id}) if mode == "coupon" else frozenset(),
        priority=int(data.get("rank", 0)),
        created_ts=0.0,
        is_active=bool(data.get("is_active", True)),
        is_deleted=False,
        from_date=_date(data.get("from_date")),
        to_date=_date(data.get("to_date")),
        from_time=int(data.get("from_time", 0)),
        to_time=int(data.get("to_time", 1439)),
        weekdays=weekdays,  # type: ignore[arg-type]
    )


def tax_from_wire(data: Mapping[str, Any] | None) -> TaxTuple:
    if not data:
        return NO_TAX
    return TaxTuple(
        rate=Decimal(str(data.get("rate", "0"))),
        name=str(data.get("name", "No tax")),
        tax_id=data.get("tax_id"),
        inclusive=bool(data.get("inclusive", True)),
    )


def _opt_decimal(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def priced_line_from_wire(data: Mapping[str, Any]) -> PricedLine:
    category = data.get("category_id")
    return PricedLine(
        id=str(data["id"]),
        quantity=int(data["quantity"]),
        unit_price=Decimal(str(data["unit_price"])),
        options_price=Decimal(str(data.get("options_price", "0"))),
        weight=_opt_decimal(data.get("weight")),
        tax=tax_from_wire(data.get("tax")),
        is_non_revenue=bool(data.get("is_non_revenue", False)),
        category_id=uuid.UUID(str(category)) if category else None,
        returned_quantity=int(data.get("returned_quantity", 0)),
        voided=bool(data.get("voided", False)),
    )


def pricing_to_wire(pricing: CheckPricing) -> dict[str, Any]:
    """`CheckPricing` as the golden vectors (and the pricing audit) spell it:
    money as 2-dp strings, rates/fractions as 4-dp strings."""
    promo = pricing.promotion
    return {
        "promotion": (
            None
            if promo is None
            else {
                "id": str(promo.id),
                "name": promo.name,
                "mode": promo.mode,
                "scope": promo.scope,
                "is_percentage": promo.is_percentage,
                "value": fmt_rate(promo.value)
                if promo.is_percentage
                else fmt_money(promo.value),
                "amount": fmt_money(promo.amount),
                "line_ids": [str(i) for i in promo.line_ids],
            }
        ),
        "lines": [
            {
                "id": str(line.id),
                "base_price": fmt_money(line.base_price),
                "options_price": fmt_money(line.options_price),
                "unit_price": fmt_money(line.unit_price),
                "gross": fmt_money(line.gross),
                "discount": fmt_money(line.discount),
                "total_price": fmt_money(line.total_price),
                "tax_amount": fmt_money(line.tax_amount),
                "tax_exclusive_total": fmt_money(line.tax_exclusive_total),
                "tax_exclusive_unit": fmt_money(line.tax_exclusive_unit),
            }
            for line in pricing.lines
        ],
        "subtotal": fmt_money(pricing.subtotal),
        "line_discounts": fmt_money(pricing.line_discounts),
        "order_discount": fmt_money(pricing.order_discount),
        "discount_total": fmt_money(pricing.discount_total),
        "charges": fmt_money(pricing.charges),
        "tax_total": fmt_money(pricing.tax_total),
        "total_excl_tax": fmt_money(pricing.total_excl_tax),
        "rounding": fmt_money(pricing.rounding),
        "total": fmt_money(pricing.total),
        "vat_rate": fmt_rate(pricing.vat_rate),
        "taxes": [
            {
                "tax_id": t.tax_id,
                "name": t.name,
                "rate": fmt_rate(t.rate),
                "taxable_amount": fmt_money(t.taxable_amount),
                "amount": fmt_money(t.amount),
            }
            for t in pricing.taxes
        ],
    }


__all__ = [
    "AppliedPromotion",
    "CheckLine",
    "CheckPricing",
    "COUNTER_ORDER_TYPE",
    "COUNTER_SOURCE",
    "ENGINE_VERSION",
    "LinePricing",
    "NO_TAX",
    "PricedLine",
    "PricingContext",
    "ProductFacts",
    "SUPPORTED_ENGINE_VERSIONS",
    "TaxRow",
    "TaxTuple",
    "effective_tax",
    "fmt_money",
    "fmt_rate",
    "line_base_price",
    "order_vat_rate",
    "price_check",
    "price_lines",
    "pricing_to_wire",
    "promo_rule_from_wire",
    "promotion_discount",
    "resolve_line",
    "tax_tuple",
]
