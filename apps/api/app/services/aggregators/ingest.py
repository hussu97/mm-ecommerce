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
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.aggregator import (
    AGGREGATOR_CHANNELS,
    CHANNEL_KEETA,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_MODE_BACKFILL,
    RUN_MODE_FINANCE,
    RUN_MODE_SALES,
    RUN_PARTIAL,
    RUN_RUNNING,
    SESSION_LIVE,
    AggregatorBranchMap,
    AggregatorOrder,
    AggregatorOrderItem,
    AggregatorOrderStatusEvent,
    AggregatorPayout,
    AggregatorSession,
    AggregatorStatement,
    AggregatorStatementLine,
    AggregatorSyncRun,
)
from app.models.base import utcnow
from app.services.aggregators import crypto, reconcile, session_store
from app.services.aggregators.modifiers import modifiers_to_json
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
    "sweep_promote_once",
    "sweep_reconcile_once",
    "link_statements_to_payouts",
    "run_daily_once",
    "PROVIDERS",
    "ingest_keeta_payloads",
    "ingest_keeta_finance_payloads",
    "ingest_deliveroo_finance_payloads",
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


async def _mm_order_for_external(
    db: AsyncSession, channel: str, external_order_id: str | None
) -> uuid.UUID | None:
    """The promoted MM order for this marketplace id, if promotion already linked it."""
    if not external_order_id:
        return None
    return await db.scalar(
        select(AggregatorOrder.mm_order_id).where(
            AggregatorOrder.channel == channel,
            AggregatorOrder.external_order_id == external_order_id,
            AggregatorOrder.mm_order_id.is_not(None),
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
    "display_ref",
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
    "customer_name",
    "customer_phone",
    "customer_address",
    "driver_name",
    "driver_phone",
    "driver_status",
    "accepted_at",
    "delivered_at",
    "cancelled_at",
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


def _aware_business(dt: datetime | None) -> datetime | None:
    """A marketplace timestamp as a tz-aware instant, in the shop's clock.

    Every marketplace we pull reports order times in Dubai business time, but the
    providers disagree on the Python type: Careem's REST and Deliveroo's Z-suffixed
    ISO arrive tz-AWARE, while Talabat's CSV, Keeta's page JSON and Noon's OMS hand
    back a NAIVE Dubai wall-clock. `placed_at`/etc. land in `timestamptz` columns
    (and `placed_at` becomes the promoted order's `created_at`), and a naive value
    written there is read back as UTC — so a 23:16 Dubai order was stored as 23:16Z
    and rendered as 03:16 the next morning, four hours in the future, which is why
    an order looked "placed" hours after the sync that pulled it ran.

    Stamp a naive value with Dubai so the stored instant is the real one; leave an
    already-aware value exactly as the provider resolved it. One rule, one seam —
    every sales order (httpx sweep and the Keeta push alike) funnels through here.
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=_DUBAI)


async def upsert_order(db: AsyncSession, channel: str, order: StandardOrder) -> None:
    branch_id = await _branch_for(db, channel, order.external_outlet_id)
    values = {
        "channel": channel,
        "external_order_id": order.external_order_id,
        "display_ref": order.display_ref,
        "branch_id": branch_id,
        "business_date": order.business_date,
        "placed_at": _aware_business(order.placed_at),
        "accepted_at": _aware_business(order.accepted_at),
        "delivered_at": _aware_business(order.delivered_at),
        "cancelled_at": _aware_business(order.cancelled_at),
        "status": order.status,
        "currency": order.currency,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "customer_address": (
            _json_safe(order.customer_address)
            if order.customer_address is not None
            else None
        ),
        "driver_name": order.driver_name,
        "driver_phone": order.driver_phone,
        "driver_status": order.driver_status,
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
            "modifiers": modifiers_to_json(item.modifiers),
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

    for ev in order.status_events:
        ev_values = {
            "channel": channel,
            "external_order_id": order.external_order_id,
            "status": ev.status,
            "at": _aware_business(ev.at),
            "sequence": ev.sequence,
            "raw": _json_safe(ev.raw) if ev.raw is not None else None,
        }
        # `status` is part of the natural key — an order revisiting a status is
        # the same step, upserted, not a new row. `at`/`sequence` still refresh.
        ev_update = {
            k: v
            for k, v in ev_values.items()
            if k not in ("channel", "external_order_id", "status")
        }
        ev_update["updated_at"] = _touched_at(AggregatorOrderStatusEvent, ev_update)
        await db.execute(
            pg_insert(AggregatorOrderStatusEvent)
            .values(**ev_values)
            .on_conflict_do_update(
                constraint="uq_aggregator_order_status_event", set_=ev_update
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


async def ingest_keeta_finance_payloads(
    db: AsyncSession, payloads: list[dict]
) -> tuple[int, int]:
    """Parse and upsert a batch of in-page-fetched Keeta finance payloads.

    The bootstrap worker fetches finance data in-page (where `mtgsig` is signed)
    and pushes the raw payloads to `/keeta/finance`, which calls this. Each
    payload is isolated — a single malformed one is logged and skipped. Returns
    `(statements_written, payouts_written)`.

    When the payload is only download-task metadata (figures live in PDFs rather
    than in the JSON), `parse_finance` sets a `truncation_note` and returns empty
    lists; this is logged but not an error — the API still responds 200 with zero
    counts, so the bootstrap worker can surface it to its caller.
    """
    from app.services.providers import keeta_provider

    statements_written = 0
    payouts_written = 0
    for payload in payloads:
        try:
            result = keeta_provider.provider.parse_finance(payload)
        except Exception:  # noqa: BLE001 — one bad payload must not fail the batch
            logger.exception("keeta finance payload parse failed — skipped")
            continue
        if result.truncation_note:
            logger.info("keeta finance payload truncated: %s", result.truncation_note)
        for statement in result.statements:
            await _upsert_statement(db, CHANNEL_KEETA, statement)
            statements_written += 1
        for payout in result.payouts:
            await _upsert_payout(db, CHANNEL_KEETA, payout)
            payouts_written += 1
    return statements_written, payouts_written


async def ingest_deliveroo_finance_payloads(
    db: AsyncSession, payloads: list[dict]
) -> tuple[int, int]:
    """Parse and upsert a batch of in-page-fetched Deliveroo invoice payloads.

    Deliveroo's invoice *list* replays over httpx, but the invoice *download*
    403s behind Cloudflare, so the bootstrap worker fetches each statement CSV
    and PDF in-page (carrying the browser's `cf_clearance`) and pushes the raw
    payloads to `/deliveroo/finance`, which calls this. Each payload is one
    invoice; `deliveroo_provider.parse_pushed_finance` reconstructs the
    `StandardStatement` (summary + per-order CSV lines + archived VAT PDF), which
    is then upserted exactly as the httpx finance sweep upserts a statement. Each
    payload is isolated — a single malformed one is logged and skipped. Returns
    `(statements_written, lines_written)`.
    """
    from app.models.aggregator import CHANNEL_DELIVEROO
    from app.services.providers import deliveroo_provider

    statements_written = 0
    lines_written = 0
    for payload in payloads:
        try:
            statement = deliveroo_provider.provider.parse_pushed_finance(payload)
        except Exception:  # noqa: BLE001 — one bad payload must not fail the batch
            logger.exception("deliveroo finance payload parse failed — skipped")
            continue
        if statement is None:
            continue
        await _upsert_statement(db, CHANNEL_DELIVEROO, statement)
        statements_written += 1
        lines_written += len(statement.lines)
    return statements_written, lines_written


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
        "external_outlet_id": statement.external_outlet_id,
        "invoice_object_key": statement.invoice_object_key,
        "invoice_content_type": statement.invoice_content_type,
        "invoice_original_filename": statement.invoice_original_filename,
        "invoice_fetched_at": statement.invoice_fetched_at,
        "invoice_attachments": (
            _json_safe(statement.invoice_attachments)
            if statement.invoice_attachments is not None
            else None
        ),
        "raw": _json_safe(statement.raw) if statement.raw is not None else None,
    }
    insert_stmt = pg_insert(AggregatorStatement).values(**values)
    preserve = {
        "invoice_object_key",
        "invoice_content_type",
        "invoice_original_filename",
        "invoice_fetched_at",
        "invoice_attachments",
        "external_outlet_id",
        "total_fees",
        "total_vat",
        "gross_sales",
        "net_payable",
    }
    update = {}
    for k in values:
        if k in ("channel", "statement_id"):
            continue
        proposed = getattr(insert_stmt.excluded, k)
        update[k] = (
            func.coalesce(proposed, getattr(AggregatorStatement, k))
            if k in preserve
            else proposed
        )
    update["updated_at"] = _touched_at(AggregatorStatement, update)
    await db.execute(
        insert_stmt.on_conflict_do_update(
            constraint="uq_aggregator_statement", set_=update
        )
    )
    for line in statement.lines:
        mm_order_id = await _mm_order_for_external(db, channel, line.external_order_id)
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
            "mm_order_id": mm_order_id,
            "grain": line.grain,
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

    # Couple every order this statement settled back to it, so the order→statement
    # link holds for EVERY channel — not only the ones whose sales feed already
    # carries the statement id (noon's RMS does; deliveroo/talabat/keeta learn it
    # here, from the statement's own per-order lines). Fills a null only: a sales
    # pull that already knew the settling statement is left as it is. This is the
    # missing half of the payout→statement→line→order→mm chain — the line already
    # names the order, and now the order names the statement.
    settled_order_ids = {
        line.external_order_id for line in statement.lines if line.external_order_id
    }
    if settled_order_ids:
        await db.execute(
            sql_update(AggregatorOrder)
            .where(
                AggregatorOrder.channel == channel,
                AggregatorOrder.external_order_id.in_(settled_order_ids),
                AggregatorOrder.statement_id.is_(None),
            )
            .values(statement_id=statement.statement_id)
            .execution_options(synchronize_session=False)
        )


async def link_statements_to_payouts(db: AsyncSession, channel: str) -> int:
    """Couple each statement to the payout that settled it. Returns links made.

    Two ways a payout names the statement it cleared, tried in order; only a
    still-null `payout_transfer_id` is ever filled, so a re-run is idempotent and a
    hand-corrected link is never clobbered. This is the payments leg of the
    reconciliation chain: payout ← statement ← line ← order ← mm_order.

    1. **Direct id.** Some marketplaces put the statement id ON the payout: Keeta's
       weekly bill payout carries the `statement_id` it settles, and Deliveroo's
       derived 1:1 payout is keyed by the statement itself. Back-link by that id —
       exact, and it reaches statements the date roll-up cannot (Keeta's statements
       carry no `payment_due_date`).

    2. **Date roll-up.** Where the payout does not name a statement (noon, talabat),
       a marketplace pays in batches: one transfer clears every statement that came
       due since the last transfer (verified on noon — a payout of 8328.29 is
       exactly the 5046.48 + 3281.81 of the two statements due before it). So each
       remaining statement is settled by the FIRST payout on or after its
       `payment_due_date`, resolved by the payment window rather than by amount
       (talabat's per-invoice statements carry no net_payable to match on).
    """
    linked = 0

    # Pass 1 — direct id (Keeta, derived-Deliveroo).
    named_payouts = list(
        await db.scalars(
            select(AggregatorPayout).where(
                AggregatorPayout.channel == channel,
                AggregatorPayout.statement_id.is_not(None),
            )
        )
    )
    if named_payouts:
        transfer_by_statement: dict[str, str] = {}
        for payout in named_payouts:
            transfer_by_statement.setdefault(payout.statement_id, payout.transfer_id)
        named = list(
            await db.scalars(
                select(AggregatorStatement).where(
                    AggregatorStatement.channel == channel,
                    AggregatorStatement.payout_transfer_id.is_(None),
                    AggregatorStatement.statement_id.in_(transfer_by_statement.keys()),
                )
            )
        )
        for statement in named:
            statement.payout_transfer_id = transfer_by_statement[statement.statement_id]
            linked += 1

    # Pass 2 — date roll-up for whatever a payout did not name directly.
    statements = list(
        await db.scalars(
            select(AggregatorStatement).where(
                AggregatorStatement.channel == channel,
                AggregatorStatement.payout_transfer_id.is_(None),
                AggregatorStatement.payment_due_date.is_not(None),
            )
        )
    )
    if statements:
        payouts = sorted(
            (
                p
                for p in await db.scalars(
                    select(AggregatorPayout).where(
                        AggregatorPayout.channel == channel,
                        AggregatorPayout.transfer_date.is_not(None),
                    )
                )
            ),
            key=lambda p: p.transfer_date,
        )
        for statement in statements:
            due = statement.payment_due_date
            # The first payout whose transfer landed on or after this statement's due
            # date is the one that cleared it (payouts are date-sorted).
            settling = next((p for p in payouts if p.transfer_date >= due), None)
            if settling is not None:
                statement.payout_transfer_id = settling.transfer_id
                linked += 1

    if linked:
        await db.flush()
    return linked


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
async def _new_run(
    db: AsyncSession,
    channel: str,
    mode: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> AggregatorSyncRun:
    run = AggregatorSyncRun(
        channel=channel,
        mode=mode,
        status=RUN_RUNNING,
        started_at=utcnow(),
        from_date=from_date.isoformat() if from_date else None,
        to_date=to_date.isoformat() if to_date else None,
    )
    db.add(run)
    await db.flush()
    return run


#: One-shot backfill window when bootstrapping a channel for the first time — an
#: adhoc `sweep_channel_once` can pass this to widen the ordinary daily lookback.
_BACKFILL_LOOKBACK_DAYS = 365


def _dubai_range_window(from_date: date, to_date: date) -> tuple[datetime, datetime]:
    """Inclusive business-date range [from_date 00:00 .. to_date 23:59:59.999999],
    as Dubai-aware datetimes.

    Every provider filters its window on `since.date()`/`until.date()` in Dubai
    business time (Talabat/Deliveroo pass those dates to their export APIs, adding
    their own +1 exclusive-end day). Anchoring both ends to the Dubai day boundary
    makes an explicit range mean exactly those business dates inclusive — the same
    calendar-alignment the daily `_start_of_today_dubai` path relies on, so a
    range rerun tiles cleanly with the nightly sweep and never double-counts at a
    seam. Dubai has no DST, so a fixed wall-clock boundary is unambiguous.
    """
    start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=_DUBAI)
    end = datetime(
        to_date.year, to_date.month, to_date.day, 23, 59, 59, 999999, tzinfo=_DUBAI
    )
    return start, end


def _sweep_window(
    now: datetime,
    *,
    from_date: date | None,
    to_date: date | None,
    lookback_days: int | None,
    lookback_hours: int | None,
) -> tuple[datetime, datetime]:
    """Resolve the (since, until) window for a sweep.

    Precedence: explicit business-date range → rolling `lookback_hours` (sub-day
    catch-up, no calendar boundary) → Dubai-calendar `lookback_days` ending at the
    last instant of yesterday (the daily default; a 1-day lookback = yesterday's
    Dubai date exactly, whole days tiling with no overlap).
    """
    if from_date is not None and to_date is not None:
        return _dubai_range_window(from_date, to_date)
    if lookback_hours is not None:
        return now - timedelta(hours=lookback_hours), now
    today_start = _start_of_today_dubai(now)
    days = (
        lookback_days
        if lookback_days is not None
        else settings.AGGREGATOR_LOOKBACK_DAYS
    )
    until = today_start - timedelta(microseconds=1)
    since = today_start - timedelta(days=days)
    return since, until


async def _fetch_and_persist(
    db: AsyncSession,
    channel: str,
    provider: BaseAggregatorClient,
    mode: str,
    session,
    *,
    since: datetime,
    until: datetime,
) -> tuple[int, str | None, dict]:
    """Fetch one channel/mode for a window and persist it. No run bookkeeping.

    Returns (records_written, truncation_note, detail) where detail breaks the
    write down by kind ({"orders": n} for sales, {"statements": n, "payouts": m,
    "invoices": k} for finance). Raises AggregatorAuthError /
    AggregatorUnavailableError for the caller to record on the run row. Shared by
    the daily `_sweep_channel` and the ranged `run_range`, so both take the exact
    same idempotent upsert path (every write is an on-conflict upsert on a
    channel-scoped natural key — re-running any window never double-counts).
    """
    if mode == RUN_MODE_SALES:
        result: SalesResult = await provider.fetch_sales(
            session, since=since, until=until
        )
        written = 0
        for order in result.orders:
            # One malformed order must not abort the whole channel's sweep and roll
            # back every good order with it — isolate it, like the reconcile/promote
            # passes and the Keeta push path already do.
            try:
                await upsert_order(db, channel, order)
                written += 1
            except Exception:  # noqa: BLE001 — one order must not stop the rest
                logger.exception(
                    "aggregator %s sales: order %s failed to upsert",
                    channel,
                    order.external_order_id,
                )
        return written, result.truncation_note, {"orders": written}
    finance: FinanceResult = await provider.fetch_finance(
        session, since=since, until=until
    )
    for statement in finance.statements:
        await _upsert_statement(db, channel, statement)
    for payout in finance.payouts:
        await _upsert_payout(db, channel, payout)
    # The statement→payout rollup and reconciliation run channel-agnostically in
    # `sweep_reconcile_once`, so they cover the push channels (Keeta, Deliveroo
    # finance) that never reach this httpx sweep too.
    written = len(finance.statements) + len(finance.payouts)
    detail = {"statements": len(finance.statements), "payouts": len(finance.payouts)}
    return written, finance.truncation_note, detail


async def _session_for(db: AsyncSession, channel: str, provider: BaseAggregatorClient):
    """Load + enrich + prepare a channel session, or None if not live."""
    session = await session_store.load(db, channel)
    session = await session_store.enrich_session(db, session)
    prepare = getattr(provider, "prepare_session", None)
    if callable(prepare):
        session = await prepare(db, session)
    if session is None or session.status != SESSION_LIVE:
        return None
    return session


async def _sweep_channel(
    db: AsyncSession,
    channel: str,
    provider: BaseAggregatorClient,
    mode: str,
    *,
    lookback_days: int | None = None,
    lookback_hours: int | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> int:
    """One channel's sweep for one mode. Returns records written; 0 on skip.

    With `from_date`/`to_date` (Dubai business dates) it sweeps that explicit
    inclusive range; otherwise the Dubai-calendar lookback window (daily default).
    """
    session = await _session_for(db, channel, provider)
    if session is None:
        return 0

    run = await _new_run(db, channel, mode, from_date=from_date, to_date=to_date)
    since, until = _sweep_window(
        utcnow(),
        from_date=from_date,
        to_date=to_date,
        lookback_days=lookback_days,
        lookback_hours=lookback_hours,
    )
    try:
        written, truncation, _detail = await _fetch_and_persist(
            db, channel, provider, mode, session, since=since, until=until
        )
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
    run.stats = {"written": written, "from": since.isoformat(), "to": until.isoformat()}
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
    from_date: date | None = None,
    to_date: date | None = None,
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
            from_date=from_date,
            to_date=to_date,
        )
        await db.commit()
        return written


#: "mmBATCH" + 9 — one lock for the whole ranged rerun, so two manual triggers
#: serialise. It may overlap the nightly sweep safely: every write is an
#: idempotent upsert on a channel-scoped natural key and promotion converges on
#: the `(source, external_reference)` key, so a concurrent pass updates rather
#: than duplicates.
_RANGE_LOCK_KEY = 0x6D6D_4241_5443_4809


async def _range_stats(
    db: AsyncSession, channel: str, from_date: date, to_date: date
) -> dict:
    """Promotion/coverage stats for a channel over a business-date range.

    All counts are scoped to `aggregator_order.business_date` in [from, to]
    (String(10), lexicographic == chronological for ISO dates). "existing" =
    promoted onto an order GrubOps already owns (Barsha/Sharjah overlay); "new" =
    promotion-created. Invoices/statements/payouts are counted from the DB, so a
    re-run reports the true current coverage, not just this pass's deltas.
    """
    from app.models.grubops_order import GrubOpsOrderMap

    lo, hi = from_date.isoformat(), to_date.isoformat()
    in_range = (
        AggregatorOrder.channel == channel,
        AggregatorOrder.business_date >= lo,
        AggregatorOrder.business_date <= hi,
    )
    retrieved = (
        await db.scalar(
            select(func.count()).select_from(AggregatorOrder).where(*in_range)
        )
        or 0
    )
    promoted = (
        await db.scalar(
            select(func.count())
            .select_from(AggregatorOrder)
            .where(*in_range, AggregatorOrder.mm_order_id.is_not(None))
        )
        or 0
    )
    grubops_owned = select(GrubOpsOrderMap.mm_order_id).where(
        GrubOpsOrderMap.mm_order_id.is_not(None)
    )
    existing = (
        await db.scalar(
            select(func.count())
            .select_from(AggregatorOrder)
            .where(
                *in_range,
                AggregatorOrder.mm_order_id.is_not(None),
                AggregatorOrder.mm_order_id.in_(grubops_owned),
            )
        )
        or 0
    )
    statements = (
        await db.scalar(
            select(func.count())
            .select_from(AggregatorStatement)
            .where(AggregatorStatement.channel == channel)
        )
        or 0
    )
    invoices = (
        await db.scalar(
            select(func.count())
            .select_from(AggregatorStatement)
            .where(
                AggregatorStatement.channel == channel,
                AggregatorStatement.invoice_object_key.is_not(None),
            )
        )
        or 0
    )
    payouts = (
        await db.scalar(
            select(func.count())
            .select_from(AggregatorPayout)
            .where(AggregatorPayout.channel == channel)
        )
        or 0
    )
    not_promoted = retrieved - promoted
    pct = lambda n: round(100.0 * n / retrieved, 1) if retrieved else 0.0  # noqa: E731
    return {
        "orders_retrieved": retrieved,
        "orders_promoted": promoted,
        "orders_promoted_new": promoted - existing,
        "orders_promoted_existing": existing,
        "orders_not_promoted": not_promoted,
        "pct_promoted": pct(promoted),
        "pct_existing": pct(existing),
        "pct_not_promoted": pct(not_promoted),
        "statements_total": statements,
        "invoices_total": invoices,
        "payouts_total": payouts,
    }


async def run_range(
    channels: list[str],
    from_date: date,
    to_date: date,
    *,
    modes: tuple[str, ...] = (RUN_MODE_SALES, RUN_MODE_FINANCE),
    promote: bool = True,
    reconcile: bool = True,
) -> list[dict]:
    """Re-runnable scrape+promote+reconcile over an explicit Dubai business-date
    range, for any set of channels, any number of times a day.

    One `aggregator_sync_run` row per channel (mode=backfill) records the whole
    operation with rich stats — what was retrieved (orders/statements/payouts/
    invoices) and the promotion split (new / existing / not-promoted, with %s) —
    plus any per-mode error, so the admin Runs table can show exactly what each
    trigger did. Idempotent: safe to re-run the same range, and to overlap the
    nightly sweep. Push-only channels (Keeta) can't be re-scraped, so their orders
    are re-ingested from the payloads saved on `aggregator_order.raw` instead —
    enough to re-apply the current normalisation and re-promote, without a re-push.
    """
    _register_providers()
    from app.models.aggregator import AGGREGATOR_CHANNELS
    from app.services.aggregators import promote as promote_mod
    from app.services.aggregators import reconcile as reconcile_mod

    results: list[dict] = []
    async with advisory_lock.held(_RANGE_LOCK_KEY, name="aggregator range") as mine:
        if not mine:
            logger.warning("aggregator range rerun already in progress; skipping")
            return results
        for channel in channels:
            if channel not in AGGREGATOR_CHANNELS:
                raise ValueError(f"unknown aggregator channel: {channel}")
            async with AsyncSessionFactory() as db:
                result = await _run_range_channel(
                    db,
                    channel,
                    from_date,
                    to_date,
                    modes=modes,
                    promote=promote,
                    reconcile=reconcile,
                    promote_mod=promote_mod,
                    reconcile_mod=reconcile_mod,
                )
                await db.commit()
                results.append(result)
    return results


def _push_order_parser(channel: str):
    """The single-order parser for a push-only channel's stored `raw`, or None.

    A push channel never registers an httpx provider, so `PROVIDERS` can't reach it;
    this maps it to the provider method that turns one saved order payload back into
    a `StandardOrder`, so a backfill can re-ingest from `aggregator_order.raw`."""
    if channel == CHANNEL_KEETA:
        from app.services.providers import keeta_provider

        return keeta_provider.provider.order_from_raw
    return None


async def _renormalize_stored(
    db: AsyncSession, channel: str, from_date: date, to_date: date
) -> int:
    """Re-ingest a push-only channel's stored orders from their saved `raw` payloads.

    Keeta is pushed in by the bootstrap worker and can't be re-scraped from here, but
    every order it pushed kept its original marketplace payload on
    `aggregator_order.raw`. Re-parsing that payload and re-upserting re-applies the
    current normalisation — the `placed_at` timezone fix among it — to already-stored
    rows, which is the only way to correct them without a re-push. Idempotent: the raw
    is the immutable source, so re-running lands the same corrected value. Scoped to
    the Dubai business-date range (which was always derived correctly); a row with no
    `raw` is skipped. Returns the number of rows rewritten."""
    parse = _push_order_parser(channel)
    if parse is None:
        return 0
    lo, hi = from_date.isoformat(), to_date.isoformat()
    rows = (
        await db.scalars(
            select(AggregatorOrder).where(
                AggregatorOrder.channel == channel,
                AggregatorOrder.business_date >= lo,
                AggregatorOrder.business_date <= hi,
                AggregatorOrder.raw.is_not(None),
            )
        )
    ).all()
    count = 0
    for agg in rows:
        try:
            order = parse(agg.raw)
        except Exception:  # noqa: BLE001 — one bad payload must not stop the rest
            logger.exception(
                "aggregator %s renormalize: order %s failed to parse",
                channel,
                agg.external_order_id,
            )
            continue
        if order is None:
            continue
        await upsert_order(db, channel, order)
        count += 1
    return count


async def _run_range_channel(
    db: AsyncSession,
    channel: str,
    from_date: date,
    to_date: date,
    *,
    modes: tuple[str, ...],
    promote: bool,
    reconcile: bool,
    promote_mod,
    reconcile_mod,
) -> dict:
    run = await _new_run(
        db, channel, RUN_MODE_BACKFILL, from_date=from_date, to_date=to_date
    )
    stats: dict = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "modes": {},
    }
    errors: list[str] = []

    provider = PROVIDERS.get(channel)
    push_only = provider is None
    if push_only:
        # Push-only channel (Keeta): the httpx sweep can't reach it, but every order
        # it pushed kept its marketplace payload on `aggregator_order.raw`. Re-run
        # those payloads through the same parse+upsert so a backfill still re-applies
        # the current normalisation — notably the placed_at tz fix — to already-stored
        # rows without a re-push. The promote/reconcile below then corrects the MM
        # orders exactly as it does for the scraped channels.
        try:
            written = await _renormalize_stored(db, channel, from_date, to_date)
            stats["modes"][RUN_MODE_SALES] = {
                "written": written,
                "source": "stored_raw",
            }
        except Exception as exc:  # noqa: BLE001 — one channel must not kill the run row
            errors.append(f"renormalize: {exc}")
            logger.exception("aggregator %s range renormalize failed", channel)
    else:
        session = await _session_for(db, channel, provider)
        if session is None:
            run.status = RUN_FAILED
            run.error = "session not live — needs a headed re-login"
            run.finished_at = utcnow()
            stats["skipped"] = "session_not_live"
            run.stats = stats
            return {"channel": channel, "status": run.status, "stats": stats}

        since, until = _dubai_range_window(from_date, to_date)
        for mode in modes:
            try:
                written, trunc, detail = await _fetch_and_persist(
                    db, channel, provider, mode, session, since=since, until=until
                )
                stats["modes"][mode] = {"written": written, **detail}
                if trunc:
                    stats["modes"][mode]["truncation"] = trunc
            except AggregatorAuthError as exc:
                errors.append(f"{mode}: session dead: {exc}")
                await session_store.mark_needs_bootstrap(db, channel, error=str(exc))
            except AggregatorUnavailableError as exc:
                errors.append(f"{mode}: unavailable: {exc}")
            except Exception as exc:  # noqa: BLE001 — one mode must not kill the run row
                errors.append(f"{mode}: {exc}")
                logger.exception("aggregator %s range %s failed", channel, mode)

    if promote:
        try:
            await promote_mod.promote_channel(db, channel)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"promote: {exc}")
            logger.exception("aggregator %s range promote failed", channel)
    if reconcile:
        try:
            await link_statements_to_payouts(db, channel)
            await reconcile_mod.reconcile_channel(db, channel)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reconcile: {exc}")
            logger.exception("aggregator %s range reconcile failed", channel)

    stats.update(await _range_stats(db, channel, from_date, to_date))
    run.finished_at = utcnow()
    if errors:
        run.status = RUN_PARTIAL
        run.error = "; ".join(errors)[:2000]
    else:
        run.status = RUN_COMPLETED
        # A push-only channel has no scrape session to credit — the success it
        # would stamp belongs to the bootstrap worker's login, not this re-parse.
        if not push_only:
            await session_store.record_success(db, channel)
    run.stats = stats
    return {"channel": channel, "status": run.status, "stats": stats}


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


#: "mmBATCH" + 8, after sales/finance/promote — reconciliation serialises alone.
_RECONCILE_LOCK_KEY = 0x6D6D_4241_5443_4808


async def sweep_reconcile_once() -> int:
    """Reconcile every channel's new-or-changed orders against their MM orders.

    Runs over `aggregator_order` for EVERY `AGGREGATOR_CHANNELS`, independent of
    how the data arrived — so Keeta, which is pushed in by the bootstrap worker
    and never reaches the httpx finance sweep, is reconciled just like the httpx
    channels (it never was before: reconciliation used to run only inside that
    sweep, which iterates the httpx `PROVIDERS` and so silently skipped Keeta).
    Runs after promotion in the daily pass, so it compares against the freshest
    statements and promoted orders. Its own advisory lock; one bad channel is
    logged and does not stop the rest. Returns reconciliation rows written.
    """
    if not is_enabled():
        return 0
    from app.models.aggregator import AGGREGATOR_CHANNELS

    async with advisory_lock.held(
        _RECONCILE_LOCK_KEY, name="aggregator reconcile"
    ) as mine:
        if not mine:
            return 0
        touched = 0
        async with AsyncSessionFactory() as db:
            for channel in AGGREGATOR_CHANNELS:
                try:
                    # Close the payments leg first (statement→payout rollup), so
                    # a finance query joining payout↔statement↔line↔order sees the
                    # freshest links. Channel-agnostic, so it covers the push
                    # channels (Keeta/Deliveroo) the httpx sweep never touches.
                    await link_statements_to_payouts(db, channel)
                    touched += await reconcile.reconcile_channel(db, channel)
                    await db.commit()
                except Exception:  # noqa: BLE001 — one channel must not stop the rest
                    await db.rollback()
                    logger.exception("aggregator %s reconcile failed", channel)
        return touched


async def run_daily_once() -> tuple[int, int]:
    """One full daily pass: sales → finance → promote → reconcile.

    Returns `(sales_written, finance_written)`. Finance runs after sales so it
    settles the freshest orders; promotion runs next, closing the
    order→mm_order (and statement-line→mm_order) links over whatever the sweeps
    and the Keeta push landed; reconciliation runs last, over EVERY channel, so
    it compares against the freshest statements and the just-promoted orders and
    covers Keeta as well as the httpx channels. Each no-ops when disabled or when
    a channel has no live session.
    """
    sales = await sweep_sales_once()
    finance = await sweep_finance_once()
    promoted = await sweep_promote_once()
    if promoted:
        logger.info("aggregator promotion filed %s MM order(s)", promoted)
    reconciled = await sweep_reconcile_once()
    if reconciled:
        logger.info("aggregator reconciliation wrote %s row(s)", reconciled)
    return sales, finance


#: Holds the in-flight manual-trigger task so the event loop's weak reference to
#: it cannot let it be collected mid-sweep (convention #5's tracked-task rule, the
#: way `indexnow_service._pending` does). Discarded on completion so it is not a
#: leak. A full daily pass takes minutes and must not block the click that started
#: it — and BackgroundTasks are dropped when the process is reaped after the
#: response, which would leave the run half-done and nothing recording it.
_manual_runs: set[asyncio.Task] = set()


def _launch_tracked(coro) -> bool:
    """Fire a coroutine on the running loop, held until it finishes. Returns whether
    it started — False when there is no loop (a management command or a test)."""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        logger.debug("aggregator manual trigger: no event loop, skipped")
        coro.close()
        return False
    _manual_runs.add(task)
    task.add_done_callback(_manual_runs.discard)
    return True


def trigger_daily_in_background() -> bool:
    """Kick off one full daily pass now, off the request. Returns whether it started.

    Fires `run_daily_once` on the running loop and returns immediately, so the
    admin's "Run now" answers at once; the Runs table fills in as each channel's
    `aggregator_sync_run` row opens and closes. Safe to click while the nightly
    pass (or a previous click) is mid-flight: every sweep is guarded by its own
    Postgres advisory lock and simply no-ops if one is already held, and every
    write is an idempotent upsert, so a concurrent pass updates rather than
    duplicates. No-op (returns False) when the ingest is disabled or unconfigured.
    """
    if not is_enabled():
        return False
    return _launch_tracked(_run_daily_logged())


def trigger_range_in_background(
    from_date: date, to_date: date, channels: list[str] | None = None
) -> bool:
    """Kick off a backfill over an explicit Dubai business-date range, off the request.

    The dated sibling of the daily trigger — for re-pulling specific past days (say,
    to correct orders scraped before a fix landed). Runs `run_range` (a `backfill`
    run per channel: scrape → promote → reconcile), which re-upserts each order and,
    because that advances `updated_at`, drives promotion to bring already-filed
    orders back in line — so an order's `created_at` is re-derived from the corrected
    placed-at on the next pull. `channels` defaults to every scrape channel; Keeta is
    push-only and `run_range` records it skipped. No-op (False) when disabled."""
    if not is_enabled():
        return False
    chans = list(channels) if channels else list(AGGREGATOR_CHANNELS)
    return _launch_tracked(_run_range_logged(chans, from_date, to_date))


async def _run_daily_logged() -> None:
    """`run_daily_once` with its own error boundary — an orphaned task that raises
    would only warn to the loop, so log it properly and keep the failure off the
    process's unhandled-exception path."""
    try:
        sales, finance = await run_daily_once()
        logger.info(
            "aggregator manual run finished: %s sales, %s finance written",
            sales,
            finance,
        )
    except Exception:  # noqa: BLE001 — background task, nothing above to catch it
        logger.exception("aggregator manual run failed")


async def _run_range_logged(
    channels: list[str], from_date: date, to_date: date
) -> None:
    """`run_range` with its own error boundary — see `_run_daily_logged`."""
    try:
        results = await run_range(channels, from_date, to_date)
        logger.info(
            "aggregator manual range run finished (%s → %s): %s",
            from_date.isoformat(),
            to_date.isoformat(),
            {r["channel"]: r["status"] for r in results},
        )
    except Exception:  # noqa: BLE001 — background task, nothing above to catch it
        logger.exception("aggregator manual range run failed")


def _start_of_today_dubai(now: datetime) -> datetime:
    """Midnight (00:00) at the start of today, as a Dubai-AWARE datetime.

    Kept in Dubai time on purpose: every provider filters its window with
    `since.date()` / `until.date()` (and Talabat/Deliveroo pass those dates
    straight to their export APIs), and marketplace orders are dated in Dubai
    business time. Returning a UTC instant here would make `.date()` fall on the
    previous calendar day for the four hours Dubai is ahead of UTC, so "yesterday"
    would silently become "the day before". Anchoring to the Dubai day boundary is
    what makes a 1-day lookback mean yesterday's Dubai date exactly, with whole
    days tiling and no double-count at the seam.
    """
    return now.astimezone(_DUBAI).replace(hour=0, minute=0, second=0, microsecond=0)


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
