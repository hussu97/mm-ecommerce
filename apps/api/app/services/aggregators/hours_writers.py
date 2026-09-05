"""The write half of the hours sync — pushing a branch's schedule to a channel.

Mirror image of `menu_readers._HOURS_READERS`: that reads each marketplace's
opening hours, this writes them. httpx channels (talabat, deliveroo, noon,
careem) are registered here. Keeta's save is in-page (H5guard) — the headed
worker's job, not this module — so it is omitted from `supported_channels()`
and a direct call raises `HoursWriteUnsupported` with that reason.

Writes are gated behind `CATALOG_SYNC_ENABLED` and default to **dry-run**, the
same shape as `catalog_sync.create_menu_item`: the flag must be on, then
`dry_run=True` (the default) returns the payload it would POST and mutates
nothing. The branch-hours cron calls with that default.

Two operations, because a marketplace has no holiday concept: on an ordinary day
`push_hours` sends the day's window; on a closed day (holiday or a closed
weekday) `close_outlet` snoozes the outlet, and the next open day's `push_hours`
reopens it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import trading_hours
from app.core.config import settings
from app.models.aggregator import AggregatorBranchMap
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.aggregator_base import AggregatorUnavailableError

logger = logging.getLogger(__name__)

__all__ = [
    "HoursWriteUnsupported",
    "supported_channels",
    "push_hours",
    "push_weekly_hours",
    "close_outlet",
]

#: httpx writers. Keeta is deliberately absent — see module docstring.
_HTTPX_CHANNELS = frozenset({"talabat", "deliveroo", "noon", "careem"})


class HoursWriteUnsupported(NotImplementedError):
    """No writer exists for this channel, or writes are gated off."""


def supported_channels() -> frozenset[str]:
    """The channels a live hours writer exists for (httpx only; not keeta)."""
    return _HTTPX_CHANNELS


def _mm_weekday_today() -> int:
    """Today's MM weekday (0=Sunday … 6=Saturday) on the shop clock."""
    python_weekday = trading_hours.local(datetime.now(trading_hours.TZ)).weekday()
    return (python_weekday + 1) % 7


def _hhmmss(clock: str) -> str:
    """`"08:00"` → `"08:00:00"`. Leaves a value already in HH:MM:SS alone."""
    parts = (clock or "").split(":")
    if len(parts) >= 3:
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
    if len(parts) == 2:
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}:00"
    return clock


def _minutes(clock: str) -> int:
    """`"08:15"` → 495 (minutes from midnight)."""
    parts = (clock or "0:0").split(":")
    return int(parts[0]) * 60 + int(parts[1] if len(parts) > 1 else 0)


