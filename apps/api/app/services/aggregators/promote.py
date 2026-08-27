"""Promote a scraped aggregator order into a real MM order — a records mirror.

Reconciliation (`reconcile.py`) checks a marketplace's ledger against the MM
order it *became*; but only Barsha and Sharjah produce that MM order, through the
GrubOps ingest. This module fills the gap for the other two branches and the
orders GrubOps never saw, so every branch's aggregator sales exist as full
`orders` + `order_items` + status the same way a website order does.

The rules, per the branch:

* **DSO / Karama** — off-platform, no GrubOps, no register. Promotion *owns* these
  orders: it creates and updates them.
* **Barsha / Sharjah** — GrubOps/Foodics is the source of truth. Promotion only
  **gap-fills**: if GrubOps already made the MM order (its `grubops_order_map`
  row carries an `mm_order_id`) promotion leaves it alone; only an order GrubOps
  never produced is created here.

**Nothing here touches the POS.** A promoted order is a reporting record, not a
kitchen ticket: it never attaches to a register, never rings a device, never
mirrors out to Foodics. That falls out of two choices rather than a special case:
status is driven through `order_lifecycle.transition` under
`acting_as(AGGREGATOR)`, and every outward consequence — register publish,
courier dispatch, refund, Foodics mirror-out — is already gated to a storefront
order or to a non-aggregator actor (see `order_lifecycle._consequences`); and the
promoted lines carry no `product_id`, so `_move_stock` is a no-op and the shelf
is left exactly as the off-platform sale left it.

Convergence: both this path and the GrubOps ingest resolve an aggregator order to
one MM row through `orders (aggregator_channel, external_reference)` — the partial
unique key from migration 156 — so a Barsha order GrubOps later delivers adopts
the promotion gap-fill instead of duplicating it. `aggregator_channel` is the
GrubOps display name (`CHANNEL_GRUBOPS_LABEL`), so both sides spell the key alike.

Incremental like reconciliation: an order is (re)promoted only when it has no
`promoted_at` yet or its `updated_at` has advanced past it. Per the transaction
convention nothing here commits — the caller's sweep does.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.aggregator import (
    GRAIN_LINE,
    AggregatorOrder,
    AggregatorOrderItem,
)
from app.models.base import utcnow
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderSourceEnum, OrderTax
from app.services.aggregators import reconcile
from app.services.orders import order_fees, order_lifecycle

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Dubai")

#: The forward ladder, as GrubOps climbs it — created→confirmed→arrived→packed→
#: delivered — so a promoted order's timeline reads like a real one and each
#: rung's (no-op, for an aggregator order) consequence fires once. Cancellation is
#: not on the ladder; it is attempted directly.
_LADDER: list[OrderStatusEnum] = [
    OrderStatusEnum.CREATED,
    OrderStatusEnum.CONFIRMED,
    OrderStatusEnum.ARRIVED_AT_POS,
    OrderStatusEnum.PACKED,
    OrderStatusEnum.DELIVERED,
]

#: Keeta's numeric order-status codes → the MM status they mean. `40` is the
#: settled/completed state (the overwhelming majority on prod); `50` is a
#: cancelled/closed order. Anything else is left indeterminate — promoted only as
#: far as `confirmed` and logged — rather than guessed into delivered or cancelled.
_KEETA_STATUS_TO_MM: dict[str, OrderStatusEnum] = {
    "40": OrderStatusEnum.DELIVERED,
    "50": OrderStatusEnum.CANCELLED,
}

#: Deliveroo's human status words → MM status.
_DELIVEROO_STATUS_TO_MM: dict[str, OrderStatusEnum] = {
    "delivered": OrderStatusEnum.DELIVERED,
    "completed": OrderStatusEnum.DELIVERED,
    "canceled": OrderStatusEnum.CANCELLED,
    "cancelled": OrderStatusEnum.CANCELLED,
    "rejected": OrderStatusEnum.CANCELLED,
    "failed": OrderStatusEnum.CANCELLED,
}

_STATUS_MAPS = {
    "keeta": _KEETA_STATUS_TO_MM,
    "deliveroo": _DELIVEROO_STATUS_TO_MM,
}


def _target_status(channel: str, raw: str | None) -> OrderStatusEnum | None:
    """The MM status a channel's raw status word means, or None if unknown."""
    if not raw:
        return None
    return _STATUS_MAPS.get(channel, {}).get(raw.strip().lower())


