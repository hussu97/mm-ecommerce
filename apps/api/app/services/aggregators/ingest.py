"""The daily pass that mirrors each marketplace's ledger into the aggregator_* tables.

One wall-clock schedule, generic across every aggregator: once a day at
`AGGREGATOR_RUN_HOUR_DXB` (Asia/Dubai) a single pass runs the **sales** sweep and
the **finance** sweep (statements/payouts + reconciliation) for each channel over
one rolling `AGGREGATOR_LOOKBACK_DAYS` window. Both mirror the same ledger; a
multi-day window catches orders that mutate after creation and statements that
post days late, and every write is an idempotent upsert on the channel-scoped
natural key so the overlap is free.

It is anchored to the wall clock, not to a fixed interval from boot, and it is
**restart-safe**: on start it reads the durable `aggregator_sync_run` trail and,
if the last due slot has no run recorded, catches it up immediately. This is the
fix for the old `sleep(tick)`-before-first-sweep loops — a container recreated
more than once a day (every deploy) reset the 24h finance timer and so never
reached the finance sweep at all. A wholesale failure retries with backoff; a
per-channel auth failure (`AggregatorAuthError`) still flips that session to
`needs_bootstrap` and is skipped — never retried in a loop, which is how an
account gets locked. Each channel's sweep is wrapped in an `aggregator_sync_run`
row so a failure is recorded rather than silent. Gated on
`AGGREGATOR_INGEST_ENABLED`, storefront only, each sweep under its own advisory
lock so a second worker no-ops instead of double-writing.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select
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
    AggregatorSession,
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
    "run_scheduler_forever",
    "sweep_sales_once",
    "sweep_finance_once",
    "run_daily_once",
    "PROVIDERS",
]

#: "mmBATCH" + 5/6, after the GrubOps order loop (…4804). Each loop needs a key
#: nobody else holds so two copies serialise instead of racing.
_SALES_LOCK_KEY = 0x6D6D_4241_5443_4805
_FINANCE_LOCK_KEY = 0x6D6D_4241_5443_4806

#: The shop's clock. The daily pass fires at `AGGREGATOR_RUN_HOUR_DXB` local time.
_DUBAI = ZoneInfo("Asia/Dubai")

#: A wholesale daily-pass failure (DB blip, every marketplace briefly down)
#: retries this many times, backing off between attempts. A per-channel auth
#: failure is NOT retried here — the sweep flips that session to
#: `needs_bootstrap` and moves on. Module constants, not env dials: this is the
#: resilience policy, not an operational knob.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 300

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


def _touched_at(model: Any, update: dict[str, Any]) -> Any:
    """`updated_at` that advances only when a real value changed on conflict.

    The upserts re-write the same rows every run (Keeta alone re-pulls ~2 months
    of orders each pass), and an unconditional `updated_at = now()` made every one
    look freshly changed — so the incremental `reconcile_channel`/`promote_channel`
    passes, which key off `updated_at > *_at`, re-processed the whole history
    daily. This keeps `updated_at` stable when the proposed values (already
    COALESCE-adjusted for the preserve columns) are not distinct from the stored
    ones, so a no-op re-upsert stays a no-op downstream. The row is still written;
    only the change signal is honest.
    """
    changed = or_(
        *[getattr(model, col).is_distinct_from(expr) for col, expr in update.items()]
    )
    return case((changed, utcnow()), else_=model.updated_at)


def _json_safe(value: Any) -> Any:
    """JSONB column values cannot hold Decimal/datetime — coerce for storage.

    Keeta's flatten path divides fils into `Decimal` on the same dict that becomes
    `StandardOrder.raw`; without this the upsert raises TypeError mid-batch.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


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
        "raw": _json_safe(order.raw) if order.raw is not None else None,
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
    update["updated_at"] = _touched_at(AggregatorOrder, update)
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
        item_update["updated_at"] = _touched_at(AggregatorOrderItem, item_update)
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
        "raw": _json_safe(statement.raw) if statement.raw is not None else None,
    }
    update = {k: v for k, v in values.items() if k not in ("channel", "statement_id")}
    update["updated_at"] = _touched_at(AggregatorStatement, update)
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
        line_update["updated_at"] = _touched_at(AggregatorStatementLine, line_update)
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
    update["updated_at"] = _touched_at(AggregatorPayout, update)
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


#: One-shot backfill window when bootstrapping a channel for the first time — an
#: adhoc `sweep_channel_once` can pass this to widen the ordinary daily lookback.
_BACKFILL_LOOKBACK_DAYS = 365


