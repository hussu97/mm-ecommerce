"""
Daily-sales idempotency runs off an explicit journal, not an email-subject LIKE.

F-POS-18, two failures the old subject-`LIKE` guard had:
  1. a manual admin send wrote an `email_logs` row with the date in its subject
     and so suppressed that night's AUTOMATIC send;
  2. a run where one recipient failed still left a `sent` row and was never
     retried.

The fix journals a business date in `daily_sales_sends` ONLY when the automatic
send reached every recipient. These two cases prove a manual `email_logs` row no
longer suppresses the nightly send, and a partial recipient failure leaves the
day due for the next tick. Against a real Postgres because the journal is a table.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch
from app.models.daily_sales_send import DailySalesSend
from app.models.email_log import EmailLog
from app.models.inventory import Warehouse
from app.services.pos import daily_sales_email as dse

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-daily-idempotency"
_DUBAI = ZoneInfo("Asia/Dubai")


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def test_a_manual_send_does_not_suppress_the_nightly_send(engine):
    """An `email_logs` row carrying the date — what a manual send writes — must
    NOT make the automatic send think the day is done. Only the journal does."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    date = "2026-07-20"
    async with Session() as db:
        await db.execute(
            delete(DailySalesSend).where(DailySalesSend.business_date == date)
        )
        # Simulate a manual send: an email_logs row whose subject carries the date.
        db.add(
            EmailLog(
                template=dse._TEMPLATE,
                recipient="owner@example.com",
                subject=f"Melting Moments — Daily sales {date}",
                status="sent",
            )
        )
        await db.commit()

        try:
            # The old subject-LIKE guard would return True here and skip the night.
            assert await dse._already_sent(db, date) is False

            # Once the automatic send journals it, and only then, it is suppressed.
            await dse._record_sent(db, date, ["owner@example.com"])
            assert await dse._already_sent(db, date) is True
        finally:
            await db.execute(
                delete(DailySalesSend).where(DailySalesSend.business_date == date)
            )
            await db.execute(delete(EmailLog).where(EmailLog.subject.like(f"%{date}%")))
            await db.commit()


@asynccontextmanager
async def _lock_ok(*_a, **_k):
    yield True


async def test_a_partial_recipient_failure_leaves_the_day_due(engine):
    """When one recipient fails, no journal row is written, so the next tick
    retries. When they all succeed, the day is journalled and stops being due."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    target = "2026-08-25"
    already = ("2026-08-23", "2026-08-24")

    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch",
            reference=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            business_day_start="04:00",
            is_active=True,
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))
        # Pre-journal the earlier catch-up days so only `target` is due at the
        # chosen instant (01:05 on the 26th, past the 25th's send hour; the 26th
        # itself is still in progress and held).
        for d in already:
            db.add(DailySalesSend(business_date=d, recipients=["x@example.com"]))
        await db.commit()
        branch_id = branch.id

    async def fake_schedule(_db, _branch_id):
        return {wd: ("09:00", "23:00") for wd in range(7)}

    now = datetime(2026, 8, 26, 1, 5, tzinfo=_DUBAI)

    partial = AsyncMock(
        return_value={
            "sent": [
                {"recipient": "a@example.com", "status": "sent", "error": None},
                {"recipient": "b@example.com", "status": "failed", "error": "550"},
            ]
        }
    )
    full = AsyncMock(
        return_value={
            "sent": [
                {"recipient": "a@example.com", "status": "sent", "error": None},
                {"recipient": "b@example.com", "status": "sent", "error": None},
            ]
        }
    )

    try:
        with (
            patch.object(
                dse.business_day_service,
                "resolve_timezone",
                AsyncMock(return_value=_DUBAI),
            ),
            patch.object(dse.branch_hours_service, "schedule", fake_schedule),
            patch.object(dse.advisory_lock, "held", _lock_ok),
        ):
            # Tick 1: b@ fails → target must NOT be journalled.
            with patch.object(dse, "send", partial):
                async with Session() as db:
                    await dse._tick(db, now=now)
            async with Session() as db:
                row = (
                    await db.execute(
                        select(DailySalesSend).where(
                            DailySalesSend.business_date == target
                        )
                    )
                ).scalar_one_or_none()
            assert row is None, "a partial failure was wrongly journalled as sent"
            assert partial.await_count == 1

            # Tick 2: everyone succeeds → now it is journalled and no longer due.
            with patch.object(dse, "send", full):
                async with Session() as db:
                    await dse._tick(db, now=now)
            async with Session() as db:
                row = (
                    await db.execute(
                        select(DailySalesSend).where(
                            DailySalesSend.business_date == target
                        )
                    )
                ).scalar_one_or_none()
                assert row is not None, "a fully-sent day was not journalled"
                assert await dse._already_sent(db, target) is True
    finally:
        async with Session() as db:
            await db.execute(
                delete(DailySalesSend).where(
                    DailySalesSend.business_date.in_([target, *already])
                )
            )
            await db.execute(
                Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
            )
            await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
            await db.commit()
