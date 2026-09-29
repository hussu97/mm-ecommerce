"""Marketplace returns: a cancelled order the rider is bringing back.

Talabat cancels some orders after its rider has collected them (customer not
found, wrong address, too late), and the rider brings the box back. Handing it
over takes a **return PIN** that only the Talabat partner portal shows — GrubOps
never carries it. This module gets that PIN onto the order
(`models.marketplace_return`) along two paths, and records the box coming back.

**Trigger (Sharjah/Barsha, GrubOps-owned).** When the GrubOps ingest cancels a
Talabat order that had already left the kitchen (`open_on_cancellation`), a
`pin_pending` row is written in the same transaction and a one-off tracked task
reads the PIN from the portal a few seconds later, retrying on a short backoff
because the PIN is minted a beat after the cancellation. No polling loop: the
task holds no connection while it waits, and touches the DB only to write what it
read.

**Scrape (every branch, and the backstop).** The hourly sales refresh already
exports every order. A row that went out for delivery, was cancelled and never
delivered gets its PIN read there (`talabat_provider._attach_return_details`),
and promotion files it here (`record_from_scrape`). So a trigger that failed — a
dead session, a restart mid-retry — is filled in within the hour.

**What counts as a return.** Talabat mints a PIN on *every* cancellation, even
one cancelled before any rider arrived, and its portal badges all of them
RETURNED. The rider's timeline decides instead: picked up, never delivered,
cancelled (`parse_return_details`). Whoever cancelled — customer or rider — the
box is coming back, so the owner is recorded but does not filter.

**Received back** (`mark_received`) is a register action. It stamps who took the
box in and, when the order's recipe consumption had been posted, restocks it in
full through the same inventory return the admin console uses — resolving the
"choose restock / waste" exception the cancellation logged. An order whose
cancellation somebody already disposed of in the console is left as they chose.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import background
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.models.aggregator import AggregatorOrder
from app.models.base import utcnow
from app.models.marketplace_return import (
    MarketplaceReturn,
    MarketplaceReturnPinSourceEnum,
    MarketplaceReturnStatusEnum,
)
from app.models.order import Order, OrderStatusEnum
from app.models.user import User

logger = logging.getLogger(__name__)

__all__ = [
    "RETURNABLE_FROM",
    "mark_received",
    "open_on_cancellation",
    "record_from_scrape",
]

#: Channels whose cancellations can come back with a PIN. Talabat only today —
#: the other marketplaces do not run a return-to-vendor flow MM has seen.
RETURN_CHANNELS = frozenset({"talabat"})

#: The statuses an order is cancelled *from* for it to have left the kitchen.
#: `packed` too: GrubOps's "left the kitchen" can lag the rider's pickup, and the
#: portal read decides (`not_returning` when no rider ever collected it).
RETURNABLE_FROM = (OrderStatusEnum.PACKED, OrderStatusEnum.OUT_FOR_DELIVERY)

#: Seconds before each PIN read the trigger task makes. The first waits for the
#: ingest transaction to commit and the portal to mint the PIN; the tail covers a
#: slow portal. After the last, the hourly scrape takes over.
_TRIGGER_DELAYS = (10, 30, 90, 300, 900)

_FINAL = frozenset(
    {
        MarketplaceReturnStatusEnum.RECEIVED.value,
        MarketplaceReturnStatusEnum.NOT_RETURNING.value,
    }
)


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clip(value: Any, width: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:width] or None


async def _row_for(db: AsyncSession, order_id: uuid.UUID) -> MarketplaceReturn | None:
    return (
        await db.execute(
            select(MarketplaceReturn).where(MarketplaceReturn.order_id == order_id)
        )
    ).scalar_one_or_none()


def _apply_details(
    row: MarketplaceReturn, details: dict[str, Any], *, source: str
) -> bool:
    """Fold a portal read into the row. Returns whether the push is now owed.

    Never regresses: a `received` row keeps its state, and a PIN once known is
    not blanked by a later read that lacks it.
    """
    pin = _clip(details.get("pin"), 16)
    was_awaiting_with_pin = (
        row.status == MarketplaceReturnStatusEnum.AWAITING_RETURN.value
        and row.return_pin is not None
    )
    if pin and pin != row.return_pin:
        row.return_pin = pin
        row.pin_source = source
        row.pin_fetched_at = utcnow()
    row.cancel_owner = _clip(details.get("cancel_owner"), 40) or row.cancel_owner
    row.cancel_reason = _clip(details.get("cancel_reason"), 120) or row.cancel_reason
    row.cancelled_at = _parse_ts(details.get("cancelled_at")) or row.cancelled_at
    if row.status == MarketplaceReturnStatusEnum.RECEIVED.value:
        return False
    if details.get("returning") is False and details.get("cancelled"):
        row.status = MarketplaceReturnStatusEnum.NOT_RETURNING.value
        return False
    if details.get("returning") and row.return_pin:
        row.status = MarketplaceReturnStatusEnum.AWAITING_RETURN.value
        return not was_awaiting_with_pin
    return False


# ── trigger: the GrubOps cancellation ─────────────────────────────────────────


async def open_on_cancellation(
    db: AsyncSession, order: Order, *, previous: OrderStatusEnum | None
) -> MarketplaceReturn | None:
    """A marketplace just cancelled `order` after it left the kitchen: expect it back.

    Called by the GrubOps ingest right after a cancellation it applied. Writes a
    `pin_pending` row (idempotent — a second call finds it) and schedules the
    portal read. A no-op for any other channel, or a cancellation from before
    packing (nothing left the building).
    """
    if not settings.TALABAT_RETURN_PIN_ENABLED:
        return None
    # `getattr`: test doubles of the ingest carry only the fields they exercise.
    if previous not in RETURNABLE_FROM:
        return None
    if getattr(order, "aggregator_channel", None) not in RETURN_CHANNELS:
        return None
    if not getattr(order, "external_reference", None):
        return None
    row = await _row_for(db, order.id)
    if row is None:
        row = MarketplaceReturn(
            order_id=order.id,
            channel=order.aggregator_channel,
            external_order_id=str(order.external_reference),
            status=MarketplaceReturnStatusEnum.PIN_PENDING.value,
            cancelled_at=utcnow(),
        )
        db.add(row)
        await db.flush()
    if row.return_pin is None and row.status not in _FINAL:
        _schedule_pin_read(order.id)
    return row


def _schedule_pin_read(order_id: uuid.UUID) -> None:
    # Tracked fire-and-forget (canon rule 5): held until done, reported if it
    # dies. Lost on a restart mid-retry — the hourly scrape is the backstop.
    background.spawn_tracked(
        _read_pin_with_retries(order_id), name=f"return-pin-{order_id}"
    )


async def _read_pin_with_retries(order_id: uuid.UUID) -> None:
    for delay in _TRIGGER_DELAYS:
        await asyncio.sleep(delay)
        try:
            done = await read_pin_once(order_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed read retries, then the scrape
            logger.warning("return PIN read failed for %s", order_id, exc_info=True)
            continue
        if done:
            return
    logger.info(
        "return PIN for %s not read after %s tries; the hourly scrape will",
        order_id,
        len(_TRIGGER_DELAYS),
    )


async def read_pin_once(order_id: uuid.UUID) -> bool:
    """One portal read for a pending return. True when there is nothing more to do.

    Three short DB touches around one HTTP call, never a connection held across
    the network: resolve the order + store + session, read the portal, write.
    """
    from app.core.database import SchedulerSessionFactory
    from app.services.aggregators import session_store
    from app.services.aggregators.menu_readers import _talabat_vendor
    from app.services.providers import talabat_provider as tp

    async with SchedulerSessionFactory() as db:
        row = await _row_for(db, order_id)
        if row is None:
            # The ingest's transaction has not committed yet (or rolled back).
            return False
        if row.return_pin is not None or row.status in _FINAL:
            return True
        order = await db.get(Order, order_id)
        if order is None or order.branch_id is None:
            return True
        external_id = row.external_order_id
        vendor = await _talabat_vendor(db, order.branch_id)
        session = await session_store.load(db, "talabat")
        session = await tp.provider.prepare_session(db, session)
    if session is None:
        return False

    details = await tp.provider.fetch_return_details(
        session, order_id=external_id, vendor_id=vendor
    )
    if details is None:
        return False

    async with SchedulerSessionFactory() as db:
        row = await _row_for(db, order_id)
        if row is None:
            return False
        push_owed = _apply_details(
            row, details, source=MarketplaceReturnPinSourceEnum.TRIGGER.value
        )
        order = await db.get(Order, order_id)
        if push_owed and order is not None:
            await _notify(db, order, row)
        await db.commit()
        return row.return_pin is not None or row.status in _FINAL


async def _notify(db: AsyncSession, order: Order, row: MarketplaceReturn) -> None:
    """Tell the branch's registers now, so the docket prints without the poll."""
    from app.services import push_service

    try:
        await push_service.notify_return_expected(db, order, pin=row.return_pin)
    except Exception:  # noqa: BLE001 — the 45s poll still finds it
        logger.warning("return push failed for %s", order.order_number, exc_info=True)


