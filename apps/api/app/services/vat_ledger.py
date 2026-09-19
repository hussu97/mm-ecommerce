"""Recompute the VAT ledger cache from the tables that actually hold the money.

The console needs one cheap read of a legal entity's VAT position — output VAT
collected on sales, input VAT paid on marketplace fees, payment processing,
courier charges and raw goods — split net / VAT / gross per category. Those
figures live across five source tables and are expensive to re-aggregate on every
page load, so this service maintains a derived cache (`vat_ledger_entries`).

It owns the arithmetic in ONE place. `compute_window` rebuilds a date range by
deleting that range's rows and re-inserting them from source — a true
recomputation, idempotent and safe to re-run — mirroring
`services/aggregators/reconcile`. `run_forever` is the lifespan loop: a one-time
full-history backfill when the cache is empty, then a trailing window every tick
so late-settling figures (a marketplace commission that lands days after the
order) are absorbed. No cron in this stack; leader-elected on an advisory lock so
a second copy across blue/green slots is harmless.

This is genuinely cross-cutting (orders + couriers + purchasing), so it lives at
the services root rather than in one domain subpackage (module-layout rule).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat
from app.core.config import settings
from app.core.money import money, to_decimal
from app.models.base import utcnow
from app.models.inventory import PurchaseOrder, PurchaseOrderStatusEnum
from app.models.legal_entity import LegalEntity
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.models.vat_ledger import VatCategoryEnum, VatDirectionEnum, VatLedgerEntry
from app.services.orders import tax_identity_service
from app.services.orders.order_pricing import VAT_RATE
from app.services.pos.pos_reports._base import _COMPLETED_SALE

logger = logging.getLogger(__name__)

#: Same flat 64-bit namespace as every other advisory lock. "mmVAT" + 1.
_ADVISORY_LOCK_KEY = 0x6D6D_5641_5400_0001

#: How often to rebuild the trailing window. Hourly is ample — the underlying
#: fees settle over days, not minutes.
_TICK_SECONDS = 3600


class _Grain:
    """Accumulator for one (date, entity, category, direction) cell."""

    __slots__ = ("net", "vat", "gross", "count", "recoverable")

    def __init__(self) -> None:
        self.net = to_decimal(0)
        self.vat = to_decimal(0)
        self.gross = to_decimal(0)
        self.count = 0
        self.recoverable = True


def _split_inclusive(gross: object) -> tuple[Decimal, Decimal]:
    """Split a VAT-inclusive figure into (net, vat) at the standard rate."""
    net = money(to_decimal(gross) / (to_decimal(1) + VAT_RATE))
    return net, money(to_decimal(gross) - net)


async def _entities(db: AsyncSession) -> tuple[dict[uuid.UUID, bool], uuid.UUID | None]:
    """Map of legal_entity_id → vat_registered, plus the default entity's id."""
    rows = (await db.execute(select(LegalEntity))).scalars().all()
    registered = {e.id: bool(e.vat_registered) for e in rows}
    default = next(
        (
            e.id
            for e in rows
            if e.reference == tax_identity_service.DEFAULT_ENTITY_REFERENCE
        ),
        None,
    )
    return registered, default


