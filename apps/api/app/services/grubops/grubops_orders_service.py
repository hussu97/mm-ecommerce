"""Turn a GrubOps aggregator order into an MM order, and mirror MM back.

This is the domain layer of the order-ingest feature: it knows how a GrubOps
order maps onto our `orders` table and lifecycle, and nothing about HTTP or the
polling loop. `services/grubops_orders.py` (the loop) calls `ingest` for each
order it sees. Driving the order *back out* — dispatch/finalise/cancel — is no
longer done here: `order_lifecycle` mirrors an MM move onto the **Foodics** order
(the POS behind GrubTech) via `foodics_orders_service`, which replaced the GrubOps
`order-force-*` overrides this file used to fire.

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
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.money import money
from app.core.phone import describe_phone
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.external_item_map import KIND_OPTION, KIND_PRODUCT, ExternalItemMap
from app.models.grubops import GrubOpsLocationMap
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.order_status_event import (
    OrderStatusEvent,
    StatusSourceEnum,
    acting_as,
)
from app.models.pos_order import OrderSourceEnum, OrderTax, PosOrderStatusEnum
from app.models.product import Product
from app.services.orders import order_fees, order_lifecycle
from app.services.providers.grubops_provider import provider

logger = logging.getLogger(__name__)

__all__ = [
    "is_enabled",
    "ingest",
    "LIVE_STATUSES",
    "sweep_auto_close",
    "sweep_open_orders",
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
#:
#: **The shop, not the poll loop, owns `packed`.** An aggregator order lands at
#: `arrived_at_pos` ("at the shop") and waits there for a person to press Packed
#: — which is what dispatches the Foodics order and calls the rider. So the
#: prepared/dispatched family maps to `arrived_at_pos`, not `packed`: mirroring
#: GrubOps's own "prepared" straight to `packed` would jump past the shop and
#: skip the Foodics dispatch our side is now responsible for. Only a genuinely
#: terminal GrubOps outcome (completed / cancelled) drives us forward on its own,
#: as a safety net for an order finished aggregator-side.
_STATUS_TO_MM: dict[str, OrderStatusEnum] = {
    "OrderCreated": OrderStatusEnum.CONFIRMED,
    "OrderAccepted": OrderStatusEnum.ARRIVED_AT_POS,
    "OrderStarted": OrderStatusEnum.ARRIVED_AT_POS,
    "OrderResumed": OrderStatusEnum.ARRIVED_AT_POS,
    "OrderPrepared": OrderStatusEnum.ARRIVED_AT_POS,
    "OrderReadyToDispatch": OrderStatusEnum.ARRIVED_AT_POS,
    "OrderDispatched": OrderStatusEnum.ARRIVED_AT_POS,
    "OrderCompleted": OrderStatusEnum.DELIVERED,
    "OrderCanceled": OrderStatusEnum.CANCELLED,
    "OrderRejected": OrderStatusEnum.CANCELLED,
    "OrderFailed": OrderStatusEnum.CANCELLED,
}

#: The forward ladder. A GrubOps order seen for the first time as completed has
#: to climb created → confirmed → arrived_at_pos → packed → delivered rather than
#: jump, so its MM timeline reads like a real order's and each rung's consequences
#: fire once. In the ordinary live case the loop only ever targets `arrived_at_pos`
#: and stops there; `packed`/`delivered` are reached on this ladder only when
#: GrubOps reports the order already completed.
_LADDER: list[OrderStatusEnum] = [
    OrderStatusEnum.CREATED,
    OrderStatusEnum.CONFIRMED,
    OrderStatusEnum.ARRIVED_AT_POS,
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


#: A customer field that means "not given". GrubOps fills these rather than
#: leaving the key out, and each channel has its own spelling of nothing.
_UNKNOWN_CUSTOMER = {"", "0", "unknown", "unknown unknown", "none", "null"}


def _clean_customer_field(value: Any) -> str | None:
    """A real customer value, or None for one of GrubOps's placeholders."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _UNKNOWN_CUSTOMER:
        return None
    return text


def _looks_like_email(value: str) -> bool:
    """An `@` and no spaces — enough to tell a private-relay address from a name."""
    return "@" in value and " " not in value


#: Name, phone (E.164), phone country, phone type, access code, email.
_CustomerFields = tuple[
    str | None, str | None, str | None, str | None, str | None, str | None
]


