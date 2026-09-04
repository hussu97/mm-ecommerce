"""Audit each aggregator × branch: the hours the marketplace has vs MM's schedule.

For every branch that trades on a marketplace, this reads that marketplace's live
opening hours and diffs them against MM's canonical weekly schedule
(`branch_weekly_hours`) and the branch's derived `opening_from`/`opening_to`
window. It prints a per-branch × channel report and, with `--csv`, writes the
rows for a spreadsheet.

Read-only against the portals, but it opens real marketplace sessions — so it
runs on the **live api slot** and needs `CATALOG_SYNC_READ_ENABLED=1` with warm
sessions. A dead or blocked channel is reported as an error row rather than
crashing the run (re-login that channel and re-run); with reads disabled it falls
back to whatever snapshot was last stored and marks channels "no data".

Reuses the same drift engine the admin catalog-sync page shows
(`catalog_sync.refresh_all` + `compute_drift_all`), so this audit and that screen
never disagree.

Usage (on the VM; derive the live slot — api or api-green — from `docker ps`):
    docker compose exec <api-slot> \
        python -m scripts.audit_aggregator_hours [--csv /tmp/hours_audit.csv]
"""

from __future__ import annotations

import argparse
import asyncio
import csv as csvmod

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.branch import Branch
from app.models.catalog_sync import SNAPSHOT_HOURS
from app.services import branch_hours_service
from app.services.aggregators import catalog_sync

_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _weekly_line(rows) -> str:
    """MM's weekly schedule as `Sun 09:00-23:00 | Mon closed | …`."""
    by_day = {r.weekday: f"{r.opens}-{r.closes}" for r in rows}
    return " | ".join(f"{_DAYS[d]} {by_day.get(d, 'closed')}" for d in range(7))


def _hours_summary(hours_diff: dict | None) -> tuple[str, str]:
    """(status, detail) for one channel's hours diff."""
    if hours_diff is None:
        return "no data", "no snapshot read yet (session down or reads disabled)"
    total = hours_diff.get("total", 0)
    if total == 0:
        return "in sync", ""
    parts = []
    for d in hours_diff.get("deltas", []):
        mm = d.get("mm_value") or "—"
        ch = d.get("channel_value") or "—"
        parts.append(f"{d.get('entity', '?')}: MM {mm} vs channel {ch}")
    return f"{total} diff(s)", "; ".join(parts)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Audit aggregator vs MM hours per branch.")
    ap.add_argument("--csv", help="write the rows to this CSV path")
    args = ap.parse_args()

    csv_rows: list[dict[str, str]] = []
    async with AsyncSessionFactory() as db:
        branches = (
            (
                await db.execute(
                    select(Branch)
                    .where(Branch.is_active.is_(True), Branch.deleted_at.is_(None))
                    .options(selectinload(Branch.aggregator_maps))
                    .order_by(Branch.name)
                )
            )
            .scalars()
            .all()
        )

        print(
            f"\nAggregator hours audit — reads_enabled={settings.CATALOG_SYNC_READ_ENABLED}\n"
        )
        for branch in branches:
            channels = branch.aggregators
            if not channels:
                continue
            weekly = await branch_hours_service.list_weekly(db, branch.id)
            print(f"━━ {branch.name} ({branch.reference}) ━━")
            print(f"   MM window (derived) : {branch.opening_from}–{branch.opening_to}")
            print(f"   MM weekly schedule  : {_weekly_line(weekly) or '(none set)'}")

            if settings.CATALOG_SYNC_READ_ENABLED:
                # Best-effort live read; per-channel isolated, commits per target.
                await catalog_sync.refresh_all(
                    db, branch_id=branch.id, targets=channels, kinds=(SNAPSHOT_HOURS,)
                )
            drift = await catalog_sync.compute_drift_all(
                db, branch_id=branch.id, targets=channels
            )
            for channel in channels:
                cell = drift.get(channel, {})
                if "error" in cell:
                    status, detail = "error", cell["error"]
                else:
                    status, detail = _hours_summary(cell.get("hours"))
                print(f"     • {channel:<10} {status:<12} {detail}")
                csv_rows.append(
                    {
                        "branch": branch.name,
                        "reference": branch.reference,
                        "channel": channel,
                        "mm_window": f"{branch.opening_from}-{branch.opening_to}",
                        "mm_weekly": _weekly_line(weekly),
                        "status": status,
                        "detail": detail,
                    }
                )
            print()

    if args.csv and csv_rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csvmod.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"Wrote {len(csv_rows)} row(s) to {args.csv}")


if __name__ == "__main__":
    asyncio.run(main())
