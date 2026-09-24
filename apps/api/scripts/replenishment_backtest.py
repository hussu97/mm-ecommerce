"""Backtest the replenishment forecast over past days, into its shadow history.

For each business day in the range: build any missing demand facts, take the
forecast as it would have stood at the day's snapshot time (``mode='backtest'``,
point-in-time stock from the ledger, facts only from earlier days), then score it
against what was actually transferred, produced and sold. The admin history page
reads the rows with the "Backtest" filter.

Writes only the replenishment tables (facts and ``mode='backtest'`` forecasts);
re-running a day replaces its backtest rows. Nothing touches stock or orders.
Recipes, menus and availability floors are today's, not the day's.

Usage (on the VM, in the live api slot — see PRODUCTION.md for which one):
    docker compose exec <api-slot> python -m scripts.replenishment_backtest \
        --from 2026-09-12 --to 2026-09-23 [--rebuild-facts]
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta

from app.core.database import AsyncSessionFactory
from app.services.inventory.replenishment import facts, history
from app.services.inventory.replenishment.settings import load_settings
from app.services.pos import business_day_service


async def run(start: date, end: date, rebuild: bool) -> None:
    async with AsyncSessionFactory() as db:
        settings = await load_settings(db)
        tz = await business_day_service.resolve_timezone(db)
        ctx = await facts.load_context(db)
        first = await facts.first_sale_date(db)
        if first is None:
            print("No completed sales yet — nothing to backtest.")
            return
        through = end + timedelta(days=1)
        days = (
            [first + timedelta(days=n) for n in range((through - first).days + 1)]
            if rebuild
            else await facts.missing_days(db, first, through)
        )
        for day in days:
            written = await facts.build_day(db, day, ctx)
            await db.commit()
            print(f"facts {day}: {written} rows")

        day = start
        while day <= end:
            local = datetime.combine(day, settings.snapshot_time).replace(tzinfo=tz)
            written = await history.snapshot_day(db, as_of=local, mode="backtest")
            await db.commit()
            print(f"snapshot {day}: {written} rows")
            day += timedelta(days=1)

        day = start
        while day <= through:
            await history.evaluate_day(db, day)
            await db.commit()
            day += timedelta(days=1)

        view = await history.history_view(
            db, date_from=start, date_to=end, mode="backtest"
        )
        for label, acc in (
            ("transfer", view.transfer),
            ("production", view.production),
        ):
            print(
                f"{label}: rows={acc.rows} wape={acc.wape} bias={acc.bias} "
                f"baseline_wape={acc.baseline_wape} "
                f"stockout_rows={acc.stockout_rows} more&ran_out={acc.forecast_more_and_ran_out} "
                f"less&surplus={acc.forecast_less_and_surplus} lost={acc.est_lost_sales}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    parser.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--rebuild-facts",
        action="store_true",
        help="Rebuild every day's facts from the first sale, not only missing days",
    )
    args = parser.parse_args()
    asyncio.run(run(args.start, args.end, args.rebuild_facts))


if __name__ == "__main__":
    main()
