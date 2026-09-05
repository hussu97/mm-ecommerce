"""Hours writers — supported channels, keeta worker-only, dry-run default."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.aggregators import hours_writers as hw


def test_supported_channels_is_working_httpx_only():
    # All four httpx writers are live (deliveroo's write was fixed 2026-09-05:
    # PUT the drn_id with a bare array); keeta is worker-only, never here.
    assert hw.supported_channels() == frozenset(
        {"talabat", "noon", "careem", "deliveroo"}
    )
    assert "keeta" not in hw.supported_channels()


@pytest.mark.asyncio
async def test_keeta_raises_worker_reason(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    with pytest.raises(hw.HoursWriteUnsupported, match="headed worker"):
        await hw.push_hours(
            None, channel="keeta", branch=object(), opens="08:00", closes="17:00"
        )


@pytest.mark.asyncio
async def test_flag_off_raises(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", False)
    with pytest.raises(hw.HoursWriteUnsupported, match="CATALOG_SYNC_ENABLED"):
        await hw.push_hours(
            None,
            channel="noon",
            branch=object(),
            opens="08:00",
            closes="17:00",
        )


@pytest.mark.asyncio
async def test_dry_run_does_not_execute(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)

    async def fake_mapper(_db, _branch, **_kw):
        return {
            "endpoint": "PUT /api/restaurants/1/opening_hours",
            "payload": {"hours": []},
            "session": object(),
        }

    executed: list[tuple] = []

    async def fake_execute(channel, plan):
        executed.append((channel, plan.get("op")))

    monkeypatch.setattr(hw, "_PUSHERS", {**hw._PUSHERS, "noon": fake_mapper})
    monkeypatch.setattr(hw, "_execute", fake_execute)

    branch = SimpleNamespace(id="b1")
    plan = await hw.push_hours(
        object(),
        channel="noon",
        branch=branch,
        opens="08:00",
        closes="17:00",
    )
    assert plan["dry_run"] is True
    assert plan["op"] == "push_hours"
    assert executed == []

    live = await hw.push_hours(
        object(),
        channel="noon",
        branch=branch,
        opens="08:00",
        closes="17:00",
        dry_run=False,
    )
    assert live["dry_run"] is False
    assert executed == [("noon", "push_hours")]


@pytest.mark.asyncio
async def test_close_outlet_dry_run_does_not_execute(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)

    async def fake_closer(_db, _branch, **_kw):
        return {
            "endpoint": "PUT .../vendors/1/status",
            "status": "CLOSED_TODAY",
            "payload": {"status": "CLOSED_TODAY"},
            "session": object(),
        }

    executed: list[str] = []

    async def fake_execute(channel, _plan):
        executed.append(channel)

    monkeypatch.setattr(hw, "_CLOSERS", {**hw._CLOSERS, "talabat": fake_closer})
    monkeypatch.setattr(hw, "_execute", fake_execute)

    plan = await hw.close_outlet(
        object(), channel="talabat", branch=SimpleNamespace(id="b1")
    )
    assert plan["dry_run"] is True
    assert plan["op"] == "close_outlet"
    assert executed == []


# ── full-weekly writers ───────────────────────────────────────────────────────

# MM weekday 0=Sun, 1=Mon; days 2..6 absent = closed.
_WEEKLY = {0: ("08:00", "22:00"), 1: ("09:00", "23:00")}


@pytest.mark.asyncio
async def test_push_weekly_hours_dry_run_then_live(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)

    async def fake_mapper(_db, _branch, *, weekly):
        return {
            "endpoint": "POST /_food-restaurant/restaurant/outlet/save",
            "schedule": {"periods": {}},
            "session": object(),
        }

    executed: list[tuple] = []

    async def fake_execute(channel, plan):
        executed.append((channel, plan.get("op")))

    monkeypatch.setattr(
        hw, "_WEEKLY_PUSHERS", {**hw._WEEKLY_PUSHERS, "noon": fake_mapper}
    )
    monkeypatch.setattr(hw, "_execute", fake_execute)

    branch = SimpleNamespace(id="b1")
    plan = await hw.push_weekly_hours(
        object(), channel="noon", branch=branch, weekly=_WEEKLY
    )
    assert plan["dry_run"] is True
    assert plan["op"] == "push_weekly_hours"
    # Every weekday present in the summary; closed days say so.
    assert plan["weekly"]["0"] == "08:00-22:00"
    assert plan["weekly"]["2"] == "closed"
    assert set(plan["weekly"]) == {str(i) for i in range(7)}
    assert executed == []

    live = await hw.push_weekly_hours(
        object(), channel="noon", branch=branch, weekly=_WEEKLY, dry_run=False
    )
    assert live["dry_run"] is False
    assert executed == [("noon", "push_weekly_hours")]


def test_talabat_weekly_builder_returns_single_normal_calendar():
    # The write takes ONE calendar object (the Normal one), not a {calendars:[...]}
    # wrapper; the alternative "Special" calendar is left out (untouched on the
    # portal).
    raw = {
        "calendars": [
            {
                "id": "1",
                "name": "Normal",
                "comment": "",
                "schedule": {
                    "type": "REGULAR",
                    "openingTimesByDay": [{"day": 3, "openingTimes": []}],
                },
            },
            {"name": "Special", "schedule": {"openingTimesByDay": [{"day": 1}]}},
        ]
    }
    cal = hw._talabat_normal_calendar_with_week(raw, weekly=_WEEKLY)
    assert cal["name"] == "Normal" and cal["id"] == "1"  # full object, not a wrapper
    assert cal["schedule"]["type"] == "REGULAR"  # envelope preserved
    by_day = cal["schedule"]["openingTimesByDay"]
    # MM Sun(0)→DH 6, Mon(1)→DH 0; closed weekdays absent; sorted by day.
    assert [e["day"] for e in by_day] == [0, 6]
    assert by_day[0]["openingTimes"] == [{"from": 540, "to": 1380}]  # Mon 09:00-23:00
    assert by_day[1]["openingTimes"] == [{"from": 480, "to": 1320}]  # Sun 08:00-22:00


def test_noon_weekly_builder_maps_days_and_keeps_envelope():
    details = {"data": {"schedule": {"periods": {"0": [["old"]]}, "tz": "Asia/Dubai"}}}
    schedule = hw._noon_schedule_weekly(details, weekly=_WEEKLY)
    # MM Sun(0)→noon 6, Mon(1)→noon 0; closed days absent.
    assert schedule["periods"] == {
        "6": [["08:00:00", "22:00:00"]],
        "0": [["09:00:00", "23:00:00"]],
    }
    assert schedule["tz"] == "Asia/Dubai"  # sibling schedule key preserved


def test_deliveroo_weekly_builder_bare_array_open_days_only():
    # A bare array (no {"hours":...} wrapper is applied here — that lives in the
    # plan envelope); day_of_week == MM weekday directly (0=Sun); closed weekdays
    # are omitted (Deliveroo full-replace closes any absent day).
    rows = hw._deliveroo_hours_weekly(_WEEKLY)
    assert isinstance(rows, list)
    assert [r["day_of_week"] for r in rows] == [0, 1]  # Sun, Mon open; 2..6 closed
    assert rows[0] == {
        "day_of_week": 0,
        "local_start_time": "08:00:00",
        "local_end_time": "22:00:00",
    }
    assert rows[1]["local_start_time"] == "09:00:00"  # Mon 09:00-23:00
    assert rows[1]["local_end_time"] == "23:00:00"


def test_careem_weekly_builder_all_seven_rows():
    rows = [{"day": 1, "active": 0, "shifts": [], "extra": "keep"}]
    out = hw._careem_rows_weekly(rows, weekly=_WEEKLY)
    assert [r["day"] for r in out] == [1, 2, 3, 4, 5, 6, 7]
    # Careem day = MM weekday + 1; Sun→1 open, Mon→2 open, rest closed.
    sun = out[0]
    assert sun["active"] == 1 and sun["shifts"] == [
        {"start_time": "08:00:00", "end_time": "22:00:00"}
    ]
    assert sun["extra"] == "keep"  # existing row fields preserved
    assert out[1]["active"] == 1  # Mon
    assert all(r["active"] == 0 and r["shifts"] == [] for r in out[2:])


@pytest.mark.asyncio
async def test_deliveroo_execute_verifies_persistence(monkeypatch):
    # A live restaurant persists the write (read-back matches) -> no raise; a non-live
    # outlet answers 204 but drops it (read-back empty) -> HoursWriteUnsupported so the
    # sync skips honestly rather than recording a false "completed".
    from app.services.providers import deliveroo_provider as dp

    want = [
        {"day_of_week": 0, "local_start_time": "08:00:00", "local_end_time": "22:00:00"}
    ]
    plan = {"session": object(), "outlet_id": "693359", "payload": {"hours": want}}

    class _Persists:
        async def put_opening_hours(self, *_a):
            pass

        async def get_opening_hours(self, *_a):
            return {"hours": want}

        async def restaurant_status(self, *_a):
            return "CLOSED"

    monkeypatch.setattr(dp, "provider", _Persists())
    await hw._execute("deliveroo", plan)  # matches -> no raise

    class _Drops(_Persists):
        async def get_opening_hours(self, *_a):
            return {"hours": []}

        async def restaurant_status(self, *_a):
            return "READY_TO_OPEN"

    monkeypatch.setattr(dp, "provider", _Drops())
    with pytest.raises(hw.HoursWriteUnsupported, match="did not persist"):
        await hw._execute("deliveroo", plan)
