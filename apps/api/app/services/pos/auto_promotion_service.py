"""
Auto-applied promotions — the discounts the register puts on by itself.

A `Promotion` with `auto_apply = True` is not a rule the cashier invokes; it is a
standing discount the pricing engine adds to every qualifying check on its own.
The one the shop runs is "every counter order is 15% off cookies, brownies and
cookie melts", but the mechanism is general: any order-level promotion
(`percentage_off_order` / `fixed_off_order`) fired on a `spend` trigger, scoped
by channel/branch/type/schedule.

A promotion with `category_ids` is confined to those categories: the discount is
written as one per-item `OrderDiscount` per matching line (so 15% comes off each
cookie and brownie and nothing else), which `recalculate` prices as line
discounts. Without `category_ids` it stays a single order-level discount spread
across the whole check, the original behaviour.

`sync_auto_discounts` is the whole of it, and it is called from
`pos_order_service.recalculate` — the single writer of money — so it re-runs
after every mutation and the discount tracks the basket as lines come and go.
It is deliberately idempotent: it reconciles the order to *exactly one*
auto-managed promotion discount (or none), so running it twice changes nothing
the second time.

Two invariants it respects, both inherited from how `recalculate` prices:

* **One order-level discount.** `pos_pricing.calculate_order` takes a single
  order-level discount, so a cashier's manual order-level discount and an
  auto-promotion cannot both be the order discount. The cashier wins: if a
  manual order-level discount is present, the auto one is removed and not
  re-added. A promotion is a floor the counter can override, not a thing that
  fights the person at the till.
* **Closed checks are frozen.** A closed/void order's discounts are history;
  this only touches an order still open.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketing import Promotion, PromotionRewardEnum, PromotionTriggerEnum
from app.models.order import Order, OrderItem, OrderItemStatusEnum
from app.models.pos_order import (
    DiscountSourceEnum,
    OrderDiscount,
    PosOrderStatusEnum,
)
from app.models.product import Product
from app.services.pos import business_day_service

#: The rewards an auto-apply promotion may carry. Both reduce to one order-level
#: `OrderDiscount` the engine can add unattended; a product-scoped or free-item
#: reward cannot, and the API refuses `auto_apply` on those (see `marketing.py`).
_AUTO_REWARDS = {
    PromotionRewardEnum.PERCENTAGE_OFF_ORDER.value,
    PromotionRewardEnum.FIXED_OFF_ORDER.value,
}

_OPEN_STATUSES = {
    PosOrderStatusEnum.DRAFT.value,
    PosOrderStatusEnum.PENDING.value,
    PosOrderStatusEnum.ACTIVE.value,
}


def _is_auto_managed(discount) -> bool:
    """A discount this service owns — a promotion it applied.

    Covers both shapes it can leave behind: the single order-level row a
    whole-order promotion adds, and the per-item rows a category-scoped one adds
    (one per matching line, `order_item_id` set). A promotion row is always this
    service's — nothing else writes `source == promotion` on an order — so the
    reconciler can safely add, update or clear any of them.
    """
    return (
        discount.source == DiscountSourceEnum.PROMOTION.value
        and discount.reference_id is not None
    )


def _has_manual_order_discount(order: Order) -> bool:
    """
    A cashier-applied order-level discount (open/predefined/coupon).

    Its presence means the cashier has already set the order discount by hand,
    and the one-order-level-discount rule says the auto promotion stands down.
    """
    return any(
        d.order_item_id is None and not _is_auto_managed(d)
        for d in order.order_discounts
    )


def _billable_items(order: Order) -> list[OrderItem]:
    """The lines that actually cost money — not voided, with units left to bill.

    The same set `recalculate` prices and `_spend_basis` sums, so a per-item
    discount is offered on exactly the lines a whole-order one would have covered.
    """
    live: list[OrderItem] = []
    for item in order.items:
        status = item.status or OrderItemStatusEnum.ACTIVE.value
        if status == OrderItemStatusEnum.VOID.value:
            continue
        if max(item.quantity - (item.returned_quantity or 0), 0) <= 0:
            continue
        live.append(item)
    return live


def _spend_basis(order: Order) -> Decimal:
    """
    What the check is worth before any discount — the figure a `spend` trigger
    is measured against. Voided lines and returned units do not count, matching
    how `recalculate` decides what is billable.
    """
    total = Decimal("0")
    for item in _billable_items(order):
        billable = max(item.quantity - (item.returned_quantity or 0), 0)
        unit = Decimal(str(item.base_price)) + Decimal(str(item.options_price))
        total += unit * billable
    return total


async def _candidates(
    db: AsyncSession, order: Order, spend: Decimal
) -> list[Promotion]:
    """Every auto-apply promotion that qualifies for this order, right now."""
    rows = (
        (
            await db.execute(
                select(Promotion).where(
                    Promotion.auto_apply.is_(True),
                    Promotion.is_active.is_(True),
                    Promotion.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    tz = await business_day_service.resolve_timezone(db)
    local = datetime.now(tz)
    weekday = local.weekday()
    minutes = local.hour * 60 + local.minute
    today = local.date()

    out: list[Promotion] = []
    for promo in rows:
        if promo.reward not in _AUTO_REWARDS:
            continue
        # Order-level rewards are unconditional-by-spend only; a quantity trigger
        # counts specific products and has no meaning for a whole-order discount.
        if promo.trigger != PromotionTriggerEnum.SPEND.value:
            continue
        if spend < Decimal(str(promo.trigger_value)):
            continue
        if not promo.matches_order(
            source=order.source,
            branch_id=order.branch_id,
            order_type=order.order_type,
        ):
            continue
        if promo.from_date and today < promo.from_date:
            continue
        if promo.to_date and today > promo.to_date:
            continue
        if not (promo.runs_on(weekday) and promo.runs_at(minutes)):
            continue
        out.append(promo)

    # Lowest priority wins; newest breaks a tie, matching `advertisable`'s rule
    # that publishing a replacement retires the one before it.
    out.sort(key=lambda p: (p.priority, -p.created_at.timestamp()))
    return out


async def sync_auto_discounts(db: AsyncSession, order: Order) -> None:
    """
    Reconcile `order` to the single auto-apply promotion it qualifies for.

    Mutates `order.order_discounts` in place (the collection `recalculate` is
    about to price) and flushes. A no-op on a closed order, on a non-POS order,
    or when a manual order-level discount is already present.
    """
    if not order.is_pos or order.pos_status not in _OPEN_STATUSES:
        return

    managed = [d for d in order.order_discounts if _is_auto_managed(d)]

    # The cashier has the wheel: a hand-applied order discount replaces the
    # promotion outright, and we clear any auto one we had left behind.
    if _has_manual_order_discount(order):
        for stale in managed:
            order.order_discounts.remove(stale)
        await db.flush()
        return

    spend = _spend_basis(order)
    candidates = await _candidates(db, order, spend)
    chosen = candidates[0] if candidates else None

    if chosen is None:
        for stale in managed:
            order.order_discounts.remove(stale)
        await db.flush()
        return

    is_percentage = chosen.reward == PromotionRewardEnum.PERCENTAGE_OFF_ORDER.value
    # `reward_value` is a percent for a percentage reward (15 == 15%); the
    # pricing engine wants a fraction. A fixed reward is already an AED amount.
    # The same `value` drives both shapes: a per-item percentage row takes it
    # off each matching line, an order-level one off the whole check.
    value = (
        Decimal(str(chosen.reward_value)) / Decimal("100")
        if is_percentage
        else Decimal(str(chosen.reward_value))
    )

    if chosen.category_ids:
        await _apply_per_category(db, order, chosen, is_percentage, value, managed)
    else:
        _apply_order_level(order, chosen, is_percentage, value, managed)

    await db.flush()


def _sync_managed_row(
    row: OrderDiscount, chosen: Promotion, is_percentage: bool, value: Decimal
) -> None:
    """Point an existing managed row at the chosen promotion, in place.

    Updating rather than delete+add keeps the discount's id stable across the
    re-prices `recalculate` runs on every mutation.
    """
    row.name = chosen.name
    row.reference_id = chosen.id
    row.is_percentage = is_percentage
    row.value = value


def _new_managed_row(
    chosen: Promotion,
    is_percentage: bool,
    value: Decimal,
    *,
    order_item_id=None,
) -> OrderDiscount:
    return OrderDiscount(
        order_item_id=order_item_id,
        source=DiscountSourceEnum.PROMOTION.value,
        name=chosen.name,
        reference_id=chosen.id,
        is_percentage=is_percentage,
        value=value,
        amount=Decimal("0"),  # filled by recalculate
        applied_by_id=None,  # nobody applied it; the engine did
    )


def _apply_order_level(
    order: Order,
    chosen: Promotion,
    is_percentage: bool,
    value: Decimal,
    managed: list[OrderDiscount],
) -> None:
    """The whole-order discount: exactly one row, `order_item_id` NULL.

    Also the migration path off a category-scoped config — any per-item managed
    rows left behind are cleared so only the single order-level one remains.
    """
    order_level = [d for d in managed if d.order_item_id is None]
    for stale in managed:
        if stale.order_item_id is not None:
            order.order_discounts.remove(stale)

    keep = order_level[0] if order_level else None
    for extra in order_level[1:]:
        order.order_discounts.remove(extra)

    if keep is None:
        order.order_discounts.append(_new_managed_row(chosen, is_percentage, value))
    else:
        _sync_managed_row(keep, chosen, is_percentage, value)


async def _apply_per_category(
    db: AsyncSession,
    order: Order,
    chosen: Promotion,
    is_percentage: bool,
    value: Decimal,
    managed: list[OrderDiscount],
) -> None:
    """Discount only the lines whose product sits in the chosen categories.

    One managed `OrderDiscount` per matching line (`order_item_id` set), which
    `recalculate` prices as a per-line discount — so 15% comes off each cookie,
    brownie and cookie-melt line and nothing else. Reconciles to exactly that
    set: rows for lines that no longer match (or an order-level row from a
    previous whole-order config) are cleared, missing ones are added.
    """
    categories = set(chosen.category_ids)
    live = _billable_items(order)
    product_ids = {item.product_id for item in live if item.product_id is not None}

    category_by_product: dict = {}
    if product_ids:
        rows = (
            await db.execute(
                select(Product.id, Product.category_id).where(
                    Product.id.in_(product_ids)
                )
            )
        ).all()
        category_by_product = {pid: cid for pid, cid in rows}

    matching_item_ids = {
        item.id
        for item in live
        if item.product_id is not None
        and category_by_product.get(item.product_id) in categories
    }

    # Reconcile the rows we already own against the lines that should have one.
    seen: set = set()
    for row in managed:
        if row.order_item_id in matching_item_ids and row.order_item_id not in seen:
            _sync_managed_row(row, chosen, is_percentage, value)
            seen.add(row.order_item_id)
        else:
            # A line that no longer qualifies, a duplicate, or the order-level
            # row from a previous whole-order configuration.
            order.order_discounts.remove(row)

    for item_id in matching_item_ids - seen:
        order.order_discounts.append(
            _new_managed_row(chosen, is_percentage, value, order_item_id=item_id)
        )
