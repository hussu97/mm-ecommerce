"""The loops that mirror each marketplace's ledger into the aggregator_* tables.

Two cadences, because the data publishes on two clocks (see the plan): an hourly
**sales** sweep over a rolling window — orders mutate after creation, so it
re-pulls and upserts rather than only taking new ones — and a daily **finance**
sweep for statements and payouts, which publish weekly. Each is shaped like the
GrubOps loops: `run_*_forever`/`sweep_*_once`, its own Postgres advisory lock so a
second worker no-ops instead of double-writing, gated on
`AGGREGATOR_INGEST_ENABLED`, started only where the storefront runs its loops.

Every write is an idempotent upsert on the channel-scoped natural key, so a
re-run over an overlapping window is free. A dead session (`AggregatorAuthError`)
is flipped to `needs_bootstrap` and skipped — never retried in a loop, which is
how an account gets locked; a transient fault is logged and left for next tick.
Each channel's sweep is wrapped in an `aggregator_sync_run` row so a failure is
recorded rather than silent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.aggregator import (
    CHANNEL_KEETA,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_MODE_FINANCE,
    RUN_MODE_SALES,
    RUN_RUNNING,
    SESSION_LIVE,
    AggregatorBranchMap,
    AggregatorOrder,
    AggregatorOrderItem,
    AggregatorPayout,
    AggregatorStatement,
    AggregatorStatementLine,
    AggregatorSyncRun,
)
from app.models.base import utcnow
from app.services.aggregators import crypto, reconcile, session_store
from app.services.aggregators.normalized import (
    FinanceResult,
    SalesResult,
    StandardOrder,
    StandardStatement,
)
from app.services.providers.aggregator_base import (
    AggregatorAuthError,
    AggregatorUnavailableError,
    BaseAggregatorClient,
)

logger = logging.getLogger(__name__)

__all__ = [
    "run_sales_forever",
    "run_finance_forever",
    "sweep_sales_once",
    "sweep_finance_once",
    "PROVIDERS",
]

#: "mmBATCH" + 5/6, after the GrubOps order loop (…4804). Each loop needs a key
#: nobody else holds so two copies serialise instead of racing.
_SALES_LOCK_KEY = 0x6D6D_4241_5443_4805
_FINANCE_LOCK_KEY = 0x6D6D_4241_5443_4806

#: The channels with a working httpx provider. Extended as each lands; a channel
#: absent here is simply never swept, no special case.
PROVIDERS: dict[str, BaseAggregatorClient] = {}


def _register_providers() -> None:
    """Import providers lazily so a half-built one cannot break app import.

    The four httpx channels. Keeta is deliberately absent: its every request
    needs an in-page `mtgsig` signature that cannot be replayed over httpx, so
    it is ingested by the bootstrap worker in-page, not by this loop.
    """
    if PROVIDERS:
        return
    from app.services.providers.careem_provider import provider as careem
    from app.services.providers.deliveroo_provider import provider as deliveroo
    from app.services.providers.noon_provider import provider as noon
    from app.services.providers.talabat_provider import provider as talabat

    for p in (careem, deliveroo, talabat, noon):
        PROVIDERS[p.channel] = p


def is_enabled() -> bool:
    return settings.AGGREGATOR_INGEST_ENABLED and crypto.is_configured()


# ── branch resolution ────────────────────────────────────────────────────────
async def _branch_for(
    db: AsyncSession, channel: str, external_outlet_id: str | None
) -> uuid.UUID | None:
    if not external_outlet_id:
        return None
    return await db.scalar(
        select(AggregatorBranchMap.branch_id).where(
            AggregatorBranchMap.channel == channel,
            AggregatorBranchMap.external_outlet_id == external_outlet_id,
        )
    )


# ── upserts ──────────────────────────────────────────────────────────────────
#: Columns a thinner re-fetch may legitimately omit: the hourly *sales* pull has
#: no settlement figures and the daily *finance* pull is what fills them, and a
#: branch may fail to re-resolve on a later pass. These are COALESCEd on conflict
#: so a later NULL never erases a value a richer pull already stored — a non-NULL
#: (including a zero, which means "charged nothing") still updates. This is what
#: the old comment here promised but the unconditional overwrite did not deliver.
_PRESERVE_IF_NULL = (
    "branch_id",
    "gross_sales",
    "net_sales",
    "commission_amount",
    "payment_fee",
    "delivery_fee",
    "vat_amount",
    "cancellation_fee",
    "refund_amount",
    "net_payable",
    "statement_id",
)


async def upsert_order(db: AsyncSession, channel: str, order: StandardOrder) -> None:
    branch_id = await _branch_for(db, channel, order.external_outlet_id)
    values = {
        "channel": channel,
        "external_order_id": order.external_order_id,
        "branch_id": branch_id,
        "business_date": order.business_date,
        "placed_at": order.placed_at,
        "status": order.status,
        "currency": order.currency,
        "gross_sales": order.gross_sales,
        "net_sales": order.net_sales,
        "commission_amount": order.commission_amount,
        "payment_fee": order.payment_fee,
        "delivery_fee": order.delivery_fee,
        "vat_amount": order.vat_amount,
        "cancellation_fee": order.cancellation_fee,
        "refund_amount": order.refund_amount,
        "net_payable": order.net_payable,
        "statement_id": order.statement_id,
        "raw": order.raw,
    }
    insert_stmt = pg_insert(AggregatorOrder).values(**values)
    update = {}
    for k in values:
        if k in ("channel", "external_order_id"):
            continue
        proposed = getattr(insert_stmt.excluded, k)
        update[k] = (
            func.coalesce(proposed, getattr(AggregatorOrder, k))
            if k in _PRESERVE_IF_NULL
            else proposed
        )
    update["updated_at"] = utcnow()
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_aggregator_order",
        set_=update,
    ).returning(AggregatorOrder.id)
    order_pk = (await db.execute(stmt)).scalar_one()

    for item in order.items:
        item_values = {
            "channel": channel,
            "source_key": item.source_key,
            "aggregator_order_id": order_pk,
            "grain": item.grain,
            "item_name": item.item_name,
            "category_name": item.category_name,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "gross_sales": item.gross_sales,
            "net_sales": item.net_sales,
            "amount_is_known": item.amount_is_known,
            "modifiers_text": item.modifiers_text,
            "business_date": item.business_date,
            "period_start": item.period_start,
            "period_end": item.period_end,
        }
        item_update = {
            k: v for k, v in item_values.items() if k not in ("channel", "source_key")
        }
        item_update["updated_at"] = utcnow()
        await db.execute(
            pg_insert(AggregatorOrderItem)
            .values(**item_values)
            .on_conflict_do_update(
                constraint="uq_aggregator_order_item", set_=item_update
            )
        )


async def ingest_keeta_payloads(db: AsyncSession, payloads: list[dict]) -> int:
    """Parse and upsert a batch of in-page-fetched Keeta `getOrders` payloads.

    Keeta is off the httpx sweep (its `mtgsig` signing lives in the page), so the
    bootstrap worker fetches each response in-page and pushes the raw payloads to
    the `/keeta/orders` endpoint, which calls this. Each payload is isolated: a
    single malformed one is logged and skipped, not fatal to the batch. Returns
    the number of orders written.
    """
    from app.services.providers import keeta_provider

    ingested = 0
    for payload in payloads:
        try:
            orders = keeta_provider.provider.parse_orders(payload)
        except Exception:  # noqa: BLE001 — one bad payload must not fail the batch
            logger.exception("keeta payload parse failed — skipped")
            continue
        for order in orders:
            await upsert_order(db, CHANNEL_KEETA, order)
            ingested += 1
    return ingested


async def _upsert_statement(
    db: AsyncSession, channel: str, statement: StandardStatement
) -> None:
    values = {
        "channel": channel,
        "statement_id": statement.statement_id,
        "period_start": statement.period_start,
        "period_end": statement.period_end,
        "payment_due_date": statement.payment_due_date,
        "gross_sales": statement.gross_sales,
        "net_payable": statement.net_payable,
        "total_fees": statement.total_fees,
        "total_vat": statement.total_vat,
        "currency": statement.currency,
        "raw": statement.raw,
    }
    update = {k: v for k, v in values.items() if k not in ("channel", "statement_id")}
    update["updated_at"] = utcnow()
    await db.execute(
        pg_insert(AggregatorStatement)
        .values(**values)
        .on_conflict_do_update(constraint="uq_aggregator_statement", set_=update)
    )
    for line in statement.lines:
        line_values = {
            "channel": channel,
            "source_key": line.source_key,
            "statement_id": line.statement_id or statement.statement_id,
            "transfer_id": line.transfer_id,
            "external_order_id": line.external_order_id,
            "line_date": line.line_date,
            "line_type": line.line_type,
            "fee_category": line.fee_category,
            "description": line.description,
            "amount": line.amount,
            "currency": line.currency,
        }
        line_update = {
            k: v for k, v in line_values.items() if k not in ("channel", "source_key")
        }
        line_update["updated_at"] = utcnow()
        await db.execute(
            pg_insert(AggregatorStatementLine)
            .values(**line_values)
            .on_conflict_do_update(
                constraint="uq_aggregator_statement_line", set_=line_update
            )
        )


async def _upsert_payout(db: AsyncSession, channel: str, payout) -> None:
    values = {
        "channel": channel,
        "transfer_id": payout.transfer_id,
        "statement_id": payout.statement_id,
        "transfer_date": payout.transfer_date,
        "payment_due_date": payout.payment_due_date,
        "transfer_amount": payout.transfer_amount,
        "transfer_status": payout.transfer_status,
        "payment_reference": payout.payment_reference,
        "currency": payout.currency,
    }
    update = {k: v for k, v in values.items() if k not in ("channel", "transfer_id")}
    update["updated_at"] = utcnow()
    await db.execute(
        pg_insert(AggregatorPayout)
        .values(**values)
        .on_conflict_do_update(constraint="uq_aggregator_payout", set_=update)
    )


# ── per-channel sweeps ───────────────────────────────────────────────────────
async def _new_run(db: AsyncSession, channel: str, mode: str) -> AggregatorSyncRun:
    run = AggregatorSyncRun(
        channel=channel, mode=mode, status=RUN_RUNNING, started_at=utcnow()
    )
    db.add(run)
    await db.flush()
    return run


#: How far back the daily finance sweep re-pulls statements/payouts. Statements
#: publish weekly/biweekly and post days after the order, so a week-plus window
#: catches a settlement that lands between two runs; the writes are idempotent so
#: the overlap is free. Named here (not a bare literal) alongside the other
#: cadence knobs in `settings` — kept a module constant rather than an env var
#: because it is a correctness floor, not an operational dial.
_FINANCE_LOOKBACK_DAYS = 8


async def _sweep_channel(
    db: AsyncSession, channel: str, provider: BaseAggregatorClient, mode: str
) -> int:
    """One channel's sweep for one mode. Returns records written; 0 on skip."""
    session = await session_store.load(db, channel)
    if session is None or session.status != SESSION_LIVE:
        return 0

    run = await _new_run(db, channel, mode)
    now = utcnow()
    written = 0
    reconciled = 0
    truncation: str | None = None
    try:
        if mode == RUN_MODE_SALES:
            since = now - timedelta(hours=settings.AGGREGATOR_SALES_WINDOW_HOURS)
            result: SalesResult = await provider.fetch_sales(
                session, since=since, until=now
            )
            for order in result.orders:
                await upsert_order(db, channel, order)
            written = len(result.orders)
            truncation = result.truncation_note
        else:
            since = now - timedelta(days=_FINANCE_LOOKBACK_DAYS)
            finance: FinanceResult = await provider.fetch_finance(
                session, since=since, until=now
            )
            for statement in finance.statements:
                await _upsert_statement(db, channel, statement)
            for payout in finance.payouts:
                await _upsert_payout(db, channel, payout)
            written = len(finance.statements) + len(finance.payouts)
            truncation = finance.truncation_note
            # Reconcile after the finance write, so Layer B compares against the
            # freshest statement commission. Reconciles all stored orders for the
            # channel (idempotent), not just this window's.
            reconciled = await reconcile.reconcile_channel(db, channel, run_id=run.id)
    except AggregatorAuthError as exc:
        run.status = RUN_FAILED
        run.error = str(exc)[:2000]
        run.finished_at = utcnow()
        await session_store.mark_needs_bootstrap(db, channel, error=str(exc))
        logger.warning("aggregator %s %s: session dead — %s", channel, mode, exc)
        return 0
    except AggregatorUnavailableError as exc:
        run.status = RUN_FAILED
        run.error = str(exc)[:2000]
        run.finished_at = utcnow()
        logger.warning("aggregator %s %s unavailable: %s", channel, mode, exc)
        return 0

    run.status = RUN_COMPLETED
    run.finished_at = utcnow()
    run.stats = {"written": written, "from": since.isoformat(), "to": now.isoformat()}
    if reconciled:
        run.stats["reconciled"] = reconciled
    if truncation:
        run.stats["truncation"] = truncation
        logger.info("aggregator %s %s truncated: %s", channel, mode, truncation)
    await session_store.record_success(db, channel)
    return written


