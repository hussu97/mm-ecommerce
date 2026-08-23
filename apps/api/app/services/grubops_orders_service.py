"""Turn a GrubOps aggregator order into an MM order, and mirror MM back.

This is the domain layer of the order-ingest feature: it knows how a GrubOps
order maps onto our `orders` table and lifecycle, and nothing about HTTP or the
polling loop. `services/grubops_orders.py` (the loop) calls `ingest` for each
order it sees; `order_lifecycle` calls `push_status_out_in_background` when an
aggregator order reaches a terminal state here.

Three things make an aggregator order different from a website order, and each
is handled deliberately rather than by pretending it is an `online` sale:

* **It is priced and charged by the aggregator.** The money is taken verbatim
  from GrubOps — there is no cart to re-price, and re-pricing would raise on a
  delivery address GrubOps records as "Unknown".
* **It is delivered by the aggregator's own rider.** MM books no courier and
  runs no arrival sweep for it — `order_lifecycle._mm_owns_fulfilment` gates
  that machinery off.
* **We only learn of it by polling.** So the loop reconciles GrubOps's status
  onto ours each tick, walking the lifecycle ladder rather than assuming a
  single hop; `on_invalid="skip"` absorbs any order GrubOps reports out of turn.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, cast, func, select
from sqlalchemy import update as sql_update

from app.core.config import settings
from app.core.money import money
from app.models.branch import Branch
from app.models.grubops import GrubOpsItemMap, GrubOpsLocationMap
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderSourceEnum, OrderTax, PosOrderStatusEnum
from app.models.product import Product
from app.services import order_fees, order_lifecycle
from app.services.providers.grubops_provider import GrubOpsError, provider

logger = logging.getLogger(__name__)

__all__ = [
    "is_enabled",
    "ingest",
    "LIVE_STATUSES",
    "push_status_out_in_background",
    "mirror_status_out",
]

#: Dubai, for the order-number date. The shop's day, not UTC's — the same
#: reasoning as `order_service._generate_order_number`.
_TZ = "Asia/Dubai"

#: The GrubOps statuses the loop asks for. In-progress ones plus the two
#: terminal ones, so an order that finishes between ticks still lands.
LIVE_STATUSES: list[str] = [
    "OrderCreated",
    "OrderAccepted",
    "OrderStarted",
    "OrderResumed",
    "OrderPrepared",
    "OrderReadyToDispatch",
    "OrderDispatched",
    "OrderCompleted",
    "OrderCanceled",
    "OrderRejected",
    "OrderFailed",
]

#: GrubOps status → the MM status it means. `OrderOnHold` is deliberately absent:
#: a hold is not a lifecycle move of ours, so an order on hold stays where it is.
_STATUS_TO_MM: dict[str, OrderStatusEnum] = {
    "OrderCreated": OrderStatusEnum.CONFIRMED,
    "OrderAccepted": OrderStatusEnum.CONFIRMED,
    "OrderStarted": OrderStatusEnum.CONFIRMED,
    "OrderResumed": OrderStatusEnum.CONFIRMED,
    "OrderPrepared": OrderStatusEnum.PACKED,
    "OrderReadyToDispatch": OrderStatusEnum.PACKED,
    "OrderDispatched": OrderStatusEnum.PACKED,
    "OrderCompleted": OrderStatusEnum.DELIVERED,
    "OrderCanceled": OrderStatusEnum.CANCELLED,
    "OrderRejected": OrderStatusEnum.CANCELLED,
    "OrderFailed": OrderStatusEnum.CANCELLED,
}

#: The forward ladder. A GrubOps order seen for the first time as completed has
#: to climb created → confirmed → packed → delivered rather than jump, so its
#: MM timeline reads like a real order's and each rung's consequences fire once.
_LADDER: list[OrderStatusEnum] = [
    OrderStatusEnum.CREATED,
    OrderStatusEnum.CONFIRMED,
    OrderStatusEnum.PACKED,
    OrderStatusEnum.DELIVERED,
]

#: How a terminal GrubOps outcome closes the order out on the register board.
#: The *active* life of the check is the register's own — `pending` at ingest,
#: `active` when a cashier (or the auto-accept) takes it — so the ladder only
#: touches pos_status to clear a finished order off the board: `closed` when the
#: aggregator delivered it, `void` when it was cancelled. A never-accepted order
#: that GrubOps completes still gets closed here, which is correct — Foodics
#: fulfilled it and nobody needs to accept it on MM any more.
_POS_STATUS: dict[OrderStatusEnum, str] = {
    OrderStatusEnum.DELIVERED: PosOrderStatusEnum.CLOSED.value,
    OrderStatusEnum.CANCELLED: PosOrderStatusEnum.VOID.value,
}

#: Fire-and-forget write-back tasks, held so the loop's GC does not cancel them
#: mid-flight — the same idiom as `grubops_service._pending` / `indexnow`.
_pending: set[asyncio.Task] = set()


def is_enabled() -> bool:
    return settings.GRUBOPS_ORDERS_ENABLED and provider.is_configured


def _num(value: Any, default: str = "0") -> Decimal:
    """A `Decimal`, or a default — GrubOps sends floats, and `subtotal` is
    sometimes null on a cash order."""
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


#: Pulls a marketplace short code out of the order instructions, e.g.
#: "No cutlery. | Talabat-short code: 1445" → "1445".
_SHORT_CODE_RE = re.compile(r"short.?code[:\s]+(\d{3,6})", re.IGNORECASE)


def _driver_code(header: dict, external_id: str | None, info: dict) -> str | None:
    """The short, driver-facing pickup code, by the surest rule per channel.

    There is no single field for it — each marketplace surfaces its handoff code
    differently — so we take the best available, in order:

    1. an explicit short code embedded in the instructions (Talabat: "1445");
    2. the external id when it is already short and numeric (Noon "5717",
       Deliveroo "0037" — for these the "external id" *is* the customer's number);
    3. the GrubOps sequence number, which is what the console shows the counter
       for a Keeta/Careem order whose own id is a long machine string;
    4. as a last resort, the last four of the external id.

    `external_reference` always keeps the full marketplace id regardless.
    """
    instructions = header.get("instructions") or ""
    match = _SHORT_CODE_RE.search(instructions)
    if match:
        return match.group(1)
    ext = str(external_id).strip() if external_id else ""
    if ext and ext.isdigit() and len(ext) <= 6:
        return ext
    seq = (info.get("orderSequenceNumber") or {}).get("createdSequence")
    if seq:
        return str(seq)
    if ext:
        return ext[-4:]
    return None


#: The whole "short code" clause, so it can be cut out of a note rather than
#: only read from one. Talabat appends its own routing metadata to the
#: customer's words with a pipe, e.g.
#: "No cutlery.  | Talabat-short code: 1452".
_SHORT_CODE_CLAUSE_RE = re.compile(
    r"\s*\|?\s*[\w-]*short.?code[:\s]+\d{3,6}\s*", re.IGNORECASE
)


def _customer_note(header: dict) -> str | None:
    """
    What the customer actually asked for, with the marketplace's plumbing removed.

    `instructions` is two things joined with a pipe: the customer's sentence and
    Talabat's own routing metadata. Both used to print, so a kitchen docket read
    `No cutlery.  | Talabat-short code: 1452` — a number that is *already* on
    the same ticket, in the box at the top, four times the size. A note is the
    one line on a docket somebody has to act on, and padding it with a duplicate
    of the largest number on the page is how it stops being read.

    The code itself is not lost: `_driver_code` reads it from the raw
    instructions and puts it in the box, which is where it is useful.

    Returns None when nothing but the metadata was there, so an order with no
    real note prints no note block at all rather than an empty rule.
    """
    raw = (header.get("instructions") or "").strip()
    if not raw:
        return None
    cleaned = _SHORT_CODE_CLAUSE_RE.sub(" ", raw)
    # Collapse what the removal leaves behind: the double spaces Talabat sends
    # anyway, and any orphaned separator at either end.
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" |").strip()
    return cleaned or None


# ── reading a GrubOps order ──────────────────────────────────────────────────


async def _resolve_branch(db, location_id: str | None) -> uuid.UUID | None:
    """The MM branch for a GrubOps location, via the branch map.

    Resolved regardless of `is_active`: that flag governs whether we push stock
    *out* to that branch's aggregators, not whether we record the orders coming
    *in*. A branch with no map row is one we cannot file an order against —
    `orders.branch_id` is NOT NULL — so its orders are skipped and counted.
    """
    if not location_id:
        return None
    row = (
        await db.execute(
            select(GrubOpsLocationMap.branch_id).where(
                GrubOpsLocationMap.grubops_location_id == location_id
            )
        )
    ).scalar_one_or_none()
    return row


async def _reverse_maps(
    db, recipe_ids: set[str], modifier_ids: set[str]
) -> tuple[dict[str, uuid.UUID], dict[str, dict[str, Any]]]:
    """GrubOps recipe/modifier id → our product / option, from the same
    approved `grubops_item_map` the OOS sync maintains.

    Only approved rows resolve — an unapproved guess must not silently attach a
    real order line to the wrong product.
    """
    products: dict[str, uuid.UUID] = {}
    options: dict[str, dict[str, Any]] = {}
    if recipe_ids:
        rows = await db.execute(
            select(GrubOpsItemMap.grubops_recipe_id, GrubOpsItemMap.product_id).where(
                GrubOpsItemMap.grubops_recipe_id.in_(recipe_ids),
                GrubOpsItemMap.product_id.isnot(None),
                GrubOpsItemMap.approved.is_(True),
            )
        )
        for gid, pid in rows:
            products[gid] = pid
    if modifier_ids:
        rows = await db.execute(
            select(
                GrubOpsItemMap.grubops_modifier_id,
                GrubOpsItemMap.modifier_option_id,
            ).where(
                GrubOpsItemMap.grubops_modifier_id.in_(modifier_ids),
                GrubOpsItemMap.modifier_option_id.isnot(None),
                GrubOpsItemMap.approved.is_(True),
            )
        )
        for gid, oid in rows:
            options[gid] = {"modifier_option_id": oid}
    return products, options


def _group_lines(order_lines: list[dict]) -> list[dict]:
    """Walk the flat line list into items each carrying their modifiers.

    GrubOps sends one row per ITEM and one per MODIFIER, in order, with the
    modifiers following the item they belong to. There is no parent pointer, so
    the grouping is positional — a MODIFIER attaches to the most recent ITEM,
    which is exactly how the console and KDS render it.
    """
    groups: list[dict] = []
    for line in order_lines or []:
        if line.get("type") == "ITEM":
            groups.append({"item": line, "modifiers": []})
        elif line.get("type") == "MODIFIER" and groups:
            groups[-1]["modifiers"].append(line)
    return groups


async def _generate_order_number(db) -> str:
    """`AGG-YYYYMMDD-NNN` — its own series, so aggregator numbers never collide
    with `MM-` (website) or `POS-` (counter)."""
    today = datetime.now(ZoneInfo(_TZ)).strftime("%Y%m%d")
    prefix = f"AGG-{today}-"
    last = (
        await db.execute(
            select(
                func.max(cast(func.split_part(Order.order_number, "-", 3), Integer))
            ).where(Order.order_number.like(f"{prefix}%"))
        )
    ).scalar_one_or_none()
    return f"{prefix}{int(last or 0) + 1:03d}"


def _history_at(histories: list[dict], mm_status: OrderStatusEnum) -> datetime | None:
    """The GrubOps timestamp for the event that means `mm_status`, so the MM
    timeline is stamped when GrubOps says it happened, not when we polled."""
    wanted = {
        OrderStatusEnum.CONFIRMED: {"OrderCreated", "OrderAccepted", "OrderStarted"},
        OrderStatusEnum.PACKED: {"OrderPrepared", "OrderReadyToDispatch"},
        OrderStatusEnum.DELIVERED: {"OrderCompleted"},
        OrderStatusEnum.CANCELLED: {"OrderCanceled", "OrderRejected", "OrderFailed"},
    }.get(mm_status, set())
    best: datetime | None = None
    for h in histories or []:
        if h.get("status") in wanted:
            ts = _parse_ts(h.get("timeStamp"))
            if ts and (best is None or ts < best):
                best = ts
    return best


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ── the two write paths ──────────────────────────────────────────────────────


async def ingest(db, info: dict, order_map: GrubOpsOrderMap) -> None:
    """Create the MM order if new, then move it to mirror GrubOps's status.

    `order_map` is the already-upserted ledger row for this GrubOps order; this
    fills its `mm_order_id` on first creation and advances `last_grubops_status`.
    """
    header = info.get("orderHeader") or {}
    gr_status = header.get("orderStatus") or info.get("orderStatus")
    target = _STATUS_TO_MM.get(gr_status)

    if order_map.mm_order_id is None:
        order = await _create_order(db, info, order_map)
        if order is None:
            return
        order_map.mm_order_id = order.id
        await db.flush()
    else:
        order = (
            await db.execute(select(Order).where(Order.id == order_map.mm_order_id))
        ).scalar_one_or_none()
        if order is None:
            return
        # Load the lines the restock/consequences need.
        await db.refresh(order, ["items"])

    if target is not None:
        await _apply_status(db, order, target, info.get("orderHistories") or [])

    order_map.last_grubops_status = gr_status
    order_map.raw = info
    await db.flush()


async def _create_order(db, info: dict, order_map: GrubOpsOrderMap) -> Order | None:
    header = info.get("orderHeader") or {}
    customer = info.get("customer") or {}

    branch_id = await _resolve_branch(db, order_map.location_id)
    if branch_id is None:
        logger.warning(
            "GrubOps order %s at location %s has no branch map row; skipped",
            order_map.grubops_order_id,
            order_map.location_id,
        )
        order_map.last_push_error = "no branch map for location"
        return None

    groups = _group_lines(info.get("orderLines") or [])
    recipe_ids = {
        g["item"].get("recipeId") for g in groups if g["item"].get("recipeId")
    }
    modifier_ids = {
        m.get("modifierId")
        for g in groups
        for m in g["modifiers"]
        if m.get("modifierId")
    }
    products, options = await _reverse_maps(db, recipe_ids, modifier_ids)

    # Money verbatim from GrubOps. `subtotal` is null on a cash order, so it
    # falls back to the taxable unit price; everything else is present.
    total = _num(header.get("totalPrice"))
    vat_amount = _num(header.get("taxAmount"))
    subtotal = header.get("subtotal")
    subtotal = _num(subtotal) if subtotal is not None else _num(header.get("unitPrice"))
    net = header.get("netPrice")
    total_excl_vat = _num(net) if net is not None else money(total - vat_amount)
    taxes = info.get("orderTaxes") or []
    vat_rate = _num(taxes[0].get("rate")) / Decimal("100") if taxes else Decimal("0.05")

    phone = customer.get("customerMobile") or customer.get("customerId")
    name = customer.get("customerName")
    unmapped = 0

    # The channel name for display: prefer the header's own `foodAggregatorName`
    # / `sourceDisplayName` (clean: "Talabat", "Keeta 2.0", "Noon"), falling back
    # to whatever the summary put on the map row.
    src = info.get("orderSource") or {}
    channel = (
        header.get("foodAggregatorName")
        or header.get("sourceDisplayName")
        or src.get("sourceDisplayName")
        or order_map.source_channel
    )

    order = Order(
        order_number=await _generate_order_number(db),
        user_id=None,
        email="",
        customer_name=(name if name and name not in ("UNKNOWN", "") else None),
        customer_phone=(phone if phone and phone not in ("UNKNOWN", "0") else None),
        locale="en",
        delivery_method="delivery",
        order_type="delivery",
        # MM neither sets nor collects the aggregator's delivery charge, and it is
        # not part of `total`. Keep our `delivery_fee` at zero so no sales or
        # freight report counts it; carry what the customer paid on the
        # aggregator-only column, for the receipt alone.
        delivery_fee=Decimal("0"),
        aggregator_delivery_fee=money(_num(header.get("deliveryTotalPrice"))),
        low_order_fee=Decimal("0"),
        subtotal=money(subtotal),
        discount_amount=money(_num(header.get("discountAmount"))),
        total=money(total),
        vat_rate=vat_rate.quantize(Decimal("0.0001")),
        vat_amount=money(vat_amount),
        total_excl_vat=money(total_excl_vat),
        status=OrderStatusEnum.CREATED,
        source=OrderSourceEnum.AGGREGATOR.value,
        aggregator_channel=channel,
        aggregator_display_code=_driver_code(header, order_map.external_id, info),
        external_reference=order_map.external_id,
        branch_id=branch_id,
        # Payment lives with the aggregator; MM books none. `cod` for a cash
        # (POSTPAID) order, and for a prepaid one still not `card` — MM never
        # took the card, so nothing here may look refundable to the register.
        payment_method="cod",
        notes=_customer_note(header),
    )
    db.add(order)
    await db.flush()

    # Onto the branch's register as a waiting order, then ring the doorbell —
    # the same pair a published website order gets, so the iPad alarms and the
    # kitchen ticket prints in MMPOS styling. `notify_order_placed` sends to the
    # branch's devices only, so a branch with no live terminal (Barsha today)
    # simply records the order without alarming anyone.
    from app.services import pos_order_service, push_service

    branch = await db.get(Branch, branch_id)
    if branch is not None:
        await pos_order_service.attach_aggregator_order(db, order, branch)
        await db.flush()
        await push_service.notify_order_placed(db, order)

    for g in groups:
        item = g["item"]
        recipe_id = item.get("recipeId")
        product_id = products.get(recipe_id)
        if product_id is None:
            unmapped += 1
        snapshot: list[dict] = []
        options_price = Decimal("0")
        for m in g["modifiers"]:
            mid = m.get("modifierId")
            opt = options.get(mid)
            if opt is None:
                unmapped += 1
            price = _num(m.get("unitPrice"))
            options_price += price
            option_id = str(opt["modifier_option_id"]) if opt else None
            # The canonical option-snapshot shape every reader expects — the
            # admin's item table renders `option_name`/`option_price`, and the
            # register reads `option_name`. Writing `name`/`price` (an older,
            # different shape) left the admin showing "1×" with no name.
            snapshot.append(
                {
                    "option_name": m.get("name"),
                    "option_price": float(price),
                    "option_id": option_id,
                    #: Kept as an alias for the register, which reads either.
                    "modifier_option_id": option_id,
                    #: GrubOps has no per-line modifier-group name to give.
                    "modifier_name": None,
                    "modifier_id": mid,
                    "quantity": int(_num(m.get("quantity"), "1")),
                }
            )
        base_price = _num(item.get("unitPrice"))
        quantity = int(_num(item.get("quantity"), "1"))
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product_id,
                product_name=item.get("name") or "Item",
                product_sku=(recipe_id or "")[:100],
                product_translations={},
                quantity=quantity,
                base_price=money(base_price),
                options_price=money(options_price),
                unit_price=money(base_price + options_price),
                total_price=money(_num(item.get("totalPrice"), str(base_price))),
                selected_options_snapshot=snapshot,
                tax_amount=money(_num(item.get("taxAmount"))),
                # What the customer said about *this line*. GrubOps carries it
                # per order line and we were dropping it on the floor: a
                # "no nuts" against one cake in a basket of four arrived at the
                # kitchen as nothing at all, while the same sentence typed on
                # our own website printed in bold. Same column, so the docket
                # renders both identically.
                kitchen_notes=(item.get("instructions") or None),
            )
        )

    # One VAT row, the same shape a website order writes, so the receipt and the
    # admin show the tax breakdown rather than a blank line. GrubOps prices
    # tax-inclusive at 5%; `total_excl_vat` is the taxable base.
    if vat_amount > 0:
        db.add(
            OrderTax(
                order_id=order.id,
                tax_id=None,
                name="VAT",
                rate=vat_rate.quantize(Decimal("0.0001")),
                taxable_amount=money(total_excl_vat),
                amount=money(vat_amount),
            )
        )

    await db.flush()  # the lines must exist before stock reads them back

    # What the marketplace takes off this order, from the rates on its
    # `couriers` row. Stamped here because an aggregator total is final the
    # moment it arrives — the marketplace priced it, and nothing downstream
    # moves it. A channel with no rate configured leaves both columns null,
    # which reads as "not itemised" rather than "free".
    await order_fees.stamp(db, order)

    await _decrement_stock(db, order.id)
    if unmapped:
        logger.warning(
            "GrubOps order %s ingested with %d unmapped line(s)",
            order_map.grubops_order_id,
            unmapped,
        )
    return order


async def _decrement_stock(db, order_id: uuid.UUID) -> None:
    """Take an aggregator sale off the shelf, the same rule checkout uses.

    Only `is_stock_product` products, and only on first creation — the caller
    reaches here once per order. A cancellation gives it back through
    `order_lifecycle._move_stock`, which now recognises `aggregator`.
    """
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


async def _apply_status(
    db, order: Order, target: OrderStatusEnum, histories: list[dict]
) -> None:
    """Move the order up the ladder to `target`, one honest rung at a time.

    Cancellation is not on the ladder — it can arrive from most states — so it
    is attempted directly; if the order is already past the point GrubOps can
    cancel from (packed, delivered), the map refuses and `skip` lets it stand.
    """
    if target == OrderStatusEnum.CANCELLED:
        at = _history_at(histories, target)
        with acting_as(StatusSourceEnum.AGGREGATOR, at=at):
            await order_lifecycle.transition(db, order, target, on_invalid="skip")
        _sync_pos_status(order, target)
        return

    if target not in _LADDER:
        return
    target_idx = _LADDER.index(target)
    while True:
        try:
            current_idx = _LADDER.index(order.status)
        except ValueError:
            # Off the ladder (already cancelled/delivered/refunded) — nothing to
            # climb.
            return
        if current_idx >= target_idx:
            break
        rung = _LADDER[current_idx + 1]
        at = _history_at(histories, rung)
        with acting_as(StatusSourceEnum.AGGREGATOR, at=at):
            moved = await order_lifecycle.transition(db, order, rung, on_invalid="skip")
        _sync_pos_status(order, rung)
        if not moved:
            break


def _sync_pos_status(order: Order, mm_status: OrderStatusEnum) -> None:
    pos = _POS_STATUS.get(mm_status)
    if pos is not None:
        order.pos_status = pos


# ── write-back: mirror an MM terminal status out to GrubOps ──────────────────


async def mirror_status_out(
    db, *, mm_order_id, new_status: OrderStatusEnum, actor: str
) -> None:
    """Reflect an MM cancel/complete onto GrubOps via the force-* overrides.

    Only if GrubOps still allows it: the order's `orderManagementOptions` are
    re-read live, and a flag of false (GrubOps has already moved on) is recorded
    rather than forced. The map row's `last_pushed_status` / `last_push_error`
    carry the outcome.
    """
    endpoint = {
        OrderStatusEnum.CANCELLED: ("force_cancel", "forceCancellationAllowed"),
        OrderStatusEnum.DELIVERED: ("force_complete", "forceCompletionAllowed"),
    }.get(new_status)
    if endpoint is None:
        return
    method_name, gate = endpoint

    order_map = (
        await db.execute(
            select(GrubOpsOrderMap).where(GrubOpsOrderMap.mm_order_id == mm_order_id)
        )
    ).scalar_one_or_none()
    if order_map is None:
        return
    grubops_order_id = order_map.grubops_order_id

    try:
        info = await provider.get_order(grubops_order_id)
        options = (info or {}).get("orderManagementOptions") or {}
        if not options.get(gate):
            order_map.last_push_error = f"{gate} is false; GrubOps already moved on"
            await db.flush()
            return
        await getattr(provider, method_name)(
            grubops_order_id, f"Mirrored from Melting Moments ({actor})"
        )
        order_map.last_pushed_status = new_status.value
        order_map.last_push_error = None
    except GrubOpsError as exc:
        order_map.last_push_error = str(exc)[:500]
    await db.flush()


def push_status_out_in_background(
    *, mm_order_id, new_status: OrderStatusEnum, actor: str
) -> None:
    """Fire-and-forget mirror-out. Never raises, no-op when disabled or when no
    event loop is running (tests, the register's sync paths)."""
    if not is_enabled():
        return

    async def _run() -> None:
        from app.core.database import AsyncSessionFactory

        try:
            async with AsyncSessionFactory() as db:
                await mirror_status_out(
                    db,
                    mm_order_id=mm_order_id,
                    new_status=new_status,
                    actor=actor,
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — best-effort, the loop reconciles
            logger.exception("GrubOps mirror-out failed for order %s", mm_order_id)

    try:
        task = asyncio.create_task(_run())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        # No running loop — nothing to mirror from here.
        pass