async def _order_number(db: AsyncSession) -> str:
    """`AGG-YYYYMMDD-NNN` — the aggregator series, shared with the GrubOps ingest
    so promoted and GrubOps orders read alike and never collide with MM-/POS-."""
    prefix = f"AGG-{datetime.now(_TZ):%Y%m%d}-"
    last = (
        await db.execute(
            select(
                func.max(cast(func.split_part(Order.order_number, "-", 3), Integer))
            ).where(Order.order_number.like(f"{prefix}%"))
        )
    ).scalar_one_or_none()
    return f"{prefix}{int(last or 0) + 1:03d}"


async def _find_convergence_order(
    db: AsyncSession, external_reference: str
) -> Order | None:
    """The MM order already filed for this marketplace reference, if any.

    Keyed on `(source, external_reference)` — the existing
    `uq_orders_source_external_reference` partial unique key — not on the channel
    label, so promotion and the GrubOps ingest resolve to the same row even if
    they spell `aggregator_channel` slightly differently.
    """
    return await db.scalar(
        select(Order).where(
            Order.source == OrderSourceEnum.AGGREGATOR.value,
            Order.external_reference == external_reference,
        )
    )


def _money_fields(agg: AggregatorOrder) -> dict:
    """The order's money, taken verbatim from the marketplace ledger. MM books no
    delivery fee and no discount for a promoted order — the aggregator charged and
    kept them — so only the customer-facing delivery charge is carried, on its own
    column, for the record."""
    total = money(agg.gross_sales or Decimal("0"))
    vat = money(agg.vat_amount or Decimal("0"))
    excl = money(total - vat)
    return {
        "subtotal": excl,
        "discount_amount": Decimal("0"),
        "delivery_fee": Decimal("0"),
        "aggregator_delivery_fee": money(agg.delivery_fee or Decimal("0")),
        "low_order_fee": Decimal("0"),
        "total": total,
        "vat_rate": Decimal("0.05") if vat > 0 else Decimal("0"),
        "vat_amount": vat,
        "total_excl_vat": excl,
    }


async def _add_lines(db: AsyncSession, order: Order, agg: AggregatorOrder) -> None:
    """Write the order's lines from the aggregator's line-grain items.

    `product_id` is deliberately left null: a promoted order is a record, not a
    stock movement, and a null product is what keeps `_move_stock` (and therefore
    the shelf) untouched. Aggregate-grain rows (a period window, no per-order
    breakdown) carry nothing to file as a line and are skipped. Called only on a
    freshly built order, so it never reads `order.items` (an async lazy-load on a
    new row) — it only adds.
    """
    items = await db.scalars(
        select(AggregatorOrderItem).where(
            AggregatorOrderItem.aggregator_order_id == agg.id,
            AggregatorOrderItem.grain == GRAIN_LINE,
        )
    )
    for it in items:
        qty = int(it.quantity) if it.quantity is not None else 1
        unit = money(it.unit_price or Decimal("0"))
        total = (
            money(it.gross_sales) if it.gross_sales is not None else money(unit * qty)
        )
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=None,
                product_name=it.item_name or "Item",
                product_sku="",
                product_translations={},
                quantity=qty,
                base_price=unit,
                options_price=Decimal("0"),
                unit_price=unit,
                total_price=total,
                selected_options_snapshot=[],
                tax_amount=Decimal("0"),
            )
        )
    await db.flush()


async def _drive_status(db: AsyncSession, order: Order, agg: AggregatorOrder) -> None:
    """Move the promoted order to the status the marketplace reports, through the
    one lifecycle door under an aggregator actor (so no POS/Foodics echo)."""
    target = _target_status(agg.channel, agg.status)
    if target is None:
        # Unknown status word — file it no further than confirmed rather than
        # guess it delivered or cancelled, and say so.
        logger.info(
            "promote %s %s: unmapped status %r — left at confirmed",
            agg.channel,
            agg.external_order_id,
            agg.status,
        )
        target = OrderStatusEnum.CONFIRMED

    at = agg.placed_at
    if target == OrderStatusEnum.CANCELLED:
        with acting_as(StatusSourceEnum.AGGREGATOR, at=at):
            await order_lifecycle.transition(db, order, target, on_invalid="skip")
        return

    if target not in _LADDER:
        return
    target_idx = _LADDER.index(target)
    while True:
        try:
            current_idx = _LADDER.index(order.status)
        except ValueError:
            return  # off the ladder (already cancelled/delivered) — nothing to climb
        if current_idx >= target_idx:
            return
        rung = _LADDER[current_idx + 1]
        with acting_as(StatusSourceEnum.AGGREGATOR, at=at):
            moved = await order_lifecycle.transition(db, order, rung, on_invalid="skip")
        if not moved:
            return


