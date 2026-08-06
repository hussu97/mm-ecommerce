"""
Custom-cake capacity, against a real database.

The whole point of the custom-order diary is that a date cannot be promised
twice. That is a counting question over rows the database owns — statuses that
hold a slot, statuses that release one, and a count compared against a setting —
so mocking the session would only prove the mock counts.

The case that matters most here is the revive: cancelling frees a slot, the slot
gets taken by somebody else, and re-opening the cancelled order must then be
refused. That is the sequence a shop actually walks into, and it is invisible to
any test that checks `book` alone.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import BadRequestError, ConflictError
from app.models import BusinessSettings, CustomOrder, CustomOrderBlackout
from app.services import custom_order_service as svc

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)

MARKER = "pytest-custom-"


@pytest.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        yield db
        await db.rollback()
        for row in (
            (
                await db.execute(
                    select(CustomOrder).where(
                        CustomOrder.customer_name.like(f"{MARKER}%")
                    )
                )
            )
            .scalars()
            .all()
        ):
            await db.delete(row)
        for row in (await db.execute(select(CustomOrderBlackout))).scalars().all():
            await db.delete(row)
        await db.commit()
    await engine.dispose()


@pytest.fixture
async def one_per_day(session):
    """The default capacity, stated explicitly so the test does not assume it."""
    settings = (
        (await session.execute(select(BusinessSettings).limit(1))).scalars().first()
    )
    if settings is None:
        settings = BusinessSettings()
        session.add(settings)
    settings.custom_orders_per_day = 1
    settings.custom_order_lead_days = 3
    await session.flush()
    return settings


def _far_future() -> date:
    """Well clear of the lead time, and of anything a real booking would use."""
    return date.today() + timedelta(days=120)


async def _book(session, day: date, who: str, **kwargs) -> CustomOrder:
    return await svc.book(
        session,
        due_date=day,
        customer_name=f"{MARKER}{who}",
        description="Two-tier pistachio with gold leaf",
        source="instagram",
        **kwargs,
    )


class TestCapacity:
    async def test_a_full_date_refuses_the_next_booking(self, session, one_per_day):
        day = _far_future()
        await _book(session, day, "aisha")
        await session.flush()

        with pytest.raises(ConflictError, match="fully booked"):
            await _book(session, day, "omar")

    async def test_cancelling_frees_the_slot(self, session, one_per_day):
        day = _far_future()
        first = await _book(session, day, "aisha")
        await session.flush()

        await svc.set_status(session, first.id, "cancelled")
        availability = await svc.availability_for(session, day)
        assert availability.remaining == 1

        second = await _book(session, day, "omar")
        assert second.due_date == day

    async def test_reviving_into_a_filled_date_is_refused(self, session, one_per_day):
        """
        The sequence a shop walks into: cancel, someone else takes the date,
        then the first customer changes their mind back.
        """
        day = _far_future()
        first = await _book(session, day, "aisha")
        await session.flush()
        await svc.set_status(session, first.id, "cancelled")
        await _book(session, day, "omar")
        await session.flush()

        with pytest.raises(ConflictError, match="filled since"):
            await svc.set_status(session, first.id, "confirmed")

    async def test_an_enquiry_still_holds_the_slot(self, session, one_per_day):
        """
        A maybe has to be baked if it becomes a yes. Releasing the slot on
        anything short of confirmation is how a date gets promised twice.
        """
        day = _far_future()
        await _book(session, day, "aisha", status="enquiry")
        await session.flush()

        availability = await svc.availability_for(session, day)
        assert availability.remaining == 0

    async def test_capacity_is_configurable(self, session, one_per_day):
        one_per_day.custom_orders_per_day = 2
        await session.flush()

        day = _far_future()
        await _book(session, day, "aisha")
        await _book(session, day, "omar")
        await session.flush()

        with pytest.raises(ConflictError):
            await _book(session, day, "layla")


class TestLeadTimeAndBlackouts:
    async def test_a_date_inside_the_lead_time_is_refused(self, session, one_per_day):
        with pytest.raises(BadRequestError, match="notice"):
            await _book(session, date.today(), "aisha")

    async def test_admins_may_record_something_that_already_happened(
        self, session, one_per_day
    ):
        """
        An Instagram order is often typed in after the fact. Refusing it would
        push it back into the chat thread the calendar is meant to replace.
        """
        yesterday = date.today() - timedelta(days=1)
        booked = await _book(session, yesterday, "aisha", allow_past=True)
        assert booked.due_date == yesterday

    async def test_a_blacked_out_date_takes_nothing(self, session, one_per_day):
        day = _far_future()
        session.add(CustomOrderBlackout(blackout_date=day, reason="Eid"))
        await session.flush()

        with pytest.raises(ConflictError, match="Eid"):
            await _book(session, day, "aisha")

    async def test_availability_range_reports_each_day(self, session, one_per_day):
        start = _far_future()
        await _book(session, start, "aisha")
        await session.flush()

        days = await svc.availability_range(session, start, start + timedelta(days=2))
        assert [d.remaining for d in days] == [0, 1, 1]
        assert [d.day for d in days] == [
            start,
            start + timedelta(days=1),
            start + timedelta(days=2),
        ]