def _customer_fields(customer: dict) -> _CustomerFields:
    """Name, phone and email, untangled and normalised per channel.

    GrubOps hands these in shapes that differ by marketplace and need sorting out:

    * **The name is sometimes an email.** Deliveroo sends the customer's Apple
      private-relay address as `customerName` with `customerEmail` null — which
      is what put "5sg2…@privaterelay.appleid.com" in the name row. An
      email-shaped name is filed as the email and the name left blank, rather
      than shown as a name.
    * **The number is normalised like every other.** `customerMobile` (or
      `customerId`) goes through `describe_phone`, so an aggregator number is
      stored the same E.164 way a website one is, with its country and line type
      beside it. Talabat's masked landline, Noon's mobile and Deliveroo's
      toll-free line all normalise.
    * **Deliveroo's access code stays its own field.** The mobile is a generic
      Deliveroo line and `customerPhoneCode` is the code to enter to reach the
      customer through it — kept apart from the number and joined only for display.
    * **Placeholders.** "UNKNOWN", "unknown unknown", "", "0" all mean "not
      given" and become null rather than being shown.
    """
    raw_name = _clean_customer_field(customer.get("customerName"))
    email = _clean_customer_field(customer.get("customerEmail"))
    name: str | None = raw_name
    if raw_name and _looks_like_email(raw_name):
        # The "name" is really an email — file it as one, and show no name.
        email = email or raw_name
        name = None

    raw_phone = _clean_customer_field(customer.get("customerMobile")) or (
        _clean_customer_field(customer.get("customerId"))
    )
    parts = describe_phone(raw_phone)
    # E.164 where it parses; the cleaned raw where a real number will not (a
    # driver still has to ring it), and None where there was nothing.
    phone = parts.e164 or raw_phone
    code = _clean_customer_field(customer.get("customerPhoneCode"))
    if phone is None:
        code = None

    return name, phone, parts.country, parts.type, code, email


