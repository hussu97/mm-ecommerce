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

**Nothing here touches the POS.** A promoted order never attaches to a register,
never rings a device, never mirrors out to Foodics — status is driven through
`order_lifecycle.transition` under `acting_as(AGGREGATOR)`, and every outward
consequence (register publish, courier dispatch, refund, Foodics mirror-out) is
already gated to a storefront order or a non-aggregator actor (see
`order_lifecycle._consequences`).

**It does draw down stock**, because these are real sales that took product off
the shelf. Each line is mapped to a catalog product by name and the sale is
decremented on creation (only `is_stock_product` items move); a cancellation
restores it through the lifecycle. An unmatched line keeps `product_id` null and
moves no stock. To keep this from double-drawing history, promotion is **windowed
to the last `AGGREGATOR_LOOKBACK_DAYS`** — the single window the sweep uses too, so
what is mirrored and what becomes a real MM order stay in lockstep.

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
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.money import money
from app.models.aggregator import (
    GRAIN_LINE,
    AggregatorOrder,
    AggregatorOrderItem,
    AggregatorStatementLine,
)
from app.models.base import utcnow
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderSourceEnum, OrderTax
from app.models.product import Product
from app.services.aggregators import aggregator_fulfilment, reconcile
from app.services.aggregators.modifiers import modifiers_from_json
from app.services.catalog import external_item_map_service
from app.services.orders import order_fees, order_lifecycle
from app.services.orders.order_pricing import VAT_RATE
from app.services.pos import pos_order_service

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

#: English status words shared by Deliveroo, Talabat, and similar portals.
_ENGLISH_AGGREGATOR_STATUS_TO_MM: dict[str, OrderStatusEnum] = {
    "delivered": OrderStatusEnum.DELIVERED,
    "completed": OrderStatusEnum.DELIVERED,
    "picked up": OrderStatusEnum.DELIVERED,
    "canceled": OrderStatusEnum.CANCELLED,
    "cancelled": OrderStatusEnum.CANCELLED,
    "rejected": OrderStatusEnum.CANCELLED,
    "failed": OrderStatusEnum.CANCELLED,
    "declined": OrderStatusEnum.CANCELLED,
}

#: Keeta's order status. It historically arrived as a NUMERIC code (`40` settled,
#: `50` cancelled) but the current parser decodes it to an English word
#: ("completed"), so the map accepts BOTH — a promotion-owned Keeta order (DSO/Al
#: Karama) whose status the map does not recognise stalls at `confirmed` and so
#: never reaches the register or the reports, which is exactly what left 16 of a
#: day's Keeta orders off the daily sales report. Anything still unknown is left
#: indeterminate (promoted only as far as `confirmed` and logged), never guessed.
_KEETA_STATUS_TO_MM: dict[str, OrderStatusEnum] = {
    **_ENGLISH_AGGREGATOR_STATUS_TO_MM,
    "40": OrderStatusEnum.DELIVERED,
    "50": OrderStatusEnum.CANCELLED,
}

