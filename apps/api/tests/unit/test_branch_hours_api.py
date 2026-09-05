"""The API seams the hours sync adds: the Foodics branch-hours envelope and the
Keeta weekly-map the DB-less worker pulls (seconds-from-midnight, Sunday-first,
closed = [{0,0,1}])."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.api.v1 import aggregators as agg
from app.services.providers.foodics_provider import FoodicsClient


@pytest.mark.asyncio
async def test_foodics_set_branch_hours_envelope():
    client = FoodicsClient()
    client._call = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
    await client.set_branch_hours("fb1", opening_from="09:00", opening_to="23:00")
    client._call.assert_awaited_once()
    args, kwargs = client._call.call_args
    assert args[0] == "PUT"
    body = kwargs["json_body"]
    assert body["url"] == "/branches/fb1"
    assert body["payload"] == {"opening_from": "09:00", "opening_to": "23:00"}


def test_hhmm_to_seconds_and_day_end():
    assert agg._hhmm_to_seconds("00:00") == 0
    assert agg._hhmm_to_seconds("08:15") == 8 * 3600 + 15 * 60
    # 23:59 is stored as the day-end 86400, matching Keeta's editor.
    assert agg._hhmm_to_seconds("23:59") == 86400


def test_keeta_weekly_from_schedule_maps_and_closes():
    # MM 0=Sun open, 1=Mon open; 2..6 closed (absent from the schedule dict).
    sched = {0: ("09:00", "23:00"), 1: ("08:00", "22:00")}
    weekly = agg._keeta_weekly_from_schedule(sched)
    assert set(weekly) == {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}
    assert weekly["sun"] == [{"startTime": 32400, "endTime": 82800, "option": 1}]
    assert weekly["mon"] == [{"startTime": 28800, "endTime": 79200, "option": 1}]
    # Every closed weekday is [{0,0,1}], never absent.
    for key in ("tue", "wed", "thu", "fri", "sat"):
        assert weekly[key] == [{"startTime": 0, "endTime": 0, "option": 1}]
