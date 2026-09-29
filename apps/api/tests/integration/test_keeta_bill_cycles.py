"""Overlapping Keeta downloads land on one statement per (shop, settlement cycle).

Keeta billing reports are export tasks for arbitrary ranges; one shop had
22–31 Jul, 23–31 Jul and 23 Jul–1 Aug downloads, all stored as separate
statements with their own copies of the same order lines. Keyed on the cycle,
they upsert the same rows; the statement period widens to the days billed; a
partial download never overwrites a whole cycle's payout.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.aggregator import (
    AggregatorPayout,
    AggregatorStatement,
    AggregatorStatementLine,
)
from app.services.aggregators import ingest
from app.services.aggregators.normalized import (
    StandardPayout,
    StandardStatement,
    StandardStatementLine,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

SHOP = "9990077"
CYCLE = f"KEETA_BILL_{SHOP}_2031-07-22_2031-07-31"


@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _download(start: str, end: str, orders: list[str]) -> StandardStatement:
    return StandardStatement(
        statement_id=CYCLE,
        period_start=start,
        period_end=end,
        external_outlet_id=SHOP,
        currency="AED",
        lines=[
            StandardStatementLine(
                source_key=f"{CYCLE}:{order}:net_payable",
                statement_id=CYCLE,
                external_order_id=order,
                line_date="2031-07-25",
                line_type="payout",
                fee_category="net_payable",
                amount=Decimal("26.20"),
                currency="AED",
            )
            for order in orders
        ],
    )


async def test_overlapping_downloads_share_lines_and_widen_the_period(db):
    await ingest._upsert_statement(
        db, "keeta", _download("2031-07-23", "2031-07-31", ["A1", "A2"])
    )
    await ingest._upsert_statement(
        db, "keeta", _download("2031-07-22", "2031-07-31", ["A0", "A1", "A2"])
    )
    await ingest._upsert_statement(
        db, "keeta", _download("2031-07-23", "2031-07-31", ["A1"])
    )
    await db.flush()

    lines = await db.scalar(
        select(func.count(AggregatorStatementLine.id)).where(
            AggregatorStatementLine.statement_id == CYCLE
        )
    )
    assert lines == 3, "one line per (order, category), whatever the downloads"
    statement = (
        await db.execute(
            select(AggregatorStatement).where(AggregatorStatement.statement_id == CYCLE)
        )
    ).scalar_one()
    await db.refresh(statement)
    # Widened by the fuller download, never narrowed by a later partial one.
    assert (statement.period_start, statement.period_end) == (
        "2031-07-22",
        "2031-07-31",
    )


async def test_a_partial_download_never_overwrites_a_whole_cycles_payout(db):
    transfer = f"KEETA_BILL_{SHOP}_2031-07-31"

    def payout(amount: str, partial: bool) -> StandardPayout:
        return StandardPayout(
            transfer_id=transfer,
            statement_id=CYCLE,
            transfer_date="2031-07-31",
            transfer_amount=Decimal(amount),
            transfer_status="settled",
            currency="AED",
            partial=partial,
        )

    await ingest._upsert_payout(db, "keeta", payout("322.97", partial=True))
    await ingest._upsert_payout(db, "keeta", payout("1250.00", partial=False))
    await ingest._upsert_payout(db, "keeta", payout("900.00", partial=True))
    await db.flush()
    amount = await db.scalar(
        select(AggregatorPayout.transfer_amount).where(
            AggregatorPayout.transfer_id == transfer
        )
    )
    assert amount == Decimal("1250.00")