async def _sweep_channel(
    db: AsyncSession,
    channel: str,
    provider: BaseAggregatorClient,
    mode: str,
    *,
    lookback_days: int | None = None,
    lookback_hours: int | None = None,
) -> int:
    """One channel's sweep for one mode. Returns records written; 0 on skip."""
    session = await session_store.load(db, channel)
    session = await session_store.enrich_session(db, session)
    prepare = getattr(provider, "prepare_session", None)
    if callable(prepare):
        session = await prepare(db, session)
    if session is None or session.status != SESSION_LIVE:
        return 0

    run = await _new_run(db, channel, mode)
    now = utcnow()
    written = 0
    reconciled = 0
    truncation: str | None = None
    # The default window is the shared daily lookback; an adhoc sweep can widen it
    # (e.g. a first-time channel backfill) via lookback_days / lookback_hours.
    if lookback_hours is not None:
        since = now - timedelta(hours=lookback_hours)
    elif lookback_days is not None:
        since = now - timedelta(days=lookback_days)
    else:
        since = now - timedelta(days=settings.AGGREGATOR_LOOKBACK_DAYS)
    try:
        if mode == RUN_MODE_SALES:
            result: SalesResult = await provider.fetch_sales(
                session, since=since, until=now
            )
            for order in result.orders:
                await upsert_order(db, channel, order)
            written = len(result.orders)
            truncation = result.truncation_note
        else:
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
            # freshest statement commission. Incremental — only orders new or
            # changed since their last reconciliation (see reconcile_channel).
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


async def sweep_channel_once(
    channel: str,
    mode: str,
    *,
    lookback_days: int | None = None,
    lookback_hours: int | None = None,
) -> int:
    """Adhoc one-channel sweep (sales or finance) with an optional wider window."""
    _register_providers()
    provider = PROVIDERS.get(channel)
    if provider is None:
        raise ValueError(f"unknown or non-httpx aggregator channel: {channel}")
    async with AsyncSessionFactory() as db:
        written = await _sweep_channel(
            db,
            channel,
            provider,
            mode,
            lookback_days=lookback_days,
            lookback_hours=lookback_hours,
        )
        await db.commit()
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


#: "mmBATCH" + 7, after the sales/finance locks — promotion serialises on its own.
_PROMOTE_LOCK_KEY = 0x6D6D_4241_5443_4807


async def sweep_promote_once() -> int:
    """Promote every channel's new-or-changed orders into MM orders.

    Runs over the `aggregator_order` table, so it covers Keeta (pushed in by the
    bootstrap worker) as well as the httpx channels — independent of how an order
    landed. Part of the one aggregator feature (`AGGREGATOR_INGEST_ENABLED`), under
    its own advisory lock. Returns MM orders touched.
    """
    if not is_enabled():
        return 0
    from app.models.aggregator import AGGREGATOR_CHANNELS
    from app.services.aggregators import promote

    async with advisory_lock.held(_PROMOTE_LOCK_KEY, name="aggregator promote") as mine:
        if not mine:
            return 0
        touched = 0
        async with AsyncSessionFactory() as db:
            for channel in AGGREGATOR_CHANNELS:
                try:
                    touched += await promote.promote_channel(db, channel)
                    await db.commit()
                except Exception:  # noqa: BLE001 — one channel must not stop the rest
                    await db.rollback()
                    logger.exception("aggregator %s promotion failed", channel)
        return touched


async def run_daily_once() -> tuple[int, int]:
    """One full daily pass: the sales sweep then the finance sweep.

    Returns `(sales_written, finance_written)`. Finance runs after sales so the
    reconciliation inside it compares against the freshest orders; promotion runs
    last, over whatever the sweeps (and the Keeta push) have landed. Each no-ops
    when disabled or when a channel has no live session.
    """
    sales = await sweep_sales_once()
    finance = await sweep_finance_once()
    promoted = await sweep_promote_once()
    if promoted:
        logger.info("aggregator promotion filed %s MM order(s)", promoted)
    return sales, finance


def _run_hour_local(now: datetime) -> datetime:
    """Today's `AGGREGATOR_RUN_HOUR_DXB`:00, in Dubai local time."""
    return now.astimezone(_DUBAI).replace(
        hour=settings.AGGREGATOR_RUN_HOUR_DXB, minute=0, second=0, microsecond=0
    )


def _next_run_at(now: datetime) -> datetime:
    """The next run instant strictly after `now`, as an aware UTC datetime."""
    target = _run_hour_local(now)
    if target <= now.astimezone(_DUBAI):
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)