async def _build_order(db: AsyncSession, agg: AggregatorOrder, label: str) -> Order:
    order = Order(
        order_number=await _order_number(db),
        user_id=None,
        email="",
        customer_name=None,
        locale="en",
        delivery_method="delivery",
        order_type="delivery",
        status=OrderStatusEnum.CREATED,
        source=OrderSourceEnum.AGGREGATOR.value,
        aggregator_channel=label,
        external_reference=agg.external_order_id,
        branch_id=agg.branch_id,
        business_date=agg.business_date,
        payment_method="cod",
        **_money_fields(agg),
    )
    db.add(order)
    await db.flush()
    await _add_lines(db, order, agg)
    # Load the collection now: driving status to cancelled walks order.items in
    # `_move_stock`, and an async lazy-load there would be a MissingGreenlet.
    await db.refresh(order, ["items"])
    if (agg.vat_amount or 0) > 0:
        fields = _money_fields(agg)
        db.add(
            OrderTax(
                order_id=order.id,
                tax_id=None,
                name="VAT",
                rate=fields["vat_rate"],
                taxable_amount=fields["total_excl_vat"],
                amount=fields["vat_amount"],
            )
        )
        await db.flush()
    await order_fees.stamp(db, order)
    await _drive_status(db, order, agg)
    return order


async def _refresh_order(db: AsyncSession, order: Order, agg: AggregatorOrder) -> None:
    """Bring an already-promoted order back in line with the ledger — its money
    and status, in case the marketplace mutated the order after we first filed it."""
    for field, value in _money_fields(agg).items():
        setattr(order, field, value)
    await db.flush()
    await order_fees.stamp(db, order)
    await _drive_status(db, order, agg)


async def promote_order(db: AsyncSession, agg: AggregatorOrder) -> Order | None:
    """Create or update the MM order for one aggregator order, honouring the
    per-branch ownership rules. Returns the MM order, or None when skipped."""
    if agg.branch_id is None:
        return None  # cannot file an order without a branch

    label = reconcile.CHANNEL_GRUBOPS_LABEL.get(agg.channel, agg.channel)

    # Barsha/Sharjah: GrubOps/Foodics owns the order when it exists. Link to it so
    # the association is recorded, but never create or edit it here.
    if await reconcile._branch_has_grubops(db, agg.branch_id):
        grubops_order = await reconcile._find_mm_order(
            db, agg.channel, agg.external_order_id
        )
        if grubops_order is not None:
            agg.mm_order_id = grubops_order.id
            agg.promoted_at = utcnow()
            return grubops_order

    existing = await _find_convergence_order(db, agg.external_order_id)
    if existing is None:
        order = await _build_order(db, agg, label)
    else:
        await db.refresh(existing, ["items"])
        await _refresh_order(db, existing, agg)
        order = existing

    agg.mm_order_id = order.id
    agg.promoted_at = utcnow()
    await db.flush()
    return order


async def promote_channel(db: AsyncSession, channel: str) -> int:
    """Promote the channel's new-or-changed orders. Returns MM orders touched.

    Incremental, like `reconcile_channel`: an order is (re)promoted only when it
    has no `promoted_at` yet or its `updated_at` has advanced past it. Idempotent
    and safe to re-run — the convergence key means a re-run updates rather than
    duplicates. A single order's failure is logged and does not stop the pass.
    """
    orders = await db.scalars(
        select(AggregatorOrder).where(
            AggregatorOrder.channel == channel,
            AggregatorOrder.branch_id.is_not(None),
            or_(
                AggregatorOrder.promoted_at.is_(None),
                AggregatorOrder.updated_at > AggregatorOrder.promoted_at,
            ),
        )
    )
    count = 0
    for agg in orders:
        try:
            if await promote_order(db, agg) is not None:
                count += 1
        except Exception:  # noqa: BLE001 — one order must not stop the pass
            logger.exception(
                "promote %s order %s failed", channel, agg.external_order_id
            )
    return count