_STATUS_MAPS = {
    "keeta": _KEETA_STATUS_TO_MM,
    "deliveroo": _ENGLISH_AGGREGATOR_STATUS_TO_MM,
    "talabat": _ENGLISH_AGGREGATOR_STATUS_TO_MM,
    "noon": _ENGLISH_AGGREGATOR_STATUS_TO_MM,
    "careem": _ENGLISH_AGGREGATOR_STATUS_TO_MM,
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
    db: AsyncSession, agg: AggregatorOrder
) -> Order | None:
    """The MM order already filed for this marketplace order, if any.

    Primary key is the long marketplace id (`external_reference`), globally unique
    — the existing `uq_orders_source_external_reference` partial unique key — so
    promotion and the GrubOps ingest resolve to the same row regardless of how each
    spells `aggregator_channel`.

    A channel that also carries a short customer code (`display_ref` — Noon's
    `orderRef`, which GrubTech mirrors as its `externalId`) matches on that too, so
    a Barsha/Sharjah order GrubOps filed under the short code and a promotion that
    knows only the long `orderNr` still converge. That match is SCOPED to the same
    branch and business day, because the short code is only a per-branch-per-day
    sequence number and would otherwise collide with the same code on another day
    or channel — so this never merges two genuinely different orders. The day is
    read off `created_at` (the marketplace placed-at, which BOTH paths stamp) in
    Dubai time, because the GrubOps-made order carries no `business_date` column.
    """
    conds = [Order.external_reference == agg.external_order_id]
    if agg.display_ref and agg.branch_id is not None and agg.business_date:
        created_dubai_day = func.to_char(
            func.timezone("Asia/Dubai", Order.created_at), "YYYY-MM-DD"
        )
        conds.append(
            and_(
                Order.external_reference == agg.display_ref,
                Order.branch_id == agg.branch_id,
                created_dubai_day == agg.business_date,
            )
        )
    return await db.scalar(
        select(Order).where(
            Order.source == OrderSourceEnum.AGGREGATOR.value,
            or_(*conds),
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
        "vat_rate": VAT_RATE if vat > 0 else Decimal("0"),
        "vat_amount": vat,
        "total_excl_vat": excl,
    }


def _actual_fee_overrides(agg: AggregatorOrder) -> dict:
    """The marketplace's own settled figures, when it has reported them.

    `commission_amount` / `payment_fee` on the aggregator order are the fees the
    marketplace ACTUALLY took, read off its statement — dynamic, per-order, and
    null until the order settles. Handed to `order_fees.stamp`, each non-null one
    overrides the static configured-rate estimate on the MM order, so a promoted
    order's P&L reflects the real cut once it is known and the modelled cut until
    then. This is the fee half of the sales↔statement coupling reaching the MM
    order.
    """
    return {
        "actual_commission": agg.commission_amount,
        "actual_payment_fee": agg.payment_fee,
        "actual_cancellation_fee": agg.cancellation_fee,
    }


async def _match_product(
    db: AsyncSession, channel: str, item_name: str | None
) -> tuple:
    """Resolve a scraped line name to a catalogue product, returning (product_id, sku).

    Order of precedence:
    1. an **approved** `external_item_map` override — a human's curated mapping,
       the place to fix a name the catalogue spells differently (a size-suffixed
       variant like "… (500 grams)", say);
    2. a direct exact (case-insensitive) product name, then SKU, match.

    Whatever the outcome, the name is recorded as a map *proposal* so an unmapped
    or newly-seen item surfaces in the review queue rather than silently staying
    unlinked. An unmatched line keeps `product_id` null — no stock moves for it.
    """
    if not item_name or not item_name.strip():
        return None, ""
    # 1. approved override wins.
    pid, sku = await external_item_map_service.resolve_product(db, channel, item_name)
    if pid is not None:
        return pid, sku
    # 2. direct name / SKU match.
    name = item_name.strip()
    hit = (
        await db.execute(
            select(Product.id, Product.sku)
            .where(func.lower(Product.name) == name.lower())
            .order_by(Product.id)
            .limit(1)
        )
    ).first()
    if hit is None:
        hit = (
            await db.execute(
                select(Product.id, Product.sku).where(Product.sku == name).limit(1)
            )
        ).first()
    guess = hit[0] if hit is not None else None
    # 3. record the sighting for review (idempotent; never overwrites a curated row).
    await external_item_map_service.record_proposal(
        db, channel, item_name, guess_product_id=guess
    )
    if hit is None:
        return None, ""
    return hit[0], hit[1] or ""


def _rung_at(agg: AggregatorOrder, rung: OrderStatusEnum) -> datetime | None:
    """The most accurate timestamp for a rung transition.

    Prefers the portal's own event columns — the moment the event actually
    happened — over placed_at, which is only "when the order was placed". Using
    a single placed_at for every rung flattens the order's real timeline and
    confuses the status-event log when two rungs stamp the same second.
    """
    if rung == OrderStatusEnum.CANCELLED:
        return agg.cancelled_at or agg.placed_at
    if rung == OrderStatusEnum.DELIVERED:
        return agg.delivered_at or agg.placed_at
    # CONFIRMED / ARRIVED_AT_POS / PACKED: "accepted" is the closest event most
    # portals carry for the order-taken moment.
    return agg.accepted_at or agg.placed_at


async def _build_modifier_snapshot(
    db: AsyncSession,
    channel: str,
    mods: list,
) -> tuple[list[dict], Decimal]:
    """Resolve a StandardModifier list to catalog options and build the snapshot.

    Returns (snapshot, options_price). options_price only sums unit_prices the
    aggregator reported — a None price contributes 0. The catalog lookup is used
    for modifier_option_id linkage only; we never substitute catalog price for an
    aggregator price the portal did not report, per the money convention.

    The snapshot shape mirrors the GrubOps ingest (option_name / option_price
    dialect) so the admin item table and the register decode it identically.
    """
    snapshot: list[dict] = []
    options_price = Decimal("0")
    for mod in mods:
        opt_id, _, _ = await external_item_map_service.resolve_option(
            db, channel, mod.name, ref=mod.external_ref
        )
        if opt_id is None:
            await external_item_map_service.record_option_proposal(
                db, channel, mod.name, ref=mod.external_ref
            )
        unit_price = mod.unit_price if mod.unit_price is not None else Decimal("0")
        quantity = int(mod.quantity)
        options_price += unit_price * quantity
        snapshot.append(
            {
                "option_name": mod.name,
                "option_price": float(unit_price),
                "option_id": str(opt_id) if opt_id is not None else None,
                "modifier_option_id": str(opt_id) if opt_id is not None else None,
                "modifier_name": None,
                "modifier_id": None,
                "quantity": quantity,
            }
        )
    return snapshot, options_price


async def _add_lines(db: AsyncSession, order: Order, agg: AggregatorOrder) -> int:
    """Write the order's lines from the aggregator's line-grain items, mapping each
    to a catalog product by name where one is found. Returns the count of lines
    left unmapped (product_id null — no stock moved for those).

    Aggregate-grain rows (a period window, no per-order breakdown) carry nothing to
    file as a line and are skipped. Called only on a freshly built order, so it
    never reads `order.items` (an async lazy-load on a new row) — it only adds.
    """
    items = await db.scalars(
        select(AggregatorOrderItem).where(
            AggregatorOrderItem.aggregator_order_id == agg.id,
            AggregatorOrderItem.grain == GRAIN_LINE,
        )
    )
    unmapped = 0
    for it in items:
        qty = int(it.quantity) if it.quantity is not None else 1
        base_price = money(it.unit_price or Decimal("0"))
        mods = modifiers_from_json(it.modifiers)
        snapshot, opt_price = await _build_modifier_snapshot(db, agg.channel, mods)
        opt_price = money(opt_price)
        unit_price = money(base_price + opt_price)
        total = (
            money(it.gross_sales)
            if it.gross_sales is not None
            else money(unit_price * qty)
        )
        product_id, sku = await _match_product(db, agg.channel, it.item_name)
        if product_id is None:
            unmapped += 1
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product_id,
                product_name=it.item_name or "Item",
                product_sku=sku,
                product_translations={},
                quantity=qty,
                base_price=base_price,
                options_price=opt_price,
                unit_price=unit_price,
                total_price=total,
                selected_options_snapshot=snapshot,
                tax_amount=Decimal("0"),
            )
        )
    await db.flush()
    return unmapped