async def _sweep_all(mode: str, lock_key: int) -> int:
    if not is_enabled():
        return 0
    _register_providers()
    async with advisory_lock.held(lock_key, name=f"aggregator {mode}") as mine:
        if not mine:
            return 0
        touched = 0
        async with AsyncSessionFactory() as db:
            for channel, provider in PROVIDERS.items():
                try:
                    touched += await _sweep_channel(db, channel, provider, mode)
                    await db.commit()
                except Exception:  # noqa: BLE001 — one channel must not stop the rest
                    await db.rollback()
                    logger.exception("aggregator %s %s sweep failed", channel, mode)
        return touched


async def sweep_sales_once() -> int:
    return await _sweep_all(RUN_MODE_SALES, _SALES_LOCK_KEY)


async def sweep_finance_once() -> int:
    return await _sweep_all(RUN_MODE_FINANCE, _FINANCE_LOCK_KEY)


async def run_sales_forever() -> None:
    tick = settings.AGGREGATOR_SALES_TICK_SECONDS
    logger.info("aggregator sales sweep started (every %ss)", tick)
    while True:
        try:
            await asyncio.sleep(tick)
            written = await sweep_sales_once()
            if written:
                logger.info("aggregator sales sweep wrote %s order(s)", written)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad tick must not stop them all
            logger.exception("aggregator sales sweep tick failed")


async def run_finance_forever() -> None:
    tick = settings.AGGREGATOR_FINANCE_TICK_SECONDS
    logger.info("aggregator finance sweep started (every %ss)", tick)
    while True:
        try:
            await asyncio.sleep(tick)
            written = await sweep_finance_once()
            if written:
                logger.info("aggregator finance sweep wrote %s record(s)", written)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad tick must not stop them all
            logger.exception("aggregator finance sweep tick failed")