# ── scrape: promotion of the hourly export ────────────────────────────────────


def _details_from_csv(raw: dict) -> dict[str, Any] | None:
    """The return facts the CSV row alone carries, when the portal read is absent."""
    from app.services.providers.talabat_provider import is_return_candidate_row

    if not is_return_candidate_row(raw):
        return None
    return {
        "pin": None,
        "cancelled": True,
        # The CSV says it went out and never arrived; the portal read confirms
        # (and supplies the PIN) on a later sweep.
        "returning": None,
        "cancel_owner": raw.get("Cancellation owner"),
        "cancel_reason": raw.get("Cancellation reason"),
        "cancelled_at": None,
    }


async def record_from_scrape(
    db: AsyncSession, order: Order, agg: AggregatorOrder
) -> MarketplaceReturn | None:
    """File a scraped cancellation that reads as a return onto its MM order.

    Called from promotion for every Talabat order it drives to `cancelled`. Uses
    the portal read the sales sweep attached (`raw["_mm_return"]`) when there is
    one, else the CSV's own out-for-delivery / never-delivered signal (PIN
    pending). A row the portal says is not a return is only ever *updated* here,
    never created — that keeps the table to real returns plus the trigger's
    checked-and-dismissed ones.
    """
    if not settings.TALABAT_RETURN_PIN_ENABLED:
        return None
    if agg.channel not in RETURN_CHANNELS:
        return None
    raw = agg.raw if isinstance(agg.raw, dict) else {}
    details = raw.get("_mm_return")
    if not isinstance(details, dict):
        details = _details_from_csv(raw)
    if details is None:
        return None
    row = await _row_for(db, order.id)
    if row is None:
        if details.get("returning") is False:
            return None
        row = MarketplaceReturn(
            order_id=order.id,
            channel=agg.channel,
            external_order_id=str(agg.external_order_id),
            status=MarketplaceReturnStatusEnum.PIN_PENDING.value,
        )
        db.add(row)
    if row.cancelled_at is None and agg.cancelled_at is not None:
        row.cancelled_at = agg.cancelled_at
    push_owed = _apply_details(
        row, details, source=MarketplaceReturnPinSourceEnum.SCRAPE.value
    )
    await db.flush()
    if push_owed:
        await _notify(db, order, row)
    return row


