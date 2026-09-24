"""The shadow history: a daily snapshot of the forecast, scored against what was
actually done and what then sold.

**Snapshot.** Once a day at `snapshot_time` (Dubai), the forecast for the
production branch is stored in `replenishment_forecasts` — one row per
destination (`transfer`), one for what the production branch keeps (`retain`)
and one for production. It is never linked to an order: the form only shows it.

**Evaluation.** After the business day closes, its facts are (re)built and each
row gets what actually happened, matched by date, item and branch:

* transfers requested and sent — children of non-cancelled transfer orders due
  that day (`required_date`, else the order's business date: the POS auto-print
  rule) from the production branch;
* production planned (production orders) and produced (ledger PRODUCTION at the
  production branch, so the shift report's "Produced" column counts too);
* sales, stock-out minutes and stock-out-adjusted demand from the facts, and the
  same weekday last week's sales as the naive baseline. A production row is
  scored on the day after it, the day its batch protects.

**Loop.** Every ten minutes under its own advisory lock: build any missing facts
(back to the first sale, a few days per tick), rebuild the last three days once
the day rolls over (aggregator orders reach their final status late), evaluate,
and take the day's snapshot once its time has passed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.models.operations import (
    ProductionLine,
    ProductionLineStatusEnum,
    ProductionOrder,
    Transfer,
    TransferKindEnum,
    TransferLine,
    TransferOrder,
    TransferStatusEnum,
)
from app.models.replenishment import ReplenishmentDailyFact, ReplenishmentForecast
from app.services.inventory.replenishment import engine, facts, loaders
from app.services.inventory.replenishment.settings import load_settings
from app.services.pos import business_day_service

logger = logging.getLogger(__name__)

#: "mmBATCH" + 0F, the next free slot after auto-availability's 0E.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_480F
_TICK_SECONDS = 600
_TICK_BUDGET_SECONDS = 300
#: Days the loop builds per tick while catching up on history.
_CATCH_UP_PER_TICK = 14
#: Days rebuilt after each rollover — aggregator orders settle late.
_REBUILD_DAYS = 3


def _d(value) -> float | None:
    return None if value is None else float(value)


# ─── Snapshot ─────────────────────────────────────────────────────────────────


async def snapshot_day(
    db: AsyncSession, *, as_of: datetime, mode: str = "scheduled"
) -> int:
    """Store the forecast for the production branch as of `as_of`. Replaces the
    same day's rows of the same mode. Flushes; the caller commits."""
    settings_row = await load_settings(db)
    pool_id = settings_row.production_branch_id
    if pool_id is None:
        return 0
    snapshot = await loaders.build_snapshot(db, as_of=as_of, source_branch_id=pool_id)
    results = engine.forecast(snapshot)
    settings = snapshot.settings
    params = {
        "bucket_hours": settings.bucket_hours,
        "service_level": settings.service_level,
        "pool_weight": settings.pool_weight,
        "same_day_ready_time": settings.same_day_ready_time.isoformat(),
        "next_day_category_ids": sorted(str(c) for c in settings.next_day_category_ids),
        "half_life_days": settings.half_life_days,
        "window_days": settings.window_days,
    }
    await db.execute(
        delete(ReplenishmentForecast).where(
            ReplenishmentForecast.business_date == snapshot.business_date,
            ReplenishmentForecast.source_branch_id == pool_id,
            ReplenishmentForecast.mode == mode,
        )
    )
    written = 0
    for result in results:
        levels = result.explain.get("levels", {})
        for line in result.lines:
            db.add(
                ReplenishmentForecast(
                    business_date=snapshot.business_date,
                    source_branch_id=pool_id,
                    branch_id=line.branch_id,
                    item_id=result.item_id,
                    kind=line.kind,
                    mode=mode,
                    snapshot_at=as_of,
                    algo_version=engine.ALGO_VERSION,
                    forecast_qty=line.qty,
                    day_demand_mean=round(line.day_demand_mean, 4),
                    window_demand_mean=round(line.window_mean, 4),
                    window_demand_quantile=line.window_quantile,
                    floor_qty=round(line.floor, 4),
                    on_hand_at_snapshot=round(line.on_hand, 4),
                    pool_at_snapshot=round(result.pool_on_hand, 4),
                    shortfall_qty=line.shortfall,
                    allocation_tier=line.tier,
                    params=params,
                    explain={
                        "window_start": line.window_start.isoformat()
                        if line.window_start
                        else None,
                        "window_end": line.window_end.isoformat()
                        if line.window_end
                        else None,
                        "target": line.target,
                        "need": line.need,
                        "model": levels.get(str(line.branch_id), {}),
                    },
                )
            )
            written += 1
        if result.production is not None:
            p = result.production
            db.add(
                ReplenishmentForecast(
                    business_date=snapshot.business_date,
                    source_branch_id=pool_id,
                    branch_id=pool_id,
                    item_id=result.item_id,
                    kind="production",
                    mode=mode,
                    snapshot_at=as_of,
                    algo_version=engine.ALGO_VERSION,
                    forecast_qty=round(p.units, 4),
                    day_demand_mean=round(p.next_day_demand_mean, 4),
                    window_demand_mean=round(p.protection_mean, 4),
                    window_demand_quantile=p.protection_quantile,
                    floor_qty=round(p.floors, 4),
                    on_hand_at_snapshot=round(p.usable_stock, 4),
                    pool_at_snapshot=round(result.pool_on_hand, 4),
                    shortfall_qty=0,
                    allocation_tier=None,
                    params=params,
                    explain={
                        "window_start": p.window_start.isoformat()
                        if p.window_start
                        else None,
                        "window_end": p.window_end.isoformat()
                        if p.window_end
                        else None,
                        "batches": p.batches,
                        "raw_units": round(p.raw_units, 4),
                        "capped_by_shelf_life": p.capped_by_shelf_life,
                    },
                )
            )
            written += 1
    await db.flush()
    return written