async def compute_window(db: AsyncSession, date_from: str, date_to: str) -> int:
    """Rebuild `vat_ledger_entries` for [date_from, date_to] inclusive.

    Deletes the window then re-inserts it from source — so a fee that has since
    become null, or an order that changed status, is reflected rather than left
    behind. Returns the number of grain rows written. The caller commits.
    """
    registered, default_entity = await _entities(db)
    if default_entity is None:
        # A database seeded before the legal entities exist — nothing to attribute
        # to. Leave the cache untouched rather than write orphan rows.
        logger.warning("vat_ledger: no default legal entity; skipping window")
        return 0

    grains: dict[tuple[str, uuid.UUID, str, str], _Grain] = {}

    def cell(
        bdate: str, entity_id: uuid.UUID | None, category: str, direction: str
    ) -> _Grain:
        eid = entity_id or default_entity
        key = (bdate, eid, category, direction)
        grain = grains.get(key)
        if grain is None:
            grain = _Grain()
            grains[key] = grain
        return grain

    in_window = and_(
        Order.business_date.is_not(None),
        Order.business_date >= date_from,
        Order.business_date <= date_to,
    )

    # --- Output: completed sales -------------------------------------------
    sales = await db.execute(
        select(
            Order.business_date,
            Order.legal_entity_id,
            func.coalesce(func.sum(Order.total_excl_vat), 0),
            func.coalesce(func.sum(Order.vat_amount), 0),
            func.coalesce(func.sum(Order.total), 0),
            func.count(Order.id),
        )
        .where(in_window, _COMPLETED_SALE)
        .group_by(Order.business_date, Order.legal_entity_id)
    )
    for bdate, entity_id, net, vat, gross, count in sales:
        g = cell(
            bdate,
            entity_id,
            VatCategoryEnum.SALES_OUTPUT.value,
            VatDirectionEnum.OUTPUT.value,
        )
        g.net += to_decimal(net)
        g.vat += to_decimal(vat)
        g.gross += to_decimal(gross)
        g.count += int(count)

    # --- Output reduction: refunds / credit notes (stored negative) --------
    # VAT in a refund is derived from the order's own frozen rate, so a refund on
    # a zero-VAT (non-registered) order carries no VAT reduction.
    refund_vat = func.sum(
        func.coalesce(Order.refunded_amount, 0) * Order.vat_rate / (1 + Order.vat_rate)
    )
    refunds = await db.execute(
        select(
            Order.business_date,
            Order.legal_entity_id,
            func.coalesce(func.sum(func.coalesce(Order.refunded_amount, 0)), 0),
            func.coalesce(refund_vat, 0),
            func.count(Order.id),
        )
        .where(in_window, func.coalesce(Order.refunded_amount, 0) > 0)
        .group_by(Order.business_date, Order.legal_entity_id)
    )
    for bdate, entity_id, gross, vat, count in refunds:
        g = cell(
            bdate,
            entity_id,
            VatCategoryEnum.SALES_REFUND.value,
            VatDirectionEnum.OUTPUT.value,
        )
        gross_d = money(gross)
        vat_d = money(vat)
        g.gross -= gross_d
        g.vat -= vat_d
        g.net -= money(gross_d - vat_d)
        g.count += int(count)

    # --- Input: order-level fees (stored VAT-inclusive) --------------------
    fee_columns = {
        VatCategoryEnum.AGGREGATOR_COMMISSION.value: Order.aggregator_fee,
        VatCategoryEnum.PAYMENT_PROCESSING.value: Order.payment_fee,
        VatCategoryEnum.MARKETPLACE_MARKETING.value: Order.marketing_fee,
        VatCategoryEnum.MARKETPLACE_CANCELLATION.value: Order.cancellation_fee,
    }
    for category, column in fee_columns.items():
        rows = await db.execute(
            select(
                Order.business_date,
                Order.legal_entity_id,
                func.coalesce(func.sum(column), 0),
                func.count(Order.id),
            )
            .where(in_window, column.is_not(None))
            .group_by(Order.business_date, Order.legal_entity_id)
        )
        for bdate, entity_id, gross, count in rows:
            g = cell(bdate, entity_id, category, VatDirectionEnum.INPUT.value)
            net, vat = _split_inclusive(gross)
            g.net += net
            g.vat += vat
            g.gross += money(gross)
            g.count += int(count)

    # --- Input: courier cost billed to the shop (VAT-inclusive @ 5%) -------
    couriers = await db.execute(
        select(
            Order.business_date,
            Order.legal_entity_id,
            func.coalesce(func.sum(OrderDelivery.cost_total), 0),
            func.count(OrderDelivery.id),
        )
        .select_from(OrderDelivery)
        .join(Order, Order.id == OrderDelivery.order_id)
        .where(in_window, OrderDelivery.cost_total.is_not(None))
        .group_by(Order.business_date, Order.legal_entity_id)
    )
    for bdate, entity_id, gross, count in couriers:
        g = cell(
            bdate,
            entity_id,
            VatCategoryEnum.COURIER_FEES.value,
            VatDirectionEnum.INPUT.value,
        )
        net, vat = _split_inclusive(gross)
        g.net += net
        g.vat += vat
        g.gross += money(gross)
        g.count += int(count)

    # --- Input: raw goods from received purchase orders --------------------
    # POs carry only a branch; the legal entity is resolved from the branch's
    # counter tax config (Barsha → Najm → non-recoverable), falling back to
    # Fatema. `vat_total` is already zero for a non-deductible supplier. A
    # partially-received PO carries its full ordered VAT here — a known v1 timing
    # nuance; most POS-origin POs auto-receive and close same day.
    pos = await db.execute(
        select(
            PurchaseOrder.business_date,
            PurchaseOrder.branch_id,
            func.coalesce(func.sum(PurchaseOrder.subtotal_net), 0),
            func.coalesce(func.sum(PurchaseOrder.vat_total), 0),
            func.coalesce(func.sum(PurchaseOrder.total_gross), 0),
            func.count(PurchaseOrder.id),
        )
        .where(
            PurchaseOrder.business_date >= date_from,
            PurchaseOrder.business_date <= date_to,
            PurchaseOrder.status.in_(
                [
                    PurchaseOrderStatusEnum.PARTIALLY_RECEIVED.value,
                    PurchaseOrderStatusEnum.CLOSED.value,
                ]
            ),
        )
        .group_by(PurchaseOrder.business_date, PurchaseOrder.branch_id)
    )
    for bdate, branch_id, net, vat, gross, count in pos:
        entity = await tax_identity_service.resolve(
            db, branch_id=branch_id, source="cashier"
        )
        entity_id = entity.id if entity is not None else default_entity
        g = cell(
            bdate,
            entity_id,
            VatCategoryEnum.RAW_GOODS.value,
            VatDirectionEnum.INPUT.value,
        )
        g.net += to_decimal(net)
        g.vat += to_decimal(vat)
        g.gross += to_decimal(gross)
        g.count += int(count)

    # --- Apply the non-registered gate to input rows -----------------------
    # An unregistered entity reclaims nothing: keep the cost visible but zero the
    # VAT and flag it non-recoverable. Output rows already carry zero VAT.
    now = utcnow()
    for (bdate, entity_id, category, direction), g in grains.items():
        if direction == VatDirectionEnum.INPUT.value and not registered.get(
            entity_id, True
        ):
            g.vat = to_decimal(0)
            g.recoverable = False

    # --- Rewrite the window ------------------------------------------------
    await db.execute(
        delete(VatLedgerEntry).where(
            VatLedgerEntry.business_date >= date_from,
            VatLedgerEntry.business_date <= date_to,
        )
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "business_date": bdate,
            "legal_entity_id": entity_id,
            "category": category,
            "direction": direction,
            "net_value": money(g.net),
            "vat_amount": money(g.vat),
            "gross_value": money(g.gross),
            "vat_recoverable": g.recoverable,
            "source_count": g.count,
            "recomputed_at": now,
        }
        for (bdate, entity_id, category, direction), g in grains.items()
    ]
    if rows:
        await db.execute(pg_insert(VatLedgerEntry).values(rows))
    return len(rows)