async def _decrement_stock(db: AsyncSession, order_id) -> None:
    """Take the promoted sale off the shelf for its stock-tracked lines — the same
    rule the counter and GrubOps ingest use. Only `is_stock_product` products, only
    on creation; a cancellation restores it through `order_lifecycle._move_stock`,
    which recognises the aggregator source. Unmapped lines (null product_id) move no
    stock."""
    rows = (
        await db.execute(
            select(OrderItem.product_id, OrderItem.quantity).where(
                OrderItem.order_id == order_id,
                OrderItem.product_id.isnot(None),
            )
        )
    ).all()
    for product_id, quantity in rows:
        await db.execute(
            sql_update(Product)
            .where(Product.id == product_id, Product.is_stock_product.is_(True))
            .values(stock_quantity=Product.stock_quantity - quantity)
            .execution_options(synchronize_session=False)
        )


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

    if target == OrderStatusEnum.CANCELLED:
        with acting_as(StatusSourceEnum.AGGREGATOR, at=_rung_at(agg, target)):
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
        with acting_as(StatusSourceEnum.AGGREGATOR, at=_rung_at(agg, rung)):
            moved = await order_lifecycle.transition(db, order, rung, on_invalid="skip")
        if not moved:
            return


def _display_code(external_reference: str | None) -> str | None:
    """The short, driver-facing pickup code for a promotion-owned order.

    Promotion owns exactly the orders GrubOps never sees — the branches not on
    GrubTech (DSO/Al Karama on Keeta) — so unlike grubops `_driver_code` there is
    no console sequence number to read. This is that function's reference-only
    subset: a marketplace id that is already short and numeric IS the code
    (Noon "5717", Deliveroo "0037"); a long machine id — Keeta's 16-digit
    `orderViewId` — collapses to its last four, so the handoff ticket shows a
    short code instead of the whole string. `external_reference` still keeps the
    full marketplace id. Kept in step with grubops `_driver_code` rules 2 and 4.
    """
    ext = str(external_reference).strip() if external_reference else ""
    if not ext:
        return None
    if ext.isdigit() and len(ext) <= 6:
        return ext
    return ext[-4:]