def _last_due_at(now: datetime) -> datetime:
    """The most recent run instant at or before `now`, as an aware UTC datetime."""
    target = _run_hour_local(now)
    if target > now.astimezone(_DUBAI):
        target -= timedelta(days=1)
    return target.astimezone(timezone.utc)


async def _slot_ran_since(since: datetime) -> bool:
    """Whether the daily pass has already executed since `since`.

    Reads the durable `aggregator_sync_run` trail — an existing run row (of any
    status) means the pass fired — rather than any in-memory flag, so a redeploy
    cannot lose the fact that a slot ran and re-trigger it. Only meaningful while
    the ingest is enabled with a live session; when nothing can run it stays
    False and the retry budget bounds the fruitless attempts.
    """
    async with AsyncSessionFactory() as db:
        row = await db.scalar(
            select(AggregatorSyncRun.id)
            .where(AggregatorSyncRun.started_at >= since)
            .limit(1)
        )
    return row is not None


async def _run_daily_with_retry() -> None:
    """Run the daily pass, retrying a wholesale failure with backoff.

    The pass is considered done once a run row exists for this slot. If none does
    — a total failure before any channel recorded a run — it retries up to
    `_RETRY_ATTEMPTS`. A slot still empty after that is left for the next boot
    catch-up and surfaced by the health check; it is never retried indefinitely,
    which for an auth-dead channel would risk locking the account.
    """
    slot = _last_due_at(utcnow())
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        sales, finance = await run_daily_once()
        logger.info(
            "aggregator daily pass done (attempt %s/%s): %s sales, %s finance record(s)",
            attempt,
            _RETRY_ATTEMPTS,
            sales,
            finance,
        )
        if not is_enabled() or await _slot_ran_since(slot):
            return
        if attempt < _RETRY_ATTEMPTS:
            logger.warning(
                "aggregator daily pass recorded no run — retrying in %ss",
                _RETRY_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
    logger.error(
        "aggregator daily pass still not recorded after %s attempts", _RETRY_ATTEMPTS
    )


#: A live session with no success/warm in this long is reported stale. Comfortably
#: longer than the daily cadence, so an ordinary day never trips it.
_HEALTH_STALE_AFTER = timedelta(days=2)


async def _log_health() -> None:
    """Log one health line per pass — a WARNING naming any channel that is not live
    or has gone stale, so the VM's log-based alerting has a single signal to watch
    (there is no ops push/email sink; these structured logs are the channel). Keeta
    records `last_warmed_at` rather than `last_success_at` — it is pushed in by the
    worker — so warming counts as liveness for it. Never raises."""
    try:
        async with AsyncSessionFactory() as db:
            rows = list(await db.scalars(select(AggregatorSession)))
    except Exception:  # noqa: BLE001 — health reporting must not fail a run
        logger.exception("aggregator health check failed")
        return
    now = utcnow()
    unhealthy: list[str] = []
    for r in rows:
        if r.status != SESSION_LIVE:
            unhealthy.append(f"{r.channel}={r.status}")
            continue
        last = r.last_success_at or r.last_warmed_at
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last > _HEALTH_STALE_AFTER:
                unhealthy.append(f"{r.channel}=stale({(now - last).days}d)")
    if unhealthy:
        logger.warning(
            "aggregator health: %s need attention", ", ".join(sorted(unhealthy))
        )
    else:
        logger.info("aggregator health: all sessions live")


async def run_scheduler_forever() -> None:
    """The once-daily aggregator pass, at `AGGREGATOR_RUN_HOUR_DXB` Dubai time.

    Wall-clock anchored with a boot catch-up: replaces the two `sleep(tick)`
    loops whose sleep-before-first-sweep meant a container recreated more than
    once a day never reached the daily finance sweep. Cancellation-safe; one bad
    day is logged and the loop lives on.
    """
    logger.info(
        "aggregator daily scheduler started (%02d:00 Asia/Dubai, %sd lookback)",
        settings.AGGREGATOR_RUN_HOUR_DXB,
        settings.AGGREGATOR_LOOKBACK_DAYS,
    )
    try:
        if is_enabled() and not await _slot_ran_since(_last_due_at(utcnow())):
            logger.info(
                "aggregator scheduler: last daily slot missed — catching up now"
            )
            await _run_daily_with_retry()
            await _log_health()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a failed catch-up must not kill the loop
        logger.exception("aggregator scheduler catch-up failed")

    while True:
        try:
            nxt = _next_run_at(utcnow())
            await asyncio.sleep(max(0.0, (nxt - utcnow()).total_seconds()))
            await _run_daily_with_retry()
            await _log_health()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad day must not stop them all
            logger.exception("aggregator daily run failed")
