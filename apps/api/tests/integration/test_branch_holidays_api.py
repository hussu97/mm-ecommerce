"""
Branch closures, from the outside.

These rows are not decoration on a settings page — `delivery_promise` reads
them, so what this endpoint accepts decides what customers are quoted. Two
things therefore have to hold at the door rather than downstream:

* the date is `YYYY-MM-DD`. These strings are compared against
  `date.isoformat()` output, so one written any other way closes the branch on
  no day at all — silently, with no error anywhere and a promise that reads
  perfectly normal.
* one row per branch per date. Two rows saying the shop is shut is two records
  of one fact, and the pair is free to disagree once somebody edits one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.deps import get_current_active_user
from app.models.branch import Branch, BranchHoliday
from app.models.user import User

BRANCH_ID = uuid.uuid4()


def _branch() -> Branch:
    return Branch(
        id=BRANCH_ID,
        name="Sharjah Central",
        reference="K001",
    )


#: The stamps a real INSERT would supply. Set by hand because these tests never
#: reach a database, and `BranchHolidayResponse` declares all three as
#: non-optional — a row missing them fails serialisation rather than the
#: assertion, which says nothing about the route.
def _stamp(holiday: BranchHoliday) -> BranchHoliday:
    now = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    if holiday.id is None:
        holiday.id = uuid.uuid4()
    holiday.created_at = now
    holiday.updated_at = now
    return holiday


def _holiday(day: str, name: str = "Eid al-Fitr") -> BranchHoliday:
    return _stamp(
        BranchHoliday(
            id=uuid.uuid4(),
            branch_id=BRANCH_ID,
            holiday_date=day,
            name=name,
            note=None,
        )
    )


@pytest.fixture
def admin_client(client):
    from app.main import app

    app.dependency_overrides[get_current_active_user] = lambda: User(
        email="admin@example.com", is_admin=True, is_active=True
    )
    yield client
    app.dependency_overrides.pop(get_current_active_user, None)


@pytest.fixture
def quiet_audit(monkeypatch):
    from app.services import audit_service

    monkeypatch.setattr(audit_service, "log_action", AsyncMock(return_value=None))


def _rows(mock_db, rows=(), *, one=None, found=()):
    """
    One result object answering the three shapes these routes read.

    `found` feeds `crud_service.get_or_404`, which goes through
    `scalars().unique().one_or_none()` — a list because the delete route looks
    up the closure and then its branch, and the two answers differ. `one` feeds
    the duplicate-date check, and `rows` the listing.
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    result.scalars.return_value.unique.return_value.one_or_none = MagicMock(
        side_effect=list(found)
    )
    result.scalar_one_or_none = MagicMock(return_value=one)
    mock_db.execute = AsyncMock(return_value=result)
    # Stands in for the INSERT: a real one fills the id and the timestamps, and
    # the response model requires all three.
    mock_db.add = MagicMock(side_effect=_stamp)


class TestListing:
    async def test_it_lists_this_branch_s_closures(self, admin_client, mock_db):
        _rows(mock_db, [_holiday("2026-12-02", "National Day")], found=[_branch()])

        response = await admin_client.get(f"/api/v1/branches/{BRANCH_ID}/holidays")

        assert response.status_code == 200
        assert response.json()[0]["holiday_date"] == "2026-12-02"
        assert response.json()[0]["name"] == "National Day"


class TestClosingADay:
    async def test_it_closes_a_day(self, admin_client, mock_db, quiet_audit):
        _rows(mock_db, one=None, found=[_branch()])
        mock_db.refresh = AsyncMock(return_value=None)

        response = await admin_client.post(
            f"/api/v1/branches/{BRANCH_ID}/holidays",
            json={"holiday_date": "2026-12-02", "name": "National Day"},
        )

        assert response.status_code == 201
        assert mock_db.add.called
        written = mock_db.add.call_args[0][0]
        assert written.holiday_date == "2026-12-02"
        assert written.branch_id == BRANCH_ID

    @pytest.mark.parametrize(
        "bad", ["2/12/2026", "2026-12-2", "Dec 2 2026", "2026-12-02T00:00:00", ""]
    )
    async def test_a_date_of_any_other_shape_is_refused(
        self, admin_client, mock_db, quiet_audit, bad
    ):
        """
        The silent failure this guards. A row that is not `YYYY-MM-DD` never
        matches the date the promise compares it against, so the branch stays
        open, the closure appears in the admin list, and nothing anywhere says
        the two disagree.
        """
        _rows(mock_db, one=None, found=[_branch()])

        response = await admin_client.post(
            f"/api/v1/branches/{BRANCH_ID}/holidays",
            json={"holiday_date": bad, "name": "Eid"},
        )

        assert response.status_code == 422

    async def test_a_closure_needs_a_name(self, admin_client, mock_db, quiet_audit):
        """
        Somebody will one day ask why a week of orders quoted three days out.
        A dated row with no occasion on it cannot answer that.
        """
        _rows(mock_db, one=None, found=[_branch()])

        response = await admin_client.post(
            f"/api/v1/branches/{BRANCH_ID}/holidays",
            json={"holiday_date": "2026-12-02", "name": ""},
        )

        assert response.status_code == 422

    async def test_the_same_day_twice_is_a_conflict(
        self, admin_client, mock_db, quiet_audit
    ):
        _rows(mock_db, one=_holiday("2026-12-02"), found=[_branch()])

        response = await admin_client.post(
            f"/api/v1/branches/{BRANCH_ID}/holidays",
            json={"holiday_date": "2026-12-02", "name": "National Day"},
        )

        assert response.status_code == 409
        assert "2026-12-02" in response.json()["detail"]

    async def test_there_is_no_way_to_close_part_of_a_day(
        self, admin_client, mock_db, quiet_audit
    ):
        """
        Whole days, by design. A branch opening late is a change to its trading
        hours, which are fields on the branch; hours here would be a second
        answer to that question, free to disagree with the first. Extra keys
        are ignored rather than rejected, so the property is that they do not
        reach the row.
        """
        _rows(mock_db, one=None, found=[_branch()])
        mock_db.refresh = AsyncMock(return_value=None)

        response = await admin_client.post(
            f"/api/v1/branches/{BRANCH_ID}/holidays",
            json={
                "holiday_date": "2026-12-02",
                "name": "Half day",
                "opening_from": "14:00",
            },
        )

        assert response.status_code == 201
        assert not hasattr(mock_db.add.call_args[0][0], "opening_from")


class TestReopening:
    async def test_removing_a_closure_deletes_the_row(
        self, admin_client, mock_db, quiet_audit
    ):
        """
        A hard delete, unlike most of this router. A `deleted_at`-shaped
        closure would be a row saying the shop is shut that the promise has to
        be trusted to ignore — one more thing to get wrong than not having it.
        """
        holiday = _holiday("2026-12-02")
        _rows(mock_db, found=[holiday, _branch()])
        mock_db.delete = AsyncMock(return_value=None)

        response = await admin_client.delete(f"/api/v1/branches/holidays/{holiday.id}")

        assert response.status_code == 204
        mock_db.delete.assert_awaited_once_with(holiday)