async def _build_order(
    db: AsyncSession, agg: AggregatorOrder, label: str, *, draw_stock: bool
) -> Order:
    order = Order(
        order_number=await _order_number(db),
        user_id=None,
        email="",
        customer_name=agg.customer_name or None,
        customer_phone=agg.customer_phone or None,
        # The marketplace's delivery address, into the same JSONB column a website
        # order snapshots its address into, so the admin renders both the same way.
        shipping_address_snapshot=agg.customer_address or None,
        # The marketplace's own rider, into the same columns the GrubOps ingest
        # fills on a Barsha/Sharjah order — so a DSO/Karama promote-owned order
        # shows a driver on the packed screen too.
        aggregator_driver_name=agg.driver_name or None,
        aggregator_driver_phone=agg.driver_phone or None,
        aggregator_driver_status=agg.driver_status or None,
        locale="en",
        delivery_method="delivery",
        order_type="delivery",
        status=OrderStatusEnum.CREATED,
        source=OrderSourceEnum.AGGREGATOR.value,
        aggregator_channel=label,
        external_reference=agg.external_order_id,
        # Prefer the marketplace's own short code (Noon's `orderRef`) so the ticket
        # and the GrubOps-adopt lookup both see the value GrubTech quotes, not the
        # last-four of a long machine id.
        aggregator_display_code=_display_code(agg.display_ref or agg.external_order_id),
        branch_id=agg.branch_id,
        business_date=agg.business_date,
        # Marketplace orders are prepaid through the app — card, not cash. The
        # scrape carries no per-order cash/card flag (only the GrubOps push does,
        # via `aggregator_payment_type`), so a scrape-promoted order defaults to
        # card, the overwhelmingly common case. MM never touched the card either
        # way, so this is a reporting label, not a refund route.
        payment_method="card",
        # The MM order's `created_at` is the moment the order was placed on the
        # MARKETPLACE, not the moment promotion happened to file it here — so order
        # history and any "created" sort/report line up with the aggregator's own
        # timeline. `placed_at` is that moment; fall back to now only if the scrape
        # carried no timestamp.
        created_at=agg.placed_at or utcnow(),
        **_money_fields(agg),
    )
    db.add(order)
    await db.flush()
    unmapped = await _add_lines(db, order, agg)
    if unmapped:
        logger.info(
            "promote %s %s: %d line(s) unmapped to a product — no stock moved for those",
            agg.channel,
            agg.external_order_id,
            unmapped,
        )
    # Load the collection now: driving status to cancelled walks order.items in
    # `_move_stock`, and an async lazy-load there would be a MissingGreenlet.
    await db.refresh(order, ["items"])
    # Take the sale off the shelf for its stock-tracked lines. On creation only,
    # and ONLY for an order inside the near-realtime sales window (`draw_stock`):
    # promotion now reaches back over the wider settlement window to close the
    # payout→statement→line→order→mm chain, but drawing stock for weeks-old
    # backfilled orders would corrupt inventory (the stock left the shelf when
    # the order was fresh, not now). A cancellation status below restores it via
    # the lifecycle (net zero for an order that arrives already cancelled).
    if draw_stock:
        await _decrement_stock(db, order.id)
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
    await order_fees.stamp(db, order, **_actual_fee_overrides(agg))
    await _drive_status(db, order, agg)
    return order