# ── received back ─────────────────────────────────────────────────────────────


async def mark_received(
    db: AsyncSession, order: Order, *, user: User
) -> MarketplaceReturn:
    """The rider handed the box back: stamp it, and restock what it had consumed.

    Idempotent — a second tap returns the row unchanged. The restock is the full
    `restock` disposition of `source_event_service.record_return`, the same one a
    counter void uses, and it resolves the post-packing cancellation's "choose a
    disposition" exception. Skipped when nothing was consumed, when a return was
    already posted, or when somebody already chose waste / no effect for it.
    """
    row = await _row_for(db, order.id)
    if row is None or row.status == MarketplaceReturnStatusEnum.NOT_RETURNING.value:
        raise ConflictError("This order is not expected back")
    if row.status == MarketplaceReturnStatusEnum.RECEIVED.value:
        return row
    row.status = MarketplaceReturnStatusEnum.RECEIVED.value
    row.received_at = utcnow()
    row.received_by_id = user.id
    row.received_by_label = _clip(user.display_name or user.email, 255)
    row.restock_transaction_id = await _restock(db, order, row, user=user)
    await db.flush()
    return row


async def _restock(
    db: AsyncSession, order: Order, row: MarketplaceReturn, *, user: User
) -> uuid.UUID | None:
    from app.models.inventory import (
        InventoryTransaction,
        InventoryTransactionTypeEnum,
        TransactionStatusEnum,
    )
    from app.models.inventory_v2 import (
        InventorySourceEvent,
        InventorySourceEventStatusEnum,
    )
    from app.services.inventory import source_event_service

    already_returned = await db.scalar(
        select(InventoryTransaction.id).where(
            InventoryTransaction.order_id == order.id,
            InventoryTransaction.type
            == InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value,
            InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
        )
    )
    if already_returned is not None:
        return None
    # A cancellation disposed of "no inventory effect" in the console posts no
    # movement but resolves its exception — respect that choice too.
    disposition = await db.scalar(
        select(InventorySourceEvent.status).where(
            InventorySourceEvent.idempotency_key == f"order-cancel:{order.id}:1"
        )
    )
    if disposition == InventorySourceEventStatusEnum.POSTED.value:
        return None
    moved = await source_event_service.record_return(
        db,
        order=order,
        user=user,
        disposition="restock",
        proportion=Decimal("1"),
        idempotency_key=f"marketplace-return:{order.id}",
        notes=(
            f"Returned by the {row.channel} rider"
            + (f" (PIN {row.return_pin})" if row.return_pin else "")
        ),
    )
    return moved[0].id if moved else None