async def read_ledger(
    db: AsyncSession,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    legal_entity_id: uuid.UUID | None = None,
) -> list[dict]:
    """Read the cache for a window, one aggregated row per (entity, category).

    Sums the per-day grains into per-(entity, category, direction) totals — the
    console reads a window, not a single day. Pure read; never recomputes.
    """
    stmt = (
        select(
            VatLedgerEntry.legal_entity_id,
            LegalEntity.legal_name,
            LegalEntity.vat_registered,
            VatLedgerEntry.category,
            VatLedgerEntry.direction,
            func.coalesce(func.sum(VatLedgerEntry.net_value), 0),
            func.coalesce(func.sum(VatLedgerEntry.vat_amount), 0),
            func.coalesce(func.sum(VatLedgerEntry.gross_value), 0),
            func.bool_and(VatLedgerEntry.vat_recoverable),
            func.coalesce(func.sum(VatLedgerEntry.source_count), 0),
        )
        .join(LegalEntity, LegalEntity.id == VatLedgerEntry.legal_entity_id)
        .group_by(
            VatLedgerEntry.legal_entity_id,
            LegalEntity.legal_name,
            LegalEntity.vat_registered,
            VatLedgerEntry.category,
            VatLedgerEntry.direction,
        )
        .order_by(
            LegalEntity.legal_name, VatLedgerEntry.direction, VatLedgerEntry.category
        )
    )
    if date_from:
        stmt = stmt.where(VatLedgerEntry.business_date >= date_from)
    if date_to:
        stmt = stmt.where(VatLedgerEntry.business_date <= date_to)
    if legal_entity_id is not None:
        stmt = stmt.where(VatLedgerEntry.legal_entity_id == legal_entity_id)

    rows = []
    for (
        entity_id,
        entity_name,
        vat_registered,
        category,
        direction,
        net,
        vat,
        gross,
        recoverable,
        count,
    ) in await db.execute(stmt):
        rows.append(
            {
                "legal_entity_id": entity_id,
                "legal_entity_name": entity_name,
                "vat_registered": bool(vat_registered),
                "category": category,
                "direction": direction,
                "net_value": money(net),
                "vat_amount": money(vat),
                "gross_value": money(gross),
                "vat_recoverable": bool(recoverable),
                "source_count": int(count),
            }
        )
    return rows