async def _refresh_order(db: AsyncSession, order: Order, agg: AggregatorOrder) -> None:
    """Bring an already-promoted order back in line with the ledger — its money
    and status, in case the marketplace mutated the order after we first filed it."""
    for field, value in _money_fields(agg).items():
        setattr(order, field, value)
    # Backfill the customer the scraper captured onto an order first filed without
    # it — one promoted before its channel exposed the customer (Keeta's
    # recipientInfo, Noon's customerInfo), or one this pass converged onto by
    # `(source, external_reference)`. Fill-only: a value already on the order (a
    # GrubOps-sourced order carries its own) is never overwritten, and `_build_order`
    # already sets it on a fresh promote — this closes the gap for the existing rows.
    if not order.customer_name and agg.customer_name:
        order.customer_name = agg.customer_name
    if not order.customer_phone and agg.customer_phone:
        order.customer_phone = agg.customer_phone
    if not order.shipping_address_snapshot and agg.customer_address:
        order.shipping_address_snapshot = agg.customer_address
    # DA info: refresh from the scrape (fill-only against a GrubOps-sourced order,
    # which carries its own rider from GrubTech and must not be overwritten).
    if not order.aggregator_driver_name and agg.driver_name:
        order.aggregator_driver_name = agg.driver_name
    if not order.aggregator_driver_phone and agg.driver_phone:
        order.aggregator_driver_phone = agg.driver_phone
    if not order.aggregator_driver_status and agg.driver_status:
        order.aggregator_driver_status = agg.driver_status
    # If the marketplace's short customer code has since landed, correct the
    # pickup/display code first derived from the long id (its last-4), so the
    # ticket and any later GrubOps-adopt lookup see the value GrubTech quotes.
    if agg.display_ref:
        want = _display_code(agg.display_ref)
        if want and order.aggregator_display_code != want:
            order.aggregator_display_code = want
    # Correct the created_at of an order first filed before this rule (or one this
    # pass converged onto) to the marketplace placed_at, so historical rows line
    # up with the aggregator timeline too. Only when the scrape has a timestamp.
    if agg.placed_at is not None and order.created_at != agg.placed_at:
        order.created_at = agg.placed_at
    await db.flush()
    await order_fees.stamp(db, order, **_actual_fee_overrides(agg))
    await _drive_status(db, order, agg)


async def _backfill_statement_lines(
    db: AsyncSession, channel: str, external_order_id: str, mm_order_id
) -> None:
    """Link statement lines for the same (channel, external_order_id) to the MM
    order just promoted, so finance queries can join without a separate
    aggregator_order hop. Only touches rows that are still null — a row already
    linked by an earlier promotion or by a direct write is left as is."""
    await db.execute(
        sql_update(AggregatorStatementLine)
        .where(
            AggregatorStatementLine.channel == channel,
            AggregatorStatementLine.external_order_id == external_order_id,
            AggregatorStatementLine.mm_order_id.is_(None),
        )
        .values(mm_order_id=mm_order_id)
        .execution_options(synchronize_session=False)
    )