def _payment_type(header: dict) -> str | None:
    """`prepaid` (card) or `postpaid` (cash), or None when the header is silent.

    `paymentStatus` is the field that carries it on every channel we see
    (`PREPAID` / `POSTPAID`); `paymentMethod` says `CASH` on the channels that
    send it and agrees. Either naming cash means cash. This drives whether a
    marketplace's payment fee applies (Careem waives its 2% on cash), so an
    unknown is left null rather than guessed — the fee logic reads a null as
    card, the common case and the one a null should not silently zero.
    """
    status = (header.get("paymentStatus") or "").strip().upper()
    method = (header.get("paymentMethod") or "").strip().upper()
    if status == "POSTPAID" or method == "CASH":
        return "postpaid"
    if status == "PREPAID" or method == "PREPAID":
        return "prepaid"
    return None


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
    """GrubOps recipe/modifier id → our product / option, from the same approved
    `external_item_map` (system `grubops`) the OOS sync maintains.

    Only approved rows resolve — an unapproved guess must not silently attach a
    real order line to the wrong product. A GrubOps recipe lives on `external_ref`
    and a modifier on `external_sub_ref` (under its recipe), so the lookups filter
    by `mm_kind` to keep the two apart.
    """
    products: dict[str, uuid.UUID] = {}
    options: dict[str, dict[str, Any]] = {}
    if recipe_ids:
        rows = await db.execute(
            select(ExternalItemMap.external_ref, ExternalItemMap.product_id).where(
                ExternalItemMap.system == "grubops",
                ExternalItemMap.mm_kind == KIND_PRODUCT,
                ExternalItemMap.external_ref.in_(recipe_ids),
                ExternalItemMap.product_id.isnot(None),
                ExternalItemMap.approved.is_(True),
            )
        )
        for gid, pid in rows:
            products[gid] = pid
    if modifier_ids:
        rows = await db.execute(
            select(
                ExternalItemMap.external_sub_ref,
                ExternalItemMap.modifier_option_id,
            ).where(
                ExternalItemMap.system == "grubops",
                ExternalItemMap.mm_kind == KIND_OPTION,
                ExternalItemMap.external_sub_ref.in_(modifier_ids),
                ExternalItemMap.modifier_option_id.isnot(None),
                ExternalItemMap.approved.is_(True),
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


def _placed_at(info: dict) -> datetime | None:
    """When the order was placed on the marketplace — the `OrderCreated` history
    event, falling back to the earliest order-line `createdAt`. Used as the MM
    order's `created_at` so its timeline matches the aggregator's, not our poll."""
    for h in info.get("orderHistories") or []:
        if h.get("status") == "OrderCreated":
            ts = _parse_ts(h.get("timeStamp"))
            if ts:
                return ts
    line_times = [
        _parse_ts(line.get("createdAt"))
        for line in (info.get("orderLines") or [])
        if isinstance(line, dict)
    ]
    line_times = [t for t in line_times if t]
    return min(line_times) if line_times else None


#: The GrubOps history statuses that mean "the order ended cancelled", so the
#: reason can be read off the event's own description when the header omits it.
_CANCEL_STATUSES = {"OrderCanceled", "OrderRejected", "OrderFailed"}


def _cancel_reason(info: dict) -> str | None:
    """Why GrubOps cancelled this order, verbatim, or None.

    `orderHeader.cancelReason` is the clean machine code (`TOO_BUSY`,
    `ITEM_OUT_OF_STOCK`, …) and is preferred. Some cancellations leave it null
    and carry the reason only in the free-text description of the cancel history
    event, so that is the fallback. Returned as GrubOps spells it — the humanising
    is the reader's, the same way `aggregator_driver_status` is stored raw.
    """
    header = info.get("orderHeader") or {}
    reason = header.get("cancelReason")
    if reason:
        text = str(reason).strip()
        if text:
            return text[:60]
    for h in info.get("orderHistories") or []:
        if h.get("status") in _CANCEL_STATUSES:
            desc = str(h.get("description") or "").strip()
            if desc:
                return desc[:60]
    return None


# ── the two write paths ──────────────────────────────────────────────────────


#: GrubOps fills the driver fields with one of these before a real rider is
#: assigned. Treated as "no driver yet" rather than written to the order, so the
#: packed screen shows nothing instead of the word "UNKNOWN".
_UNKNOWN_DRIVER = {"", "unknown", "0", "none", "null"}


def _clean_driver_field(value: Any) -> str | None:
    """A real driver value, or None for one of GrubOps's placeholders."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _UNKNOWN_DRIVER:
        return None
    return text


def _apply_driver_info(order: Order, info: dict) -> None:
    """Copy the aggregator rider's name / phone / job status onto the order.

    From `orderDelivery`, which GrubOps refreshes as the delivery job progresses,
    so this runs every ingest tick — a rider is often assigned minutes after the
    order lands. Placeholders ("UNKNOWN", "0", "") are dropped rather than stored.
    Only ever fills or updates; never wipes a real value we already hold back to
    null on a later tick that happens to omit it.
    """
    delivery = info.get("orderDelivery") or {}
    name = _clean_driver_field(delivery.get("deliveryOrderDriverName"))
    phone = _clean_driver_field(delivery.get("deliveryOrderDriverMobile"))
    status = _clean_driver_field(
        delivery.get("deliveryOrderStatus") or delivery.get("deliveryStatus")
    )
    if name is not None:
        order.aggregator_driver_name = name
    if phone is not None:
        order.aggregator_driver_phone = phone
    if status is not None:
        order.aggregator_driver_status = status


#: The Foodics order id, as GrubOps embeds it. GrubTech does not surface a POS-id
#: field; it only records the publish in the order history, e.g.
#: "Order External Id - 4961 Foodics Order Id: f172c019-ba85-46c4-88d7-cb85f728696f"
#: (status `PUBLISHING_ORDER_CREATED_TO_POS_SUCCEEDED`, code 20000). The uuid is
#: the id the Foodics API and console use for the order, so it is the write-back's
#: handle. Parsed rather than field-read because there is no field.
_FOODICS_ORDER_ID_RE = re.compile(
    r"Foodics\s+Order\s+Id\s*[:\-]?\s*"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.IGNORECASE,
)


def _foodics_order_id(info: dict) -> str | None:
    """Pull the Foodics order id out of a `getOrderInfo` payload, or None.

    Prefers the dedicated publish event (code 20000) and falls back to scanning
    every history description, so a change in which event carries the line does
    not lose it. Returns the first uuid found; there is only ever one.
    """
    histories = info.get("orderHistories") or []
    ordered = sorted(
        histories,
        key=lambda h: 0 if h.get("code") == 20000 else 1,
    )
    for entry in ordered:
        match = _FOODICS_ORDER_ID_RE.search(str(entry.get("description") or ""))
        if match:
            return match.group(1)
    return None


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

    # Refresh the rider details each tick — they arrive after the order does.
    _apply_driver_info(order, info)

    if target is not None:
        reason = _cancel_reason(info) if target == OrderStatusEnum.CANCELLED else None
        await _apply_status(
            db, order, target, info.get("orderHistories") or [], reason=reason
        )

    order_map.last_grubops_status = gr_status
    order_map.raw = info
    # Cache the Foodics id the first time GrubOps reveals it (the publish event
    # lands a beat after creation), so the Foodics write-back needs no re-parse.
    if order_map.foodics_order_id is None:
        foodics_id = _foodics_order_id(info)
        if foodics_id is not None:
            order_map.foodics_order_id = foodics_id
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

    # Name, normalised phone (E.164) with its country and line type, any Deliveroo
    # access code, and email — sorted out per channel; see `_customer_fields`.
    name, phone, phone_country, phone_type, phone_code, email = _customer_fields(
        customer
    )
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

    # Convergence: if order promotion already gap-filled this order (a
    # Barsha/Sharjah sale GrubOps had not yet produced), adopt that MM row rather
    # than insert a second one — the existing `uq_orders_source_external_reference`
    # unique key (source='aggregator', external_reference) forbids a duplicate, and
    # GrubOps is authoritative from here. Its status ladder runs on the adopted
    # order as usual. The gap-fill carried no product_id (a records mirror moves no
    # stock), so this rare handover leaves the shelf as the promotion left it rather
    # than reconciling it here.
    if order_map.external_id:
        # A promotion may have gap-filled under the marketplace's LONG id
        # (Noon's `orderNr`) while GrubTech quotes only the SHORT `externalId`
        # ("2253"). Promotion mirrors that short code onto `aggregator_display_code`,
        # so also adopt a gap-fill found there — scoped to this branch + placed day
        # (the short code is a per-branch-per-day sequence) so it never adopts an
        # unrelated order carrying the same code on another day.
        placed = _placed_at(info)
        placed_day = (
            placed.astimezone(ZoneInfo(_TZ)).date().isoformat() if placed else None
        )
        match = [Order.external_reference == order_map.external_id]
        if placed_day:
            match.append(
                and_(
                    Order.aggregator_display_code == order_map.external_id,
                    Order.branch_id == branch_id,
                    func.to_char(func.timezone(_TZ, Order.created_at), "YYYY-MM-DD")
                    == placed_day,
                )
            )
        adopted = await db.scalar(
            select(Order)
            .where(
                Order.source == OrderSourceEnum.AGGREGATOR.value,
                or_(*match),
            )
            .options(selectinload(Order.items))
        )
        if adopted is not None:
            logger.info(
                "GrubOps order %s adopts promotion gap-fill %s",
                order_map.grubops_order_id,
                adopted.order_number,
            )
            return adopted

    order = Order(
        order_number=await _generate_order_number(db),
        user_id=None,
        # The customer's email where the marketplace gave one (Deliveroo's
        # private-relay address included) — for display only. MM sends an
        # aggregator customer nothing (`email_service.is_counter_sale` covers
        # them), so this never becomes a recipient; it is the address a person
        # would use, not one we write to.
        email=email or "",
        customer_name=name,
        customer_phone=phone,
        customer_phone_country=phone_country,
        customer_phone_type=phone_type,
        customer_phone_access_code=phone_code,
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
        # What the customer actually paid with, so the console and reports read
        # true. `paymentStatus` is the reliable discriminator — `POSTPAID` is cash,
        # `PREPAID` is card; `paymentMethod == 'CASH'` says the same on the channels
        # that send it. An unknown reads as card (the common case), which is why a
        # marketplace order defaults to card here rather than the old flat `cod`.
        # MM never touched the card either way, so this is a reporting label, not a
        # refund route to the register.
        payment_method="cod" if _payment_type(header) == "postpaid" else "card",
        # Recorded verbatim too, because a marketplace's payment fee can turn on it
        # (Careem waives its 2% on cash). Null stays null here and reads as card
        # downstream in the fee logic.
        aggregator_payment_type=_payment_type(header),
        # Loyalty membership (Careem Plus, Talabat Pro) is not in the payload —
        # left unknown, which the fee logic treats as "not a member". See the
        # column's own note on why this is null rather than a guess.
        aggregator_customer_is_member=None,
        notes=_customer_note(header),
        # `created_at` is when the order was placed on the marketplace (the
        # OrderCreated event), not when GrubOps polling filed it here — so order
        # history and "created" sorts line up with the aggregator timeline.
        created_at=_placed_at(info) or utcnow(),
    )
    db.add(order)
    await db.flush()

    # Onto the branch's register as a waiting order, then ring the doorbell —
    # the same pair a published website order gets, so the iPad alarms and the
    # kitchen ticket prints in MMPOS styling. `notify_order_placed` sends to the
    # branch's devices only, so a branch with no live terminal (Barsha today)
    # simply records the order without alarming anyone.
    from app.services import push_service
    from app.services.pos import pos_order_service

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
        unit_price = base_price + options_price
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
                unit_price=money(unit_price),
                # The line total is the per-unit price (base plus its modifiers)
                # times quantity — the same figure the register and the website
                # write. It was taken from GrubOps' own `totalPrice` with a
                # fallback to `base_price`, and a product whose price lives
                # entirely on a modifier (base 0, the "3 Pieces" charge on the
                # brownie) has neither: GrubOps omitted `totalPrice` and the
                # fallback wrote 0, so the line read 0.00 on an order whose
                # subtotal — summed from the header, not these lines — was right.
                total_price=money(unit_price * quantity),
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
    db,
    order: Order,
    target: OrderStatusEnum,
    histories: list[dict],
    *,
    reason: str | None = None,
) -> None:
    """Move the order up the ladder to `target`, one honest rung at a time.

    Cancellation is not on the ladder — it can arrive from most states — so it
    is attempted directly; if the order is already past the point GrubOps can
    cancel from (packed, delivered), the map refuses and `skip` lets it stand.

    `reason` is GrubOps's cancellation reason, carried only on the CANCELLED path:
    it is stamped on the order for display and threaded through `acting_as` as the
    status-event note, so the timeline row records *why* as well as *when*.
    """
    if target == OrderStatusEnum.CANCELLED:
        at = _history_at(histories, target)
        # Set before the transition so the value is in place whether or not the
        # move lands — a GrubOps cancel of an order our board has already closed
        # (packed/delivered) is refused, but the reason it gives is still the
        # truth about that order and worth keeping.
        if reason:
            order.aggregator_cancel_reason = reason
        with acting_as(StatusSourceEnum.AGGREGATOR, at=at, note=reason):
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
        # A closed check has to carry the moment it closed. The counter stamps
        # this when the cashier takes payment; an aggregator order has no cashier
        # tapping "close", so without this it reached pos_status=closed with a
        # null closed_at — which is what left the hour/terminal/cashier reports
        # reading "Unknown" and what the constraint added alongside this now
        # forbids. Stamp once, when the delivery that closes the order arrives.
        if pos == PosOrderStatusEnum.CLOSED.value and order.closed_at is None:
            order.closed_at = utcnow()


# ── auto-close: a packed aggregator order closes itself after the window ──────


#: How many orders one auto-close sweep will close. Generous — a busy evening's
#: worth of aggregator orders all pass through `packed` and the window is short.
_AUTO_CLOSE_LIMIT = 200


async def sweep_auto_close(db) -> int:
    """Move packed aggregator orders to `delivered` once their window has passed.

    An aggregator order gives us no on-the-way or delivered signal — the dispatch
    calls the rider and then GrubTech goes quiet — so from our side the order is
    done a few minutes after it is packed. This closes it: a `packed → delivered`
    move attributed to `system`, which clears the check off the register board via
    `_sync_pos_status` and (unlike an ingest-driven move) mirrors out to *finalise*
    the Foodics order on the delivery axis, via `order_lifecycle`'s DELIVERED
    mirror-out. This is the 5-minute delay the write-back's "close" hangs off.

    The packed moment is read from `order_status_events` rather than a column, the
    way the rest of the stack derives its timestamps — there is no `packed_at`.
    Returns how many it closed. Bounded and idempotent: an order already delivered
    or cancelled by the ingest loop in the meantime is off the query.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.AGG_AUTO_CLOSE_SECONDS
    )
    # The latest moment each order was marked packed.
    packed_at = (
        select(
            OrderStatusEvent.order_id.label("order_id"),
            func.max(OrderStatusEvent.at).label("packed_at"),
        )
        .where(OrderStatusEvent.status == OrderStatusEnum.PACKED.value)
        .group_by(OrderStatusEvent.order_id)
        .subquery()
    )
    orders = list(
        (
            await db.execute(
                select(Order)
                .join(packed_at, packed_at.c.order_id == Order.id)
                .where(
                    Order.source == OrderSourceEnum.AGGREGATOR.value,
                    Order.status == OrderStatusEnum.PACKED,
                    packed_at.c.packed_at <= cutoff,
                )
                # The restock a cancellation would walk is not needed here, but a
                # delivered move touches no lines; `items` is loaded anyway so a
                # consequence that grows later cannot trip a `MissingGreenlet`.
                .options(selectinload(Order.items))
                .order_by(packed_at.c.packed_at)
                .limit(_AUTO_CLOSE_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    closed = 0
    for order in orders:
        with acting_as(
            StatusSourceEnum.SYSTEM.value,
            note="auto-closed: aggregator sends no further status after packed",
        ):
            moved = await order_lifecycle.transition(
                db, order, OrderStatusEnum.DELIVERED, on_invalid="skip"
            )
        if moved:
            _sync_pos_status(order, OrderStatusEnum.DELIVERED)
            closed += 1
    if closed:
        await db.flush()
    return closed


# ── re-poll: catch a GrubOps-side change after the order left the window ──────


#: The aggregator statuses that are still "open" from our side — the order is
#: with us or waiting, not yet closed. A GrubOps-side cancel or completion of one
#: of these is what the summary window can drop before the ordinary loop sees it.
#: `packed` is deliberately absent: `sweep_auto_close` already owns it, and
#: re-polling a packed order would race that sweep.
_OPEN_STATUSES: tuple[OrderStatusEnum, ...] = (
    OrderStatusEnum.CREATED,
    OrderStatusEnum.CONFIRMED,
    OrderStatusEnum.ARRIVED_AT_POS,
)

#: How many aged-out open orders one sweep will re-poll. Each costs a GrubOps
#: `getOrderInfo`, so this bounds that spend per tick; comfortably above the
#: handful ever open at once.
_OPEN_REPOLL_LIMIT = 50


async def sweep_open_orders(db, seen_ids: set[str]) -> int:
    """Re-poll open aggregator orders GrubOps has stopped showing us.

    `getOrderSummaryList` is a single most-recent window, so an order that lingers
    at `arrived_at_pos` — waiting for the shop to press Packed — eventually falls
    out of it. Once it does, the ordinary loop never re-checks it, and a later
    GrubOps-side cancellation or completion is missed: the order freezes on our
    board at the last status we happened to catch. This closes that gap. For each
    open aggregator order not already refreshed from this tick's summary, it
    fetches the full order and, when GrubOps's status has moved, reconciles it
    through `ingest` — the same path the loop uses, so a cancel carries its reason
    and a completion walks the ladder exactly as an in-window one would.

    `seen_ids` are the GrubOps order ids the summary already returned this tick;
    they are skipped so a fetch is spent only on the genuinely quiet orders.
    Bounded by `_OPEN_REPOLL_LIMIT`. Returns how many it advanced.
    """
    order_maps = list(
        (
            await db.execute(
                select(GrubOpsOrderMap)
                .join(Order, Order.id == GrubOpsOrderMap.mm_order_id)
                .where(
                    Order.source == OrderSourceEnum.AGGREGATOR.value,
                    Order.status.in_(_OPEN_STATUSES),
                )
                .order_by(Order.created_at)
                .limit(_OPEN_REPOLL_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    touched = 0
    for order_map in order_maps:
        if order_map.grubops_order_id in seen_ids:
            continue
        # Per order, so one unreachable order or malformed payload does not end
        # the pass — the same guarantee the summary loop gives.
        try:
            info = await provider.get_order(order_map.grubops_order_id)
            if info is None:
                continue
            header = info.get("orderHeader") or {}
            gr_status = header.get("orderStatus") or info.get("orderStatus")
            if gr_status == order_map.last_grubops_status:
                continue
            await ingest(db, info, order_map)
            touched += 1
        except Exception:  # noqa: BLE001 — one bad order must not stop the rest
            logger.exception(
                "GrubOps: failed to re-poll open order %s",
                order_map.grubops_order_id,
            )
            continue
    if touched:
        await db.flush()
    return touched
