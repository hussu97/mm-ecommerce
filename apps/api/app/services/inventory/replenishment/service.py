"""The forecast as the form reads it: build a snapshot now, run the engine, shape
the response. Read-only."""

from __future__ import annotations

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


async def current_forecast(
    db: AsyncSession,
    *,
    source_branch_id: uuid.UUID | None,
    bucket_hours: int | None = None,
    as_of: datetime | None = None,
) -> ForecastResponse:
    as_of = as_of or datetime.now(timezone.utc)
    snapshot = await loaders.build_snapshot(
        db, as_of=as_of, source_branch_id=source_branch_id, bucket_hours=bucket_hours
    )
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
