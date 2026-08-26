"""
What a storefront order costs. One function, one answer.

There used to be two engines writing the same `orders.subtotal / total /
vat_amount / total_excl_vat` columns — `pos_pricing.calculate_order` for the
counter, tax-group driven and inclusive/exclusive aware, and a second set of
arithmetic inlined in `order_service` for the website, which back-calculated VAT
from a module constant. On top of those, the checkout screen re-derived the
grand total twice and printed a VAT line from a third formula that ignored the
fees, so the "VAT included" figure was not 5/105 of the total printed directly
above it.

This module is the website's half, extracted whole so that **every** figure an
order is written with comes out of one call. The register keeps
`pos_pricing.calculate_order`; the two are not merged because a counter check
and a web basket genuinely price differently — a counter has line discounts,
comps, open charges, cash rounding and per-line returns, and a web basket has
none of those but has a zone-priced delivery fee, a small-basket surcharge and a
single order-level coupon. What they *do* share is the arithmetic that matters,
and they share it by construction: the tax split here is
`pos_pricing.split_inclusive_tax` and the rounding is `pos_pricing.money`, so a
website receipt and a counter receipt round a dirham the same way.

The invariant this exists to protect: **`POST /orders/preview` and
`POST /orders` must never disagree.** Both call `compute_order_totals`; there is
no second implementation for them to drift apart into.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import trial_customer
from app.services.delivery import delivery_service
from app.services.delivery.delivery_zone_service import Zone
from app.services.pos import pos_order_service, pos_pricing

logger = logging.getLogger(__name__)

__all__ = [
    "VAT_RATE",
    "OrderTotals",
    "TaxBreakdown",
    "compute_order_totals",
    "low_order_fee_for",
    "tax_breakdown",
]

ZERO = Decimal("0.00")

#: UAE standard rate, as a fraction.
#:
#: **Only a fallback now.** Every line's rate is read from its product's tax
#: group (see `tax_breakdown`), which is what makes a zero-rated product
#: possible and a rate change an admin edit rather than a deploy. This constant
#: is what a line with *no resolvable tax at all* is charged, because a
#: storefront price is advertised VAT-inclusive and a product that slipped
#: through without a group would otherwise sell VAT-free at that price — leaving
#: the shop owing 5% it never collected. It is also the rate stamped on an order
#: whose basket produced no tax rows to average.
#:
#: What would change it: the UAE changing its standard rate. Every product-level
#: variation belongs in `tax_groups`, not here.
VAT_RATE = Decimal("0.05")


# ── The small-basket surcharge ────────────────────────────────────────────────


def low_order_fee_for(
    subtotal: Decimal,
    delivery_method: DeliveryMethodEnum,
    settings: DeliverySettings,
) -> Decimal:
    """
    The small-basket surcharge, if this order attracts one.

    Delivery only. A pickup order costs us nothing to hand over, so charging for
    a small one would be a fee with no cost behind it.

    **Judged on the basket before any discount.** Free delivery is judged on the
    discounted figure, and the asymmetry is deliberate rather than an oversight:
    free delivery is a reward, and rewarding someone for what they actually paid
    is right; this is a surcharge, and a surcharge that appears *because* the
    customer applied a coupon is indefensible. On the gross figure a 40-dirham
    basket with a 15% new-customer code stays a 40-dirham basket and attracts
    nothing. On the discounted figure it would fall to 34, trip the threshold,
    and hand back a 15-dirham fee against a 6-dirham discount — the acquisition
    offer fighting itself.

    The threshold is inclusive: a basket of exactly the threshold still pays.

    This used to be mirrored in the checkout as `lowOrderFeeFor`, whose own
    comment said so. That copy is gone — the screen renders what
    `/orders/preview` returns — and the one behaviour it had that this did not
    came with it: an empty basket is not a small order, it is a basket that
    cannot be ordered, and quoting a surcharge on one puts a fee on a page that
    is about to redirect. Unreachable from `create_order`, which refuses an
    empty cart before it gets here, and reached by every preview fired while the
    basket is still loading.
    """
    if delivery_method == DeliveryMethodEnum.PICKUP:
        return ZERO
    if subtotal <= 0:
        return ZERO
    threshold = settings.low_order_threshold
    if threshold is None or subtotal > threshold:
        return ZERO
    return settings.low_order_fee or ZERO


# ── VAT, from each product's own tax group ────────────────────────────────────


@dataclass(frozen=True)
class TaxBreakdown:
    """The VAT on an order, per rate, the way an invoice has to show it."""

    #: One entry per distinct tax group in the basket, ready for `order_taxes`.
    lines: list[pos_pricing.TaxLine]
    #: Their sum, for `orders.vat_amount`.
    total: Decimal
    #: The rate to stamp on `orders.vat_rate`. The basket's single rate where
    #: there is one; otherwise the blended effective rate, because that column
    #: is one number and a mixed basket does not have one.
    rate: Decimal


async def tax_breakdown(
    db: AsyncSession,
    *,
    lines: list[tuple[uuid.UUID | None, Decimal]],
    discount_amount: Decimal,
) -> TaxBreakdown:
    """
    Split an order's goods into tax rows, using each product's own tax group.

    **The website used to apply a flat 5% and write no rows at all.** It read a
    module constant, so a zero-rated product would have been charged VAT, a
    change of rate meant a deploy, and `order_taxes` was empty for every
    storefront order — which is why a website receipt printed no VAT breakdown
    while a counter receipt printed one from the same table. Tax groups were
    never counter-specific; only the code that read them was.

    The arithmetic is unchanged for today's catalogue, and deliberately so: every
    active product sits in one 5% inclusive group, so this computes exactly what
    the constant did. What changes is that it will keep being right when one of
    them does not.

    `lines` is `(tax_group_id, gross)` per order line — gross being what the
    customer pays for that line, tax included, before any order-level discount.

    **The discount is spread pro rata**, which only matters once rates differ:
    knocking AED 20 off a basket of one standard and one zero-rated item has to
    reduce the taxable base of the standard line by its share and no more, or
    the shop pays VAT on money it never received.
    """
    gross_total = sum((gross for _, gross in lines), Decimal("0"))
    if gross_total <= 0:
        return TaxBreakdown(lines=[], total=Decimal("0"), rate=VAT_RATE)

    # Group first, then discount, so a basket with four lines on one rate
    # produces one invoice row rather than four.
    by_group: dict[uuid.UUID | None, Decimal] = {}
    for tax_group_id, gross in lines:
        by_group[tax_group_id] = by_group.get(tax_group_id, Decimal("0")) + gross

    rows: list[pos_pricing.TaxLine] = []
    total = Decimal("0")
    for tax_group_id, gross in by_group.items():
        rate, name, tax_id, inclusive = await pos_order_service._resolve_tax(
            db, tax_group_id
        )
        # **A product with no tax group falls back to the standard rate, and
        # that is a deliberate difference from the counter**, which reads a
        # missing group as "No tax".
        #
        # The two channels are not in the same position. A counter product with
        # no group is rung up by somebody who can see the total; a storefront
        # price is advertised VAT-inclusive to the public, and a product that
        # slipped through without a group would quietly sell VAT-free at the
        # advertised price — leaving the shop owing the 5% it never collected,
        # silently, until an audit. A missing group is a configuration gap, not
        # an exemption. Genuine zero-rating is a group with a zero-rate tax in
        # it, which this honours.
        #
        # Every one of the 130 active products is in a group today, so this is a
        # guard rather than a path.
        # Keyed on `tax_id` rather than on the group being null, because that is
        # what separates the two cases `_resolve_tax` reports identically: a
        # product with nothing configured, and a group deliberately holding a
        # zero-rate tax. The second has an identifiable tax behind it and is
        # honoured; the first is the gap.
        if tax_id is None:
            logger.warning(
                "Storefront line with no resolvable tax (group=%s); charging the "
                "standard rate rather than selling it VAT-free.",
                tax_group_id,
            )
            rate, name, tax_id, inclusive = VAT_RATE, "VAT", None, True
        share = gross / gross_total
        taxable = pos_pricing.money(gross - discount_amount * share)
        if rate <= 0 or taxable <= 0:
            continue
        # Inclusive is the UAE default and what every group here is set to. An
        # exclusive group would mean the rate is added on top, which the
        # storefront's prices are not written for — so it is refused loudly
        # rather than silently under-charged.
        if not inclusive:
            logger.error(
                "Tax group %s is exclusive; the storefront prices inclusively. "
                "Treating it as inclusive rather than under-charging.",
                tax_group_id,
            )
        net, tax = pos_pricing.split_inclusive_tax(taxable, rate)
        rows.append(
            pos_pricing.TaxLine(
                tax_id=tax_id,
                name=name,
                rate=rate,
                taxable_amount=net,
                amount=tax,
            )
        )
        total += tax

    taxable_total = pos_pricing.money(gross_total - discount_amount)
    effective = (total / (taxable_total - total)) if taxable_total > total else VAT_RATE
    return TaxBreakdown(
        lines=rows,
        total=pos_pricing.money(total),
        # One row's rate where the basket has one; the blended rate otherwise.
        rate=rows[0].rate if len(rows) == 1 else pos_pricing.money(effective),
    )


# ── The totals themselves ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class OrderTotals:
    """
    Every figure the order row needs, named.

    This used to be a five-tuple unpacked positionally at its one call site,
    which was survivable while it held one fee. It now holds two, both `Decimal`,
    both money, and adjacent — and a tuple whose second and third elements can be
    swapped without a type error is how a small-basket fee ends up charged to the
    customer as delivery, reconciled against the courier's bill, and quietly
    wrecking the freight margin report. Naming them costs nothing and removes the
    whole class of mistake.

    It now carries the basket and the tax rows too, so `_persist_order` writes
    what was quoted rather than recomputing part of it. Before that, the VAT was
    worked out twice on the way to one order — once here from a flat rate, once
    at the insert from the tax groups — and the two figures were only equal
    because today's catalogue has one rate in it.
    """

    subtotal: Decimal
    discount_amount: Decimal
    delivery_fee: Decimal
    #: The small-basket surcharge, zero on everything above the threshold and on
    #: every pickup order.
    low_order_fee: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    total_excl_vat: Decimal
    total: Decimal
    #: The invoice's tax rows, one per distinct rate.
    taxes: list[pos_pricing.TaxLine]
    #: Comes back because it also decides who carries the order, and resolving it
    #: twice risks the two answers disagreeing if the map is published in between.
    zone: Zone | None
    #: Everything the pin's price was decided by — the zone's threshold, whether
    #: free delivery reaches here, the courier's own estimate. Carried so a
    #: preview can answer "how much and when" from the *same* pricing call the
    #: totals came out of, rather than asking the courier a second time.
    delivery: delivery_service.DeliveryPrice | None
    #: False when there is no fee to name yet — nowhere we can deliver to, or no
    #: pin dropped. `total` then excludes delivery, which is exactly what the
    #: checkout has always shown while the address is still being typed. An
    #: order is never written in this state; see `compute_order_totals`.
    delivery_fee_known: bool
    serviceable: bool


async def compute_order_totals(
    db: AsyncSession,
    *,
    lines: list[tuple[uuid.UUID | None, Decimal]],
    delivery_method: DeliveryMethodEnum,
    discount_amount: Decimal = ZERO,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
    address: str | None = None,
    settings: DeliverySettings | None = None,
    priced: delivery_service.DeliveryPrice | None = None,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
    strict: bool = True,
) -> OrderTotals:
    """
    Price a whole storefront order: goods, discount, both fees, VAT.

    `lines` is `(tax_group_id, gross)` per basket line, and the subtotal is
    summed from it rather than passed alongside — a caller that could hand over a
    subtotal disagreeing with its own lines is a caller that can quote one figure
    and charge another.

    `priced` lets a caller that has *already* asked what delivery costs to this
    pin hand the answer in instead of having it asked again. That is not an
    optimisation: `delivery_service.price` reaches a courier's live pricing API
    in the dynamic zones, and a preview that called it twice would quote against
    one estimate and bill against another.

    `strict` is the difference between quoting and charging:

    * **strict (order creation)** — an address nothing can be delivered to, or a
      delivery order with no pin at all, raises `UnserviceableAreaError`. There
      is no honest fee for either, and inventing one sells a delivery nobody is
      going to make. (The no-pin branch used to read a `default_delivery_fee`
      column that was dropped from `delivery_settings` when the polygon map took
      over per-zone pricing — so it would have raised `AttributeError` rather
      than refusing. Unreachable either way: `AddressCreate` requires the pin.)
    * **not strict (preview)** — both are reported instead. The fee is unknown,
      `total` excludes it, and the checkout renders "—" or "fee from your
      address" exactly as it always has. A preview must never refuse: the
      customer is still filling the form in.

    `user_id` and `email` are here for one reason: a pilot account pays no
    delivery fee anywhere it can be delivered to. The waiver is applied here as
    well as in `delivery_service.quote`, because these are the two places the
    number a customer sees is produced, and a waiver in only one of them is a
    checkout that shows free delivery and an order that charges for it.
    `test_delivery_fee_agreement` is what holds the two together.

    The identity is passed in rather than looked up, so the two callers that
    know it — the preview and the order being written — are the only two that
    can grant it, and both grant it identically.

    Everything else is identical in both modes, which is the point — see the
    module docstring.
    """
    subtotal = sum((gross for _, gross in lines), Decimal("0"))
    discounted_subtotal = subtotal - discount_amount

    if settings is None:
        settings = await delivery_service.get_settings(db)
    low_order_fee = low_order_fee_for(subtotal, delivery_method, settings)

    zone: Zone | None = None
    serviceable = True
    delivery_fee_known = True

    if delivery_method == DeliveryMethodEnum.PICKUP:
        # No pin, no polygon, no courier. The counter charges what the counter
        # charges, which is the one delivery number that is not a zone's.
        delivery_fee = settings.pickup_fee
        priced = None
    else:
        if priced is None:
            # The fee is priced off the pin, and only the pin. One call, so the
            # zone the order is filed against is the same zone its price came
            # from. Priced against the *discounted* subtotal because free
            # delivery is a reward for what the customer actually paid.
            priced = await delivery_service.price(
                db,
                discounted_subtotal,
                latitude=latitude,
                longitude=longitude,
                address=address,
                settings=settings,
                # The same identity the waiver below reads, so a non-pilot
                # Slider zone is costed against the courier the pilot gate would
                # actually book rather than against Slider's dead fare endpoint.
                user_id=user_id,
                email=email,
            )
        zone = priced.zone
        serviceable = priced.serviceable
        fee = priced.fee if priced.serviceable else None
        if fee is None:
            if strict:
                raise delivery_service.UnserviceableAreaError()
            delivery_fee = ZERO
            delivery_fee_known = False
        else:
            delivery_fee = fee

        # After the pin has been priced, never before: a pilot account still may
        # not order somewhere we cannot deliver to. Free is a discount, not an
        # exemption from geography — which is why this sits below the
        # `fee is None` branch above rather than short-circuiting it.
        #
        # It zeroes what the *customer* pays and nothing else. `quoted_cost` and
        # `cost_total` on `order_deliveries` still record what the courier
        # charges us, so the pilot can be costed truthfully from those two
        # columns; the margin figure will show every pilot order as fully
        # negative, correctly.
        if delivery_fee_known and trial_customer.is_trial_customer(user_id, email):
            delivery_fee = ZERO

    taxes = await tax_breakdown(db, lines=lines, discount_amount=discount_amount)

    # VAT is on goods only. Both fees sit outside it — the same UAE rule
    # delivery has always been handled under, and the small-basket surcharge is
    # treated identically so the two service charges on an order agree.
    #
    # The net is the discounted basket *minus the tax actually declared*, rather
    # than a second independent back-calculation from a flat rate. Identical for
    # today's catalogue (one 5% inclusive group, so the two agree to the fils),
    # and the difference shows the moment a basket mixes rates: only this
    # version is guaranteed to satisfy `total_excl_vat + vat_amount ==
    # discounted subtotal`, which is what an invoice has to add up to.
    vat_amount = taxes.total
    total_excl_vat = pos_pricing.money(discounted_subtotal - vat_amount)

    return OrderTotals(
        subtotal=subtotal,
        discount_amount=discount_amount,
        delivery_fee=delivery_fee,
        low_order_fee=low_order_fee,
        vat_rate=taxes.rate,
        vat_amount=vat_amount,
        total_excl_vat=total_excl_vat,
        total=discounted_subtotal + delivery_fee + low_order_fee,
        taxes=taxes.lines,
        zone=zone,
        delivery=priced,
        delivery_fee_known=delivery_fee_known,
        serviceable=serviceable,
    )
