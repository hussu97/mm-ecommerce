"""
Auto-applied promotions — the discounts the register puts on by itself.

A `Promotion` with `auto_apply = True` is not a rule the cashier invokes; it is a
standing discount the pricing engine adds to every qualifying check on its own.
The one the shop runs is "every counter order is 15% off", but the mechanism is
general: any order-level promotion (`percentage_off_order` / `fixed_off_order`)
fired on a `spend` trigger, scoped by channel/branch/type/schedule.

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
from app.models.order import Order, OrderItemStatusEnum
from app.models.pos_order import (
    DiscountSourceEnum,
    OrderDiscount,
    PosOrderStatusEnum,
)
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
    """An order-level discount this service owns — a promotion it applied."""
    return (
        discount.order_item_id is None
        and discount.source == DiscountSourceEnum.PROMOTION.value
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


def _spend_basis(order: Order) -> Decimal:
    """
    What the check is worth before any discount — the figure a `spend` trigger
    is measured against. Voided lines and returned units do not count, matching
    how `recalculate` decides what is billable.
    """
    total = Decimal("0")
    for item in order.items:
        status = item.status or OrderItemStatusEnum.ACTIVE.value
        if status == OrderItemStatusEnum.VOID.value:
            continue
        billable = max(item.quantity - (item.returned_quantity or 0), 0)
        if billable <= 0:
            continue
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
    value = (
        Decimal(str(chosen.reward_value)) / Decimal("100")
        if is_percentage
        else Decimal(str(chosen.reward_value))
    )

    # Keep one managed row and update it in place; drop any duplicates. Updating
    # rather than delete+add keeps the discount's id stable across re-prices.
    keep = managed[0] if managed else None
    for extra in managed[1:]:
        order.order_discounts.remove(extra)

    if keep is None:
        order.order_discounts.append(
            OrderDiscount(
                order_item_id=None,
                source=DiscountSourceEnum.PROMOTION.value,
                name=chosen.name,
                reference_id=chosen.id,
                is_percentage=is_percentage,
                value=value,
                amount=Decimal("0"),  # filled by recalculate
                applied_by_id=None,  # nobody applied it; the engine did
            )
        )
    else:
        keep.name = chosen.name
        keep.reference_id = chosen.id
        keep.is_percentage = is_percentage
        keep.value = value

    await db.flush()