async def _branch_map(
    db: AsyncSession, channel: str, branch_id: Any
) -> AggregatorBranchMap:
    row = (
        await db.execute(
            select(AggregatorBranchMap).where(
                AggregatorBranchMap.channel == channel,
                AggregatorBranchMap.branch_id == branch_id,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.external_outlet_id:
        raise HoursWriteUnsupported(
            f"no active {channel} outlet map for branch {branch_id}"
        )
    return row


async def _load_session(db: AsyncSession, channel: str) -> LoadedSession:
    from app.services.aggregators import session_store
    from app.services.providers import deliveroo_provider, talabat_provider

    session = await session_store.load(db, channel)
    # Careem/noon replay the stored session as-is; only Deliveroo/Talabat mint
    # extra headers from the account row before the first GET.
    preparers = {
        "deliveroo": deliveroo_provider.provider.prepare_session,
        "talabat": talabat_provider.provider.prepare_session,
    }
    prepare = preparers.get(channel)
    if prepare is not None:
        session = await prepare(db, session)
    if session is None:
        raise HoursWriteUnsupported(f"no {channel} session")
    return session


def _ensure_writable(channel: str) -> None:
    if channel == "keeta":
        raise HoursWriteUnsupported(
            "keeta hours write is the headed worker's job (persistent Chrome "
            "profile / H5guard), not httpx"
        )
    if channel not in _HTTPX_CHANNELS:
        raise HoursWriteUnsupported(f"no hours writer for {channel} yet")
    if not settings.CATALOG_SYNC_ENABLED:
        raise HoursWriteUnsupported("hours writes are disabled (CATALOG_SYNC_ENABLED)")


async def push_hours(
    db: Any,
    *,
    channel: str,
    branch: Any,
    opens: str,
    closes: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Send today's `opens`–`closes` window to `channel` for `branch`.

    `dry_run` (the default) returns the exact payload the live write would send.
    """
    _ensure_writable(channel)
    weekday = _mm_weekday_today()
    mapper = _PUSHERS[channel]
    plan = await mapper(db, branch, opens=opens, closes=closes, weekday=weekday)
    plan.update(
        {
            "channel": channel,
            "branch_id": str(getattr(branch, "id", "")),
            "op": "push_hours",
            "opens": opens,
            "closes": closes,
            "dry_run": dry_run,
        }
    )
    if dry_run:
        logger.info("hours writer dry-run %s %s", channel, plan.get("endpoint"))
        return plan
    await _execute(channel, plan)
    plan["dry_run"] = False
    return plan


def _weekly_summary(weekly: dict[int, tuple[str, str]]) -> dict[str, str]:
    """A compact `{weekday: "opens-closes" | "closed"}` for the run-log/plan.

    MM weekday 0=Sunday … 6=Saturday; a weekday absent from `weekly` is closed.
    """
    return {
        str(wd): (f"{weekly[wd][0]}-{weekly[wd][1]}" if wd in weekly else "closed")
        for wd in range(7)
    }


async def push_weekly_hours(
    db: Any,
    *,
    channel: str,
    branch: Any,
    weekly: dict[int, tuple[str, str]],
    dry_run: bool = True,
) -> dict[str, Any]:
    """Mirror the branch's whole weekly schedule to `channel` in one write.

    `weekly` is `branch_hours_service.schedule(...)` — `{mm_weekday: (opens,
    closes)}`, a weekday absent = closed. Unlike `push_hours` (one weekday), this
    rebuilds all seven days from MM so the portal ends up an exact mirror of the
    source of truth. Read-modify-replace: the current portal payload is read so
    its non-hours envelope survives, then every day is rebuilt. `dry_run` (the
    default) returns the payload the live write would send and mutates nothing.
    """
    _ensure_writable(channel)
    mapper = _WEEKLY_PUSHERS[channel]
    plan = await mapper(db, branch, weekly=weekly)
    plan.update(
        {
            "channel": channel,
            "branch_id": str(getattr(branch, "id", "")),
            "op": "push_weekly_hours",
            "weekly": _weekly_summary(weekly),
            "dry_run": dry_run,
        }
    )
    if dry_run:
        logger.info("hours writer dry-run weekly %s %s", channel, plan.get("endpoint"))
        return plan
    await _execute(channel, plan)
    plan["dry_run"] = False
    return plan


async def close_outlet(
    db: Any, *, channel: str, branch: Any, dry_run: bool = True
) -> dict[str, Any]:
    """Snooze `branch`'s outlet on `channel` for a closed day (holiday/closed weekday)."""
    _ensure_writable(channel)
    weekday = _mm_weekday_today()
    mapper = _CLOSERS[channel]
    plan = await mapper(db, branch, weekday=weekday)
    plan.update(
        {
            "channel": channel,
            "branch_id": str(getattr(branch, "id", "")),
            "op": "close_outlet",
            "dry_run": dry_run,
        }
    )
    if dry_run:
        logger.info("hours writer dry-run close %s %s", channel, plan.get("endpoint"))
        return plan
    await _execute(channel, plan)
    plan["dry_run"] = False
    return plan


async def _execute(channel: str, plan: dict[str, Any]) -> None:
    """Dispatch a live write. `plan` is the dict the dry-run already returned."""
    session: LoadedSession = plan["session"]
    if channel == "deliveroo":
        from app.services.providers import deliveroo_provider as dp

        await dp.provider.put_opening_hours(
            session, plan["outlet_id"], plan["payload"]["hours"]
        )
        return
    if channel == "talabat":
        from app.services.providers import talabat_provider as tp

        if plan.get("status"):
            await tp.provider.put_vendor_status(session, plan["vendor"], plan["status"])
        if plan.get("calendars") is not None:
            await tp.provider.put_delivery_calendars(
                session, plan["vendor"], plan["calendars"]
            )
        return
    if channel == "noon":
        from app.services.providers import noon_provider as np

        await np.provider.save_outlet_schedule(
            session, plan["outlet_code"], plan["schedule"]
        )
        return
    if channel == "careem":
        from app.services.providers import careem_provider as cp

        await cp.provider.save_operational_hours(
            session,
            plan["company"],
            plan["brand"],
            plan["outlet"],
            plan["rows"],
        )
        return
    raise HoursWriteUnsupported(f"no live executor for {channel}")


# ── per-channel payload builders (read-modify, never write unless executed) ──


async def _deliveroo_push(
    db: AsyncSession, branch: Any, *, opens: str, closes: str, weekday: int
) -> dict[str, Any]:
    from app.services.providers import deliveroo_provider as dp

    session = await _load_session(db, "deliveroo")
    row = await _branch_map(db, "deliveroo", branch.id)
    outlet = row.external_outlet_id
    current = await dp.provider.get_opening_hours(session, outlet)
    hours = [
        h
        for h in (current.get("hours") if isinstance(current, dict) else current) or []
        if isinstance(h, dict) and h.get("day_of_week") != weekday
    ]
    hours.append(
        {
            "day_of_week": weekday,
            "local_start_time": _hhmmss(opens),
            "local_end_time": _hhmmss(closes),
        }
    )
    hours.sort(key=lambda h: int(h.get("day_of_week") or 0))
    return {
        "session": session,
        "outlet_id": outlet,
        "endpoint": f"PUT /api/restaurants/{outlet}/opening_hours",
        "payload": {"hours": hours},
    }


async def _deliveroo_close(
    db: AsyncSession, branch: Any, *, weekday: int
) -> dict[str, Any]:
    from app.services.providers import deliveroo_provider as dp

    session = await _load_session(db, "deliveroo")
    row = await _branch_map(db, "deliveroo", branch.id)
    outlet = row.external_outlet_id
    current = await dp.provider.get_opening_hours(session, outlet)
    hours = [
        h
        for h in (current.get("hours") if isinstance(current, dict) else current) or []
        if isinstance(h, dict) and h.get("day_of_week") != weekday
    ]
    return {
        "session": session,
        "outlet_id": outlet,
        "endpoint": f"PUT /api/restaurants/{outlet}/opening_hours",
        "payload": {"hours": hours},
    }


def _talabat_set_day(raw: Any, *, dh_day: int, opens: str, closes: str) -> Any:
    """Copy the VTS calendars payload with `dh_day`'s window replaced."""
    if not isinstance(raw, dict):
        return {"calendars": []}
    calendars = [dict(c) for c in (raw.get("calendars") or []) if isinstance(c, dict)]
    if not calendars:
        calendars = [{"name": "Normal", "schedule": {"openingTimesByDay": []}}]
    target = next((c for c in calendars if c.get("name") == "Normal"), calendars[0])
    schedule = dict(target.get("schedule") or {})
    by_day = [
        dict(e)
        for e in (schedule.get("openingTimesByDay") or [])
        if isinstance(e, dict) and e.get("day") != dh_day
    ]
    by_day.append(
        {
            "day": dh_day,
            "openingTimes": [{"from": _minutes(opens), "to": _minutes(closes)}],
        }
    )
    by_day.sort(key=lambda e: int(e.get("day") or 0))
    schedule["openingTimesByDay"] = by_day
    target["schedule"] = schedule
    return {"calendars": calendars}


def _talabat_clear_day(raw: Any, *, dh_day: int) -> Any:
    if not isinstance(raw, dict):
        return {"calendars": []}
    calendars = [dict(c) for c in (raw.get("calendars") or []) if isinstance(c, dict)]
    for cal in calendars:
        schedule = dict(cal.get("schedule") or {})
        schedule["openingTimesByDay"] = [
            e
            for e in (schedule.get("openingTimesByDay") or [])
            if not (isinstance(e, dict) and e.get("day") == dh_day)
        ]
        cal["schedule"] = schedule
    return {"calendars": calendars}


async def _talabat_push(
    db: AsyncSession, branch: Any, *, opens: str, closes: str, weekday: int
) -> dict[str, Any]:
    from app.services.providers import talabat_provider as tp

    session = await _load_session(db, "talabat")
    row = await _branch_map(db, "talabat", branch.id)
    vendor = row.external_outlet_id
    current = await tp.provider.get_delivery_calendars(session, vendor)
    dh_day = (weekday - 1) % 7  # MM 0=Sun → DH 6; MM 1=Mon → DH 0
    calendars = _talabat_set_day(current, dh_day=dh_day, opens=opens, closes=closes)
    return {
        "session": session,
        "vendor": vendor,
        "endpoint": f"PUT vts .../vendor/TB_AE;{vendor}/calendars/DELIVERY",
        "calendars": calendars,
        "status": "OPEN",
        "payload": {"calendars": calendars, "status": "OPEN"},
    }


async def _talabat_close(
    db: AsyncSession, branch: Any, *, weekday: int
) -> dict[str, Any]:
    # Holiday close is Partner `CLOSED_TODAY`, not a calendar rewrite — weekday
    # is unused by design (same signature as the other closers).
    _ = weekday
    session = await _load_session(db, "talabat")
    row = await _branch_map(db, "talabat", branch.id)
    vendor = row.external_outlet_id
    return {
        "session": session,
        "vendor": vendor,
        "endpoint": f"PUT vendor-api .../vendors/{vendor}/status",
        "status": "CLOSED_TODAY",
        "payload": {"status": "CLOSED_TODAY"},
    }


def _noon_day(mm_weekday: int) -> int:
    """MM 0=Sun … 6=Sat → noon 0=Mon … 6=Sun."""
    return (mm_weekday - 1) % 7


def _noon_schedule_with_day(
    details: Any, *, noon_day: int, opens: str, closes: str, closed: bool
) -> dict[str, Any]:
    data = details.get("data") if isinstance(details, dict) else details
    schedule = (
        dict((data or {}).get("schedule") or {}) if isinstance(data, dict) else {}
    )
    periods = dict(schedule.get("periods") or {}) if isinstance(schedule, dict) else {}
    rebuilt: dict[str, list] = {}
    for key, ranges in periods.items():
        kept_days = [
            p.strip()
            for p in str(key).split(",")
            if p.strip().isdigit() and int(p.strip()) != noon_day
        ]
        if kept_days:
            rebuilt[",".join(kept_days)] = list(ranges or [])
    if not closed:
        rebuilt[str(noon_day)] = [[_hhmmss(opens), _hhmmss(closes)]]
    schedule["periods"] = rebuilt
    return schedule


async def _noon_push(
    db: AsyncSession, branch: Any, *, opens: str, closes: str, weekday: int
) -> dict[str, Any]:
    from app.services.providers import noon_provider as np

    session = await _load_session(db, "noon")
    row = await _branch_map(db, "noon", branch.id)
    outlet = row.external_outlet_id
    details = await np.provider.get_outlet_details(session, outlet)
    schedule = _noon_schedule_with_day(
        details, noon_day=_noon_day(weekday), opens=opens, closes=closes, closed=False
    )
    return {
        "session": session,
        "outlet_code": outlet,
        "endpoint": "POST /_food-restaurant/restaurant/outlet/save",
        "schedule": schedule,
        "payload": {"outletCode": outlet, "schedule": schedule},
    }


async def _noon_close(db: AsyncSession, branch: Any, *, weekday: int) -> dict[str, Any]:
    from app.services.providers import noon_provider as np

    session = await _load_session(db, "noon")
    row = await _branch_map(db, "noon", branch.id)
    outlet = row.external_outlet_id
    details = await np.provider.get_outlet_details(session, outlet)
    schedule = _noon_schedule_with_day(
        details,
        noon_day=_noon_day(weekday),
        opens="00:00",
        closes="00:00",
        closed=True,
    )
    return {
        "session": session,
        "outlet_code": outlet,
        "endpoint": "POST /_food-restaurant/restaurant/outlet/save",
        "schedule": schedule,
        "payload": {"outletCode": outlet, "schedule": schedule},
    }


def _careem_rows_for_day(
    rows: Any, *, day: int, opens: str, closes: str, closed: bool
) -> list[dict[str, Any]]:
    current = (
        [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    )
    found = False
    out: list[dict[str, Any]] = []
    for row in current:
        if row.get("day") != day:
            out.append(row)
            continue
        found = True
        if closed:
            out.append({**row, "active": 0, "shifts": []})
        else:
            out.append(
                {
                    **row,
                    "active": 1,
                    "shifts": [
                        {
                            "start_time": _hhmmss(opens),
                            "end_time": _hhmmss(closes),
                        }
                    ],
                }
            )
    if not found:
        out.append(
            {
                "day": day,
                "active": 0 if closed else 1,
                "shifts": (
                    []
                    if closed
                    else [
                        {
                            "start_time": _hhmmss(opens),
                            "end_time": _hhmmss(closes),
                        }
                    ]
                ),
            }
        )
    out.sort(key=lambda r: int(r.get("day") or 0))
    return out


async def _careem_push(
    db: AsyncSession, branch: Any, *, opens: str, closes: str, weekday: int
) -> dict[str, Any]:
    from app.services.aggregators.menu_readers import _careem_ids
    from app.services.providers import careem_provider as cp

    session = await _load_session(db, "careem")
    try:
        company, brand, outlet = await _careem_ids(db, branch.id)
    except AggregatorUnavailableError as exc:
        raise HoursWriteUnsupported(str(exc)) from exc
    current = await cp.provider.get_operational_hours(session, company, brand, outlet)
    rows = _careem_rows_for_day(
        current, day=weekday + 1, opens=opens, closes=closes, closed=False
    )
    return {
        "session": session,
        "company": company,
        "brand": brand,
        "outlet": outlet,
        "endpoint": "PUT .../food-outlet-operational-hours",
        "rows": rows,
        "payload": rows,
    }


async def _careem_close(
    db: AsyncSession, branch: Any, *, weekday: int
) -> dict[str, Any]:
    from app.services.aggregators.menu_readers import _careem_ids
    from app.services.providers import careem_provider as cp

    session = await _load_session(db, "careem")
    try:
        company, brand, outlet = await _careem_ids(db, branch.id)
    except AggregatorUnavailableError as exc:
        raise HoursWriteUnsupported(str(exc)) from exc
    current = await cp.provider.get_operational_hours(session, company, brand, outlet)
    rows = _careem_rows_for_day(
        current, day=weekday + 1, opens="00:00", closes="00:00", closed=True
    )
    return {
        "session": session,
        "company": company,
        "brand": brand,
        "outlet": outlet,
        "endpoint": "PUT .../food-outlet-operational-hours",
        "rows": rows,
        "payload": rows,
    }


# ── full-weekly builders (rebuild all seven days from MM, keep the envelope) ──


async def _deliveroo_push_weekly(
    db: AsyncSession, branch: Any, *, weekly: dict[int, tuple[str, str]]
) -> dict[str, Any]:
    from app.services.providers import deliveroo_provider as dp

    session = await _load_session(db, "deliveroo")
    row = await _branch_map(db, "deliveroo", branch.id)
    outlet = row.external_outlet_id
    # Deliveroo's payload is only the hours list (no envelope to preserve); the
    # read confirms the outlet/session before a live PUT would replace it.
    await dp.provider.get_opening_hours(session, outlet)
    hours = [
        {
            "day_of_week": wd,  # Deliveroo day_of_week == MM weekday
            "local_start_time": _hhmmss(win[0]),
            "local_end_time": _hhmmss(win[1]),
        }
        for wd in range(7)
        if (win := weekly.get(wd)) is not None
    ]
    return {
        "session": session,
        "outlet_id": outlet,
        "endpoint": f"PUT /api/restaurants/{outlet}/opening_hours",
        "payload": {"hours": hours},
    }


def _talabat_set_weekly(raw: Any, *, weekly: dict[int, tuple[str, str]]) -> Any:
    """Rebuild the VTS `Normal` calendar's whole week, keeping other calendars."""
    if not isinstance(raw, dict):
        return {"calendars": []}
    calendars = [dict(c) for c in (raw.get("calendars") or []) if isinstance(c, dict)]
    if not calendars:
        calendars = [{"name": "Normal", "schedule": {"openingTimesByDay": []}}]
    target = next((c for c in calendars if c.get("name") == "Normal"), calendars[0])
    schedule = dict(target.get("schedule") or {})
    by_day = [
        {
            "day": (wd - 1) % 7,  # MM 0=Sun → DH 6; MM 1=Mon → DH 0
            "openingTimes": [{"from": _minutes(win[0]), "to": _minutes(win[1])}],
        }
        for wd in range(7)
        if (win := weekly.get(wd)) is not None
    ]
    by_day.sort(key=lambda e: int(e.get("day") or 0))
    schedule["openingTimesByDay"] = by_day
    target["schedule"] = schedule
    return {"calendars": calendars}


async def _talabat_push_weekly(
    db: AsyncSession, branch: Any, *, weekly: dict[int, tuple[str, str]]
) -> dict[str, Any]:
    from app.services.providers import talabat_provider as tp

    session = await _load_session(db, "talabat")
    row = await _branch_map(db, "talabat", branch.id)
    vendor = row.external_outlet_id
    current = await tp.provider.get_delivery_calendars(session, vendor)
    calendars = _talabat_set_weekly(current, weekly=weekly)
    # No `status`: the recurring calendar is the schedule; a transient
    # OPEN/CLOSED_TODAY override belongs to the holiday path, not the weekly
    # mirror, which must not undo a manager's same-day close.
    return {
        "session": session,
        "vendor": vendor,
        "endpoint": f"PUT vts .../vendor/TB_AE;{vendor}/calendars/DELIVERY",
        "calendars": calendars,
        "payload": {"calendars": calendars},
    }


def _noon_schedule_weekly(details: Any, *, weekly: dict[int, tuple[str, str]]) -> dict:
    data = details.get("data") if isinstance(details, dict) else details
    schedule = (
        dict((data or {}).get("schedule") or {}) if isinstance(data, dict) else {}
    )
    periods: dict[str, list] = {}
    for wd in range(7):
        win = weekly.get(wd)
        if win is None:  # closed day → day-index simply absent from periods
            continue
        noon_day = (wd - 1) % 7  # MM 0=Sun … 6=Sat → noon 0=Mon … 6=Sun
        periods[str(noon_day)] = [[_hhmmss(win[0]), _hhmmss(win[1])]]
    schedule["periods"] = periods
    return schedule


async def _noon_push_weekly(
    db: AsyncSession, branch: Any, *, weekly: dict[int, tuple[str, str]]
) -> dict[str, Any]:
    from app.services.providers import noon_provider as np

    session = await _load_session(db, "noon")
    row = await _branch_map(db, "noon", branch.id)
    outlet = row.external_outlet_id
    details = await np.provider.get_outlet_details(session, outlet)
    schedule = _noon_schedule_weekly(details, weekly=weekly)
    return {
        "session": session,
        "outlet_code": outlet,
        "endpoint": "POST /_food-restaurant/restaurant/outlet/save",
        "schedule": schedule,
        "payload": {"outletCode": outlet, "schedule": schedule},
    }


def _careem_rows_weekly(
    rows: Any, *, weekly: dict[int, tuple[str, str]]
) -> list[dict[str, Any]]:
    """All seven Careem rows: open → active:1 + shift, closed → active:0 + []."""
    current: dict[int, dict[str, Any]] = {}
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("day") is not None:
                current[int(r["day"])] = dict(r)
    out: list[dict[str, Any]] = []
    for wd in range(7):
        day = wd + 1  # Careem day = MM weekday + 1 (Sun=1 … Sat=7)
        base = current.get(day, {"day": day})
        win = weekly.get(wd)
        if win is None:
            out.append({**base, "day": day, "active": 0, "shifts": []})
        else:
            out.append(
                {
                    **base,
                    "day": day,
                    "active": 1,
                    "shifts": [
                        {"start_time": _hhmmss(win[0]), "end_time": _hhmmss(win[1])}
                    ],
                }
            )
    out.sort(key=lambda r: int(r.get("day") or 0))
    return out


async def _careem_push_weekly(
    db: AsyncSession, branch: Any, *, weekly: dict[int, tuple[str, str]]
) -> dict[str, Any]:
    from app.services.aggregators.menu_readers import _careem_ids
    from app.services.providers import careem_provider as cp

    session = await _load_session(db, "careem")
    try:
        company, brand, outlet = await _careem_ids(db, branch.id)
    except AggregatorUnavailableError as exc:
        raise HoursWriteUnsupported(str(exc)) from exc
    current = await cp.provider.get_operational_hours(session, company, brand, outlet)
    rows = _careem_rows_weekly(current, weekly=weekly)
    return {
        "session": session,
        "company": company,
        "brand": brand,
        "outlet": outlet,
        "endpoint": "PUT .../food-outlet-operational-hours",
        "rows": rows,
        "payload": rows,
    }


_PUSHERS = {
    "deliveroo": _deliveroo_push,
    "talabat": _talabat_push,
    "noon": _noon_push,
    "careem": _careem_push,
}

_WEEKLY_PUSHERS = {
    "deliveroo": _deliveroo_push_weekly,
    "talabat": _talabat_push_weekly,
    "noon": _noon_push_weekly,
    "careem": _careem_push_weekly,
}

_CLOSERS = {
    "deliveroo": _deliveroo_close,
    "talabat": _talabat_close,
    "noon": _noon_close,
    "careem": _careem_close,
}