async def _source_date_bounds(db: AsyncSession) -> tuple[str | None, str | None]:
    """The earliest and latest business_date across every source table."""
    lows: list[str] = []
    highs: list[str] = []
    order_lo, order_hi = (
        await db.execute(
            select(func.min(Order.business_date), func.max(Order.business_date))
        )
    ).one()
    po_lo, po_hi = (
        await db.execute(
            select(
                func.min(PurchaseOrder.business_date),
                func.max(PurchaseOrder.business_date),
            )
        )
    ).one()
    for v in (order_lo, po_lo):
        if v:
            lows.append(v)
    for v in (order_hi, po_hi):
        if v:
            highs.append(v)
    return (min(lows) if lows else None, max(highs) if highs else None)


def _month_windows(date_from: str, date_to: str) -> list[tuple[str, str]]:
    """Split [from, to] into calendar-month [start, end] string pairs."""
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    windows: list[tuple[str, str]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        if cursor.month == 12:
            nxt = cursor.replace(year=cursor.year + 1, month=1)
        else:
            nxt = cursor.replace(month=cursor.month + 1)
        w_start = max(cursor, start)
        w_end = min(nxt - timedelta(days=1), end)
        windows.append((w_start.isoformat(), w_end.isoformat()))
        cursor = nxt
    return windows


async def backfill_all(db: AsyncSession) -> int:
    """Recompute the entire history, month by month. Idempotent. Commits per month."""
    lo, hi = await _source_date_bounds(db)
    if not lo or not hi:
        return 0
    total = 0
    for w_start, w_end in _month_windows(lo, hi):
        total += await compute_window(db, w_start, w_end)
        await db.commit()
    logger.info("vat_ledger: backfilled %s grain rows over %s..%s", total, lo, hi)
    return total


async def _is_empty(db: AsyncSession) -> bool:
    return (await db.scalar(select(func.count(VatLedgerEntry.id)))) == 0


async def run_forever() -> None:
    """Keep the VAT ledger cache fresh — full backfill once, then trailing window.

    Same shape as the other lifespan loops: leader-elected on an advisory lock,
    beating its heartbeat, one worker inside the sweep at a time.
    """
    window_days = settings.VAT_LEDGER_WINDOW_DAYS
    logger.info(
        "VAT ledger refresh started (every %ss, %s-day window)",
        _TICK_SECONDS,
        window_days,
    )
    # Boot catch-up: fill the trailing window immediately, and the whole history
    # once if the cache has never been built. Runs before the first sleep so a
    # fresh deploy has data without waiting an hour.
    try:
        async with advisory_lock.held_session(
            _ADVISORY_LOCK_KEY, name="vat ledger backfill"
        ) as db:
            if db is not None:
                await heartbeat.beat("vat_ledger_refresh")
                if await _is_empty(db):
                    await backfill_all(db)
                today = date.today()
                await compute_window(
                    db,
                    (today - timedelta(days=window_days)).isoformat(),
                    today.isoformat(),
                )
                await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a bad boot pass must not kill the loop
        logger.exception("VAT ledger boot backfill failed")

    while True:
        try:
            await asyncio.sleep(_TICK_SECONDS)
            await heartbeat.beat("vat_ledger_refresh")
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="vat ledger refresh"
            ) as db:
                if db is None:
                    continue
                today = date.today()
                written = await compute_window(
                    db,
                    (today - timedelta(days=window_days)).isoformat(),
                    today.isoformat(),
                )
                await db.commit()
                logger.debug("VAT ledger refresh wrote %s grain rows", written)
        except asyncio.CancelledError:
            logger.info("VAT ledger refresh stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("VAT ledger refresh tick failed")
