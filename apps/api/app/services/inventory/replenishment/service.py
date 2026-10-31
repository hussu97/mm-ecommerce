"""The forecast as the form reads it: build a snapshot now, run the engine, shape
the response. Read-only."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.replenishment import (
    ForecastBranchLine,
    ForecastItem,
    ForecastProduction,
    ForecastResponse,
)
from app.services.inventory.replenishment import engine, loaders
from app.services.inventory.replenishment.settings import load_settings, to_engine


def _r(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def to_items(
    snapshot: engine.Snapshot, results: list[engine.ItemForecast]
) -> list[ForecastItem]:
    out = []
    for result in results:
        item = snapshot.items[result.item_id]
        lines = [
            ForecastBranchLine(
                branch_id=line.branch_id,
                branch_name=snapshot.branches[line.branch_id].name,
                kind=line.kind,
                qty=line.qty,
                day_demand_mean=_r(line.day_demand_mean),
                window_start=line.window_start,
                window_end=line.window_end,
                window_demand_mean=_r(line.window_mean),
                window_demand_quantile=line.window_quantile,
                floor=_r(line.floor),
                on_hand=_r(line.on_hand),
                target=line.target,
                need=line.need,
                shortfall=line.shortfall,
                tier=line.tier,
            )
            for line in result.lines
        ]
        production = None
        if result.production is not None:
            p = result.production
            production = ForecastProduction(
                units=_r(p.units),
                batches=p.batches,
                raw_units=_r(p.raw_units),
                protection_demand_mean=_r(p.protection_mean),
                protection_demand_quantile=p.protection_quantile,
                floors=_r(p.floors),
                usable_stock=_r(p.usable_stock),
                capped_by_shelf_life=p.capped_by_shelf_life,
                window_start=p.window_start,
                window_end=p.window_end,
            )
        out.append(
            ForecastItem(
                item_id=item.id,
                item_name=item.name,
                storage_unit=item.storage_unit,
                source_on_hand=_r(result.pool_on_hand),
                lines=lines,
                production=production,
                explain=result.explain,
            )
        )
    return out


def respond(snapshot: engine.Snapshot, as_of: datetime) -> ForecastResponse:
    """Run the engine over a snapshot and shape the response. Pure Python, no
    database and no session — which is what lets `current_forecast` run it off
    the event loop."""
    results = engine.forecast(snapshot)
    return ForecastResponse(
        business_date=snapshot.business_date,
        as_of=as_of,
        source_branch_id=snapshot.pool_branch_id,
        bucket_hours=snapshot.settings.bucket_hours,
        service_level=snapshot.settings.service_level,
        algo_version=engine.ALGO_VERSION,
        history_days=len({fact.business_date for fact in snapshot.facts}),
        items=to_items(snapshot, results),
    )


#: How long one "now" forecast is served for. The form is opened a handful of
#: times a morning, each open took seconds of single-core CPU, and the answer
#: barely moves inside ten minutes (the facts are daily; only the stock and the
#: intraday position drift).
CACHE_SECONDS = 600

#: (source branch, engine settings, 10-minute bucket) → the response. In process
#: memory: a handful of small responses, one per branch at most, and only the
#: current bucket is kept. Changing a setting changes the key, so a saved
#: setting is reflected on the next open.
_cache: dict[tuple[uuid.UUID | None, engine.Settings, int], ForecastResponse] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _compute(
    db: AsyncSession, *, source_branch_id: uuid.UUID | None, as_of: datetime
) -> ForecastResponse:
    snapshot = await loaders.build_snapshot(
        db, as_of=as_of, source_branch_id=source_branch_id
    )
    # The engine is seconds of pure-Python arithmetic over ~8k facts. On the
    # loop it froze every other request on the worker for that long; in a
    # thread the loop keeps serving (the GIL is handed back every few ms).
    return await asyncio.to_thread(respond, snapshot, as_of)


async def current_forecast(
    db: AsyncSession,
    *,
    source_branch_id: uuid.UUID | None,
    as_of: datetime | None = None,
) -> ForecastResponse:
    """The forecast as of `as_of`, or — for the form, which passes none — as of
    now, reused for up to `CACHE_SECONDS` per source branch and settings. A
    cached answer carries the `as_of` it was computed at, so it never claims to
    be fresher than it is."""
    if as_of is not None:
        return await _compute(db, source_branch_id=source_branch_id, as_of=as_of)

    now = _utcnow()
    settings_row = await load_settings(db)
    key = (
        source_branch_id or settings_row.production_branch_id,
        to_engine(settings_row),
        int(now.timestamp()) // CACHE_SECONDS,
    )
    cached = _cache.get(key)
    if cached is not None:
        return cached
    response = await _compute(db, source_branch_id=source_branch_id, as_of=now)
    for stale in [k for k in _cache if k[2] != key[2]]:
        del _cache[stale]
    _cache[key] = response
    return response