# ─── Evaluation ───────────────────────────────────────────────────────────────


def _storage_qty(quantity_col, factor_col):
    """A line quantity in storage units: quantity × its conversion factor gives
    ingredient units; ÷ the item's storage→ingredient factor gives storage."""
    return (
        func.coalesce(quantity_col, 0)
        * factor_col
        / func.nullif(InventoryItem.storage_to_ingredient_factor, 0)
    )


async def _actual_transfers(
    db: AsyncSession, day: date, source_id: uuid.UUID
) -> tuple[
    dict[tuple[uuid.UUID, uuid.UUID], float], dict[tuple[uuid.UUID, uuid.UUID], float]
]:
    due = or_(
        TransferOrder.required_date == day,
        and_(
            TransferOrder.required_date.is_(None),
            TransferOrder.business_date == day.isoformat(),
        ),
    )
    rows = (
        await db.execute(
            select(
                Transfer.branch_id,
                TransferLine.item_id,
                func.sum(
                    _storage_qty(TransferLine.quantity, TransferLine.conversion_factor)
                ),
                func.sum(
                    _storage_qty(
                        TransferLine.sent_quantity, TransferLine.conversion_factor
                    )
                ),
            )
            .join(Transfer, Transfer.id == TransferLine.transfer_id)
            .join(TransferOrder, TransferOrder.id == Transfer.transfer_order_id)
            .join(InventoryItem, InventoryItem.id == TransferLine.item_id)
            .where(
                TransferOrder.kind == TransferKindEnum.TRANSFER.value,
                TransferOrder.source_branch_id == source_id,
                Transfer.status != TransferStatusEnum.CANCELLED.value,
                due,
            )
            .group_by(Transfer.branch_id, TransferLine.item_id)
        )
    ).all()
    requested = {(b, i): float(q or 0) for b, i, q, _ in rows}
    sent = {(b, i): float(s or 0) for b, i, _, s in rows}
    return requested, sent


async def _actual_production(
    db: AsyncSession, day: date, source_id: uuid.UUID
) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, float]]:
    planned = {
        item_id: float(q or 0)
        for item_id, q in (
            await db.execute(
                select(
                    ProductionLine.item_id,
                    func.sum(
                        _storage_qty(
                            ProductionLine.planned_quantity,
                            ProductionLine.conversion_factor,
                        )
                    ),
                )
                .join(
                    ProductionOrder,
                    ProductionOrder.id == ProductionLine.production_order_id,
                )
                .join(InventoryItem, InventoryItem.id == ProductionLine.item_id)
                .where(
                    ProductionOrder.source_branch_id == source_id,
                    ProductionOrder.business_date == day.isoformat(),
                    ProductionLine.status != ProductionLineStatusEnum.CANCELLED.value,
                )
                .group_by(ProductionLine.item_id)
            )
        ).all()
    }
    produced = {
        item_id: float(q or 0)
        for item_id, q in (
            await db.execute(
                select(
                    InventoryTransactionItem.item_id,
                    func.sum(InventoryTransactionItem.signed_quantity),
                )
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryTransactionItem.transaction_id,
                )
                .where(
                    InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.PRODUCTION.value,
                    InventoryTransaction.branch_id == source_id,
                    InventoryTransaction.business_date == day.isoformat(),
                )
                .group_by(InventoryTransactionItem.item_id)
            )
        ).all()
    }
    return planned, produced