def _grubops_adopt_grace_elapsed(agg: AggregatorOrder) -> bool:
    """Whether enough time has passed that a still-absent GrubOps order means
    GrubOps will not ingest this order — so promotion may file a standalone —
    rather than merely that the aggregator and GrubOps ingests raced.

    Dated off the marketplace `placed_at` (tz-aware after ingest), falling back to
    the Dubai `business_date`. With neither, do not defer forever.
    """
    grace = timedelta(hours=max(settings.AGGREGATOR_GRUBOPS_ADOPT_GRACE_HOURS, 0))
    placed = agg.placed_at
    if placed is not None:
        if placed.tzinfo is None:  # defensive — ingest stamps Dubai, but guard
            placed = placed.replace(tzinfo=_TZ)
        return utcnow() - placed >= grace
    if agg.business_date:
        try:
            bd = datetime.strptime(agg.business_date, "%Y-%m-%d").replace(tzinfo=_TZ)
        except ValueError:
            return True
        return utcnow() - bd >= grace
    return True  # nothing to date it by — do not defer indefinitely


async def promote_order(
    db: AsyncSession, agg: AggregatorOrder, *, draw_stock: bool = True
) -> Order | None:
    """Create or update the MM order for one aggregator order, honouring the
    per-branch ownership rules. Returns the MM order, or None when skipped.

    `draw_stock` gates the on-creation inventory decrement to the near-realtime
    sales window — a backfilled order (older than the sales lookback, promoted
    only to close the reconciliation chain) is filed without moving stock.
    """
    if agg.branch_id is None:
        return None  # cannot file an order without a branch

    label = reconcile.CHANNEL_GRUBOPS_LABEL.get(agg.channel, agg.channel)

    # Barsha/Sharjah: GrubOps/Foodics owns the order when it exists. Link to it so
    # the association is recorded, and never create it or touch its items/status
    # here. The one exception, and it is deliberate: once the marketplace settles
    # the order we overlay the ACTUAL commission / payment fee it charged onto the
    # order's fee columns (only those), because the GrubOps ingest could only
    # stamp the static configured-rate estimate and the real cut is what makes the
    # P&L honest. It is a null-guarded overlay — nothing happens until a statement
    # reports a figure — and the reconciliation recomputes its own modelled
    # estimate rather than reading these columns, so the variance check is not
    # blinded by the overlay.
    if await reconcile._branch_has_grubops(db, agg.branch_id):
        grubops_order = await reconcile._find_mm_order(
            db, agg.channel, agg.external_order_id, agg.display_ref
        )
        if grubops_order is not None:
            agg.mm_order_id = grubops_order.id
            agg.promoted_at = utcnow()
            await _backfill_statement_lines(
                db, agg.channel, agg.external_order_id, grubops_order.id
            )
            if agg.commission_amount is not None or agg.payment_fee is not None:
                await order_fees.stamp(db, grubops_order, **_actual_fee_overrides(agg))
            await _record_fulfilment(db, grubops_order)
            return grubops_order
        # No GrubOps order found on a GrubOps branch. GrubOps is the source of
        # truth here, so filing a standalone now is how the same order gets filed
        # twice — promotion racing ahead of the GrubOps ingest, or ahead of the
        # short `display_ref` that convergence keys on. Defer within the grace
        # window and retry next tick (the incremental cursor re-selects this order
        # until it links). Only past the grace — GrubOps genuinely never took the
        # order — fall through and file a standalone as recovery.
        if not _grubops_adopt_grace_elapsed(agg):
            logger.info(
                "promote %s %s: GrubOps branch, no GrubOps order yet — deferring "
                "(within adopt grace) rather than filing a duplicate",
                agg.channel,
                agg.external_order_id,
            )
            return None
        logger.warning(
            "promote %s %s: GrubOps branch, no GrubOps order after %dh grace — "
            "filing a standalone; GrubOps appears not to have ingested this order",
            agg.channel,
            agg.external_order_id,
            settings.AGGREGATOR_GRUBOPS_ADOPT_GRACE_HOURS,
        )

    existing = await _find_convergence_order(db, agg)
    if existing is None:
        order = await _build_order(db, agg, label, draw_stock=draw_stock)
    else:
        await db.refresh(existing, ["items"])
        await _refresh_order(db, existing, agg)
        order = existing

    # File the promotion-owned order onto the register as a historical, settled
    # POS order — so it appears in the POS screens, POS reports and the daily
    # sales email exactly like a GrubOps-ingested order, with no distinction. This
    # is the promotion-owned branch (DSO/Karama and any aggregator-only sale); the
    # Barsha/Sharjah GrubOps-owned path returned above already attached the order.
    # Idempotent (guarded on the check number) and a no-op until the order is
    # terminal, so a still-in-progress order is filed on a later re-promote.
    await pos_order_service.attach_promoted_aggregator_order(
        db, order, placed_at=agg.placed_at, delivered_at=agg.delivered_at
    )

    agg.mm_order_id = order.id
    agg.promoted_at = utcnow()
    await db.flush()
    await _backfill_statement_lines(db, agg.channel, agg.external_order_id, order.id)
    await _record_fulfilment(db, order)
    return order