async def _outcomes(
    db: AsyncSession, day: date, window_days: int, bucket_hours: int
) -> tuple[
    dict[tuple[uuid.UUID, uuid.UUID], dict], dict[tuple[uuid.UUID, uuid.UUID], float]
]:
    """(branch, item) → sales / demand / stock-out figures for `day`; and
    (branch, item) → the same weekday last week's sales."""
    branches = list(
        (await db.execute(select(Branch).where(Branch.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    cals = await facts.branch_calendars(db, branches)
    infos = {b.id: engine.BranchInfo(b.id, b.name, cals[b.id]) for b in branches}
    history = await loaders.load_facts(
        db, day - timedelta(days=window_days), day + timedelta(days=1)
    )
    history = [f for f in history if f.branch_id in infos]
    profiles = engine.ProfileModel(history, infos, bucket_hours)
    out = {}
    baseline = {}
    week_ago = day - timedelta(days=7)
    closing = {
        (row.branch_id, row.item_id): _d(row.closing_on_hand)
        for row in (
            await db.execute(
                select(ReplenishmentDailyFact).where(
                    ReplenishmentDailyFact.business_date == day
                )
            )
        ).scalars()
    }
    for fact in history:
        key = (fact.branch_id, fact.item_id)
        if fact.business_date == week_ago:
            baseline[key] = fact.sales_units
        if fact.business_date != day:
            continue
        est = engine.estimate_demand(fact, profiles)
        stockout = None
        if fact.hourly_in_stock_minutes is not None:
            stockout = max(0, fact.open_minutes - sum(fact.hourly_in_stock_minutes))
        out[key] = {
            "sales": fact.sales_units,
            "est": est.value if est.value is not None else fact.sales_units,
            "share": est.in_stock_share,
            "stockout": stockout,
            "closing": closing.get(key),
        }
    return out, baseline


async def evaluate_day(db: AsyncSession, day: date) -> int:
    """Fill actuals and outcomes on `day`'s rows, and the outcomes of the
    previous day's production rows (the day their batch protects)."""
    settings_row = await load_settings(db)
    rows = list(
        (
            await db.execute(
                select(ReplenishmentForecast).where(
                    or_(
                        ReplenishmentForecast.business_date == day,
                        and_(
                            ReplenishmentForecast.business_date
                            == day - timedelta(days=1),
                            ReplenishmentForecast.kind == "production",
                        ),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    outcomes, baseline = await _outcomes(
        db, day, settings_row.window_days, settings_row.bucket_hours
    )
    now = datetime.now(timezone.utc)
    cache: dict[tuple[str, date, uuid.UUID], tuple] = {}

    async def transfers(source_id: uuid.UUID):
        key = ("t", day, source_id)
        if key not in cache:
            cache[key] = await _actual_transfers(db, day, source_id)
        return cache[key]

    async def production(on: date, source_id: uuid.UUID):
        key = ("p", on, source_id)
        if key not in cache:
            cache[key] = await _actual_production(db, on, source_id)
        return cache[key]

    requested_by_item: dict[tuple[uuid.UUID, date, uuid.UUID], float] = defaultdict(
        float
    )
    for row in rows:
        if row.kind == "transfer" and row.business_date == day:
            requested, _ = await transfers(row.source_branch_id)
            requested_by_item[(row.source_branch_id, day, row.item_id)] += (
                requested.get((row.branch_id, row.item_id), 0.0)
            )

    for row in rows:
        if row.kind in ("transfer", "retain") and row.business_date == day:
            requested, sent = await transfers(row.source_branch_id)
            key = (row.branch_id, row.item_id)
            if row.kind == "transfer":
                row.actual_requested_qty = requested.get(key, 0.0)
                row.actual_sent_qty = sent.get(key, 0.0)
            else:
                row.actual_requested_qty = max(
                    0.0,
                    float(row.pool_at_snapshot)
                    - requested_by_item[(row.source_branch_id, day, row.item_id)],
                )
            outcome = outcomes.get(key)
            if outcome:
                row.realized_sales = outcome["sales"]
                row.est_demand = outcome["est"]
                row.in_stock_share = outcome["share"]
                row.stockout_minutes = outcome["stockout"]
                row.closing_on_hand = outcome["closing"]
            row.baseline_demand = baseline.get(key)
            row.evaluated_at = now
        elif row.kind == "production":
            if row.business_date == day:
                planned, produced = await production(day, row.source_branch_id)
                row.actual_planned_qty = planned.get(row.item_id, 0.0)
                row.actual_produced_qty = produced.get(row.item_id, 0.0)
            else:
                # The batch made yesterday protects today: score it on today's
                # network sales and stock-outs.
                network = [v for (b, i), v in outcomes.items() if i == row.item_id]
                if network:
                    row.realized_sales = sum(v["sales"] for v in network)
                    row.est_demand = sum(v["est"] for v in network)
                    row.stockout_minutes = sum(v["stockout"] or 0 for v in network)
                    row.baseline_demand = sum(
                        v for (b, i), v in baseline.items() if i == row.item_id
                    )
                row.evaluated_at = now
    await db.flush()
    return len(rows)


# ─── Loop ─────────────────────────────────────────────────────────────────────


async def tick(db: AsyncSession) -> None:
    """One pass of the loop, on the loop's own `held_session` (no request owns
    it). It commits after each unit of work — a day's facts, a day's scores, the
    snapshot — so a failure later in the tick never rolls back finished days."""
    settings_row = await load_settings(db)
    await db.commit()
    pool_id = settings_row.production_branch_id
    tz = await business_day_service.resolve_timezone(db)
    now = datetime.now(timezone.utc)
    pool = await db.get(Branch, pool_id) if pool_id else None
    anchor = (
        pool
        or (
            await db.execute(select(Branch).where(Branch.deleted_at.is_(None)).limit(1))
        ).scalar_one_or_none()
    )
    if anchor is None:
        return
    today = date.fromisoformat(business_day_service.business_date_for(anchor, now, tz))
    yesterday = today - timedelta(days=1)

    ctx = await facts.load_context(db)

    # Rebuild the days just closed, once, after the rollover.
    rollover = datetime.combine(today, datetime.min.time()).replace(
        tzinfo=tz
    ) + timedelta(hours=facts.day_start_hour(anchor))
    built = await db.scalar(
        select(func.max(ReplenishmentDailyFact.built_at)).where(
            ReplenishmentDailyFact.business_date == yesterday
        )
    )
    rebuilt_yesterday = False
    if built is None or built < rollover:
        for offset in range(_REBUILD_DAYS - 1, -1, -1):
            await facts.build_day(db, yesterday - timedelta(days=offset), ctx)
            # Each day on its own (see the docstring): the loop owns this session.
            await db.commit()
        rebuilt_yesterday = True

    # Catch up on history (first deploy, or a gap).
    first = await facts.first_sale_date(db)
    if first is not None:
        for day in (await facts.missing_days(db, first, yesterday))[
            :_CATCH_UP_PER_TICK
        ]:
            await facts.build_day(db, day, ctx)
            # Each day on its own (see the docstring): the loop owns this session.
            await db.commit()

    # Score every day whose facts now exist and whose rows are not yet scored.
    pending = (
        (
            await db.execute(
                select(ReplenishmentForecast.business_date)
                .where(
                    ReplenishmentForecast.evaluated_at.is_(None),
                    ReplenishmentForecast.business_date <= yesterday,
                )
                .distinct()
                .order_by(ReplenishmentForecast.business_date)
                .limit(_CATCH_UP_PER_TICK)
            )
        )
        .scalars()
        .all()
    )
    days = set(pending)
    # A day's production rows are scored when the day after it is evaluated.
    days.update(d + timedelta(days=1) for d in pending if d < yesterday)
    if rebuilt_yesterday:
        days.update({yesterday, yesterday - timedelta(days=1)})
    for day in sorted(days):
        await evaluate_day(db, day)
        # Each day on its own (see the docstring): the loop owns this session.
        await db.commit()

    # Today's snapshot, once its time has passed.
    if pool_id is not None:
        local_now = now.astimezone(tz)
        due = (
            local_now.date() == today and local_now.time() >= settings_row.snapshot_time
        )
        have = await db.scalar(
            select(func.count())
            .select_from(ReplenishmentForecast)
            .where(
                ReplenishmentForecast.business_date == today,
                ReplenishmentForecast.source_branch_id == pool_id,
                ReplenishmentForecast.mode == "scheduled",
            )
        )
        if due and not have:
            written = await snapshot_day(db, as_of=now)
            # The loop owns this session (see the docstring).
            await db.commit()
            logger.info("Replenishment snapshot for %s: %d rows", today, written)


async def run_forever() -> None:
    logger.info("Replenishment history loop started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            await heartbeat.beat("replenishment")
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="replenishment history"
            ) as db:
                if db is not None:
                    # A budget, so a wedged tick cannot pin the connection.
                    async with asyncio.timeout(_TICK_BUDGET_SECONDS):
                        await tick(db)
        except asyncio.CancelledError:
            logger.info("Replenishment history loop stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Replenishment history tick failed")
        await asyncio.sleep(_TICK_SECONDS)


# ─── Reading ──────────────────────────────────────────────────────────────────


def _accuracy(rows: list[ReplenishmentForecast]):
    from app.schemas.replenishment import ForecastAccuracy

    scored = [
        r for r in rows if r.evaluated_at is not None and r.est_demand is not None
    ]
    est = sum(float(r.est_demand) for r in scored)
    errors = [float(r.day_demand_mean) - float(r.est_demand) for r in scored]
    base = [r for r in scored if r.baseline_demand is not None]
    base_est = sum(float(r.est_demand) for r in base)
    ran_out = [r for r in scored if (r.stockout_minutes or 0) > 0]
    more = less = 0
    for r in scored:
        actual = r.actual_sent_qty if r.kind == "transfer" else r.actual_produced_qty
        if actual is None:
            continue
        gap = float(r.forecast_qty) - float(actual)
        if gap > 0 and (r.stockout_minutes or 0) > 0:
            more += 1
        elif (
            gap < 0
            and r.closing_on_hand is not None
            and float(r.closing_on_hand) >= -gap
        ):
            less += 1
    return ForecastAccuracy(
        rows=len(scored),
        wape=round(sum(abs(e) for e in errors) / est, 4) if est > 0 else None,
        bias=round(sum(errors) / est, 4) if est > 0 else None,
        baseline_wape=(
            round(
                sum(abs(float(r.baseline_demand) - float(r.est_demand)) for r in base)
                / base_est,
                4,
            )
            if base_est > 0
            else None
        ),
        stockout_rows=len(ran_out),
        forecast_more_and_ran_out=more,
        forecast_less_and_surplus=less,
        est_lost_sales=round(
            sum(
                max(0.0, float(r.est_demand) - float(r.realized_sales or 0))
                for r in ran_out
            ),
            2,
        ),
    )


async def history_view(
    db: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    branch_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    kind: str | None = None,
    mode: str | None = None,
):
    from app.schemas.replenishment import ForecastHistoryResponse, ForecastHistoryRow

    stmt = select(ReplenishmentForecast).where(
        ReplenishmentForecast.business_date >= date_from,
        ReplenishmentForecast.business_date <= date_to,
    )
    if branch_id:
        stmt = stmt.where(ReplenishmentForecast.branch_id == branch_id)
    if item_id:
        stmt = stmt.where(ReplenishmentForecast.item_id == item_id)
    if kind:
        stmt = stmt.where(ReplenishmentForecast.kind == kind)
    if mode:
        stmt = stmt.where(ReplenishmentForecast.mode == mode)
    rows = list(
        (
            await db.execute(
                stmt.order_by(
                    ReplenishmentForecast.business_date.desc(),
                    ReplenishmentForecast.kind,
                )
            )
        )
        .scalars()
        .all()
    )
    branch_names = dict((await db.execute(select(Branch.id, Branch.name))).all())
    item_ids = {r.item_id for r in rows}
    item_names = (
        dict(
            (
                await db.execute(
                    select(InventoryItem.id, InventoryItem.name).where(
                        InventoryItem.id.in_(item_ids)
                    )
                )
            ).all()
        )
        if item_ids
        else {}
    )
    out = []
    for r in rows:
        row = ForecastHistoryRow.model_validate(r)
        row.branch_name = branch_names.get(r.branch_id, "")
        row.item_name = item_names.get(r.item_id, "")
        out.append(row)
    return ForecastHistoryResponse(
        rows=out,
        transfer=_accuracy([r for r in rows if r.kind == "transfer"]),
        production=_accuracy([r for r in rows if r.kind == "production"]),
    )