async def _record_fulfilment(db: AsyncSession, order: Order) -> None:
    """Mirror the order's marketplace rider into the shared fulfilment tables
    (`order_deliveries` + `order_drivers`), so the details page shows one
    fulfilment section for every order type. Reads the order's own
    `aggregator_driver_*` columns — populated by _build_order/_refresh_order on the
    scrape path and by the GrubOps ingest on the matched path — so this one call
    covers both promotion exits. Best-effort: a fulfilment write must never fail a
    promotion (the money side is already done)."""
    try:
        await aggregator_fulfilment.record_aggregator_fulfilment(
            db,
            order,
            channel=getattr(order, "aggregator_channel", None),
            driver_name=getattr(order, "aggregator_driver_name", None),
            driver_phone=getattr(order, "aggregator_driver_phone", None),
            driver_status=getattr(order, "aggregator_driver_status", None),
            cancel_reason=getattr(order, "aggregator_cancel_reason", None),
            delivery_fee=getattr(order, "aggregator_delivery_fee", None),
        )
    except Exception:  # noqa: BLE001 — never fail a promotion on the fulfilment mirror
        logger.exception(
            "promote: could not mirror fulfilment for order %s",
            getattr(order, "order_number", "?"),
        )


async def promote_channel(db: AsyncSession, channel: str) -> int:
    """Promote the channel's recent new-or-changed orders. Returns MM orders touched.

    Windowed by business date to `AGGREGATOR_PROMOTE_LOOKBACK_DAYS`, which is
    SEPARATE from and wider than the sales lookback: a settlement statement posts
    days-to-weeks after the sale, and a statement line only links to its MM order
    once that order is promoted — so promotion has to reach back over the whole
    settlement window or the payout→statement→line→order→mm chain never closes for
    anything older than the sales window (this is why a 1-day promotion left every
    statement line unlinked). Stock, by contrast, is drawn only for orders inside
    the tight sales window (`draw_stock`) — a weeks-old backfill is filed for
    linkage without moving inventory. Incremental within the window, like
    `reconcile_channel`: an order is (re)promoted only when it has no `promoted_at`
    yet or its `updated_at` has advanced past it. Idempotent and safe to re-run —
    the convergence key means a re-run updates rather than duplicates. A single
    order's failure is logged and does not stop the pass.
    """
    today = datetime.now(_TZ).date()
    cutoff = (
        today - timedelta(days=max(settings.AGGREGATOR_PROMOTE_LOOKBACK_DAYS, 0))
    ).isoformat()
    #: Orders on or after this date still move stock; older promoted orders are
    #: linkage-only backfill.
    stock_cutoff = (
        today - timedelta(days=max(settings.AGGREGATOR_LOOKBACK_DAYS, 0))
    ).isoformat()
    orders = await db.scalars(
        select(AggregatorOrder).where(
            AggregatorOrder.channel == channel,
            AggregatorOrder.branch_id.is_not(None),
            AggregatorOrder.business_date >= cutoff,
            or_(
                AggregatorOrder.promoted_at.is_(None),
                AggregatorOrder.updated_at > AggregatorOrder.promoted_at,
            ),
        )
    )
    count = 0
    for agg in orders:
        try:
            draw_stock = (agg.business_date or "") >= stock_cutoff
            if await promote_order(db, agg, draw_stock=draw_stock) is not None:
                count += 1
        except Exception:  # noqa: BLE001 — one order must not stop the pass
            logger.exception(
                "promote %s order %s failed", channel, agg.external_order_id
            )
    return count
