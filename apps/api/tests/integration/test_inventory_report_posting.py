"""Shift-report posting and set-the-balance semantics, against a real Postgres.

These exercise the ledger poster and its advisory lock across the whole posting
path, which a mocked session cannot answer:

- F-INV-1: a `production` report produces the entered *Produced* column, not the
  physical closing count (which the legacy early-return produced, inflating stock).
- F-INV-2: a movement column the ledger prefilled posts only the shop's delta on
  top of it, never the whole value; a refresh clears the typed markers.
- F-INV-13: an opening balance SETS the level, so a second opening count replaces
  the balance instead of doubling it.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventoryReportTemplate,
    ShiftInventoryReport,
    ShiftInventoryReportLine,
    ShiftInventoryReportStatusEnum,
)
from app.models.user import User
from app.services.inventory import inventory_service, report_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-inv-report"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine):
    """A live inventory branch with a default warehouse, settings, user and item."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        warehouse = Warehouse(
            branch_id=branch.id, name="Default stock", is_default=True
        )
        db.add(warehouse)
        db.add(
            BranchInventorySettings(
                branch_id=branch.id,
                inventory_enabled=True,
                production_enabled=True,
                sales_consumption_enabled=True,
                go_live_at=report_service.utcnow(),
                go_live_sequence=0,
            )
        )
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
        )
        db.add(user)
        item = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            name="Brownie",
            kind="produced_good",
            tracking_mode="stocked",
            storage_unit="unit",
            ingredient_unit="unit",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("3"),
        )
        db.add(item)
        template = InventoryReportTemplate(
            branch_id=branch.id,
            name=f"{MARKER} template",
            report_type="raw_materials",
            cadence="per_till",
            version_number=1,
            configuration={},
        )
        db.add(template)
        await db.commit()
        ids = (branch.id, warehouse.id, user.id, item.id, template.id)

    yield ids

    branch_id, _, user_id, item_id, template_id = ids
    async with Session() as db:
        # Closed inventory transactions are immutable by trigger; drop into replica
        # role so teardown can delete this test's rows regardless.
        await db.execute(text("SET session_replication_role = 'replica'"))
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id == branch_id
                    )
                )
            )
        )
        for table in (
            ShiftInventoryReportLine.__table__.delete().where(
                ShiftInventoryReportLine.report_id.in_(
                    select(ShiftInventoryReport.id).where(
                        ShiftInventoryReport.branch_id == branch_id
                    )
                )
            ),
            ShiftInventoryReport.__table__.delete().where(
                ShiftInventoryReport.branch_id == branch_id
            ),
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == branch_id
            ),
        ):
            await db.execute(table)
        # inventory_levels / settings / warehouse cascade off the branch delete via
        # their FKs; do the explicit ones first, then the parents.
        from app.models.inventory import InventoryLevel

        await db.execute(
            InventoryLevel.__table__.delete().where(InventoryLevel.item_id == item_id)
        )
        await db.execute(
            InventoryReportTemplate.__table__.delete().where(
                InventoryReportTemplate.branch_id == branch_id
            )
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id == branch_id
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(InventoryItem.id == item_id)
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(User.__table__.delete().where(User.id == user_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


def _report(branch_id, template_id, *, report_type, opening_count=False):
    return ShiftInventoryReport(
        branch_id=branch_id,
        template_id=template_id,
        business_date=BUSINESS_DATE,
        status=ShiftInventoryReportStatusEnum.APPROVED.value,
        idempotency_key=f"test-report:{uuid.uuid4()}",
        base_posting_sequence=None,
        template_snapshot={
            "report_type": report_type,
            "opening_count": opening_count,
        },
    )


def _line(item_id, **columns):
    summary = {"required_input": "physical_count", "item_name": "Brownie"}
    summary.update(columns.pop("source_summary", {}))
    line = ShiftInventoryReportLine(
        item_id=item_id,
        unit="unit",
        confirmed=True,
        source_summary=summary,
    )
    for key, value in columns.items():
        setattr(line, key, Decimal(str(value)))
    return line


async def _levels(db, item_id, warehouse_id):
    level = await inventory_service.level_for(db, item_id, warehouse_id)
    return Decimal(str(level.quantity))


async def test_a_production_report_produces_the_produced_column_not_the_count(
    engine, env
):
    branch_id, warehouse_id, user_id, item_id, template_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        report = _report(branch_id, template_id, report_type="production")
        report.lines = [
            _line(
                item_id,
                production_quantity=10,
                entered_quantity=7,
                expected_quantity=10,
                opening_quantity=0,
                source_summary={
                    "entered_columns": ["production_quantity"],
                    "prefilled": {"production_quantity": "0"},
                },
            )
        ]
        db.add(report)
        await db.flush()

        await report_service.post_report(db, report=report, user=user)
        await db.commit()

    async with Session() as db:
        production = (
            await db.execute(
                select(InventoryTransaction)
                .where(
                    InventoryTransaction.branch_id == branch_id,
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.PRODUCTION.value,
                )
                .options()
            )
        ).scalar_one()
        line = (
            await db.execute(
                select(InventoryTransactionItem).where(
                    InventoryTransactionItem.transaction_id == production.id
                )
            )
        ).scalar_one()
        # Produced the entered "Produced" column (10), NOT the physical count (7).
        assert Decimal(str(line.signed_quantity)) == Decimal("10")
        # The count then trues the level down to what was physically there.
        assert await _levels(db, item_id, warehouse_id) == Decimal("7")


async def test_confirming_a_prefilled_movement_posts_only_the_delta(engine, env):
    branch_id, warehouse_id, user_id, item_id, template_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def post_received(prefilled, entered):
        async with Session() as db:
            user = await db.get(User, user_id)
            report = _report(branch_id, template_id, report_type="raw_materials")
            report.lines = [
                _line(
                    item_id,
                    purchasing_quantity=entered,
                    entered_quantity=entered,
                    expected_quantity=entered,
                    opening_quantity=0,
                    source_summary={
                        "entered_columns": ["purchasing_quantity"],
                        "prefilled": {"purchasing_quantity": str(prefilled)},
                    },
                )
            ]
            db.add(report)
            await db.flush()
            await report_service.post_report(db, report=report, user=user)
            await db.commit()

    async def purchasing_total(db):
        rows = (
            (
                await db.execute(
                    select(InventoryTransactionItem.signed_quantity)
                    .join(
                        InventoryTransaction,
                        InventoryTransaction.id
                        == InventoryTransactionItem.transaction_id,
                    )
                    .where(
                        InventoryTransaction.branch_id == branch_id,
                        InventoryTransaction.type
                        == InventoryTransactionTypeEnum.PURCHASING.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        return sum((Decimal(str(v)) for v in rows), Decimal("0"))

    # The ledger prefilled 5 received; the shop confirmed 5 unchanged -> nothing.
    await post_received(prefilled=5, entered=5)
    async with Session() as db:
        assert await purchasing_total(db) == Decimal("0")

    # The shop raised it to 8 -> only the +3 delta posts, never the whole 8.
    await post_received(prefilled=5, entered=8)
    async with Session() as db:
        assert await purchasing_total(db) == Decimal("3")


async def test_refresh_clears_the_entered_column_markers(engine, env):
    branch_id, warehouse_id, user_id, item_id, template_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        report = _report(branch_id, template_id, report_type="raw_materials")
        report.base_posting_sequence = None
        report.status = ShiftInventoryReportStatusEnum.DRAFT.value
        report.lines = [
            _line(
                item_id,
                purchasing_quantity=8,
                source_summary={
                    "entered_columns": ["purchasing_quantity"],
                    "prefilled": {"purchasing_quantity": "5"},
                },
            )
        ]
        db.add(report)
        await db.flush()
        report_id = report.id
        await db.commit()

    async with Session() as db:
        report = await report_service.load_report(db, report_id)
        refreshed = await report_service.refresh_report(db, report)
        await db.commit()
        assert (refreshed.lines[0].source_summary or {}).get("entered_columns") == []


async def test_a_second_opening_balance_sets_the_level_not_doubles_it(engine, env):
    branch_id, warehouse_id, user_id, item_id, template_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def post_opening(count):
        async with Session() as db:
            user = await db.get(User, user_id)
            txn = InventoryTransaction(
                reference=await inventory_service.next_reference(
                    db, InventoryTransactionTypeEnum.OPENING_BALANCE.value
                ),
                type=InventoryTransactionTypeEnum.OPENING_BALANCE.value,
                status=TransactionStatusEnum.DRAFT.value,
                branch_id=branch_id,
                warehouse_id=warehouse_id,
                business_date=BUSINESS_DATE,
                creator_id=user.id,
                idempotency_key=f"opening:{uuid.uuid4()}",
                items=[],
            )
            db.add(txn)
            await db.flush()
            txn.items.append(
                InventoryTransactionItem(
                    item_id=item_id,
                    quantity=Decimal(str(count)),
                    unit="ingredient",
                    conversion_factor=Decimal("1"),
                    unit_cost=Decimal("3"),
                )
            )
            await db.flush()
            await inventory_service.post_transaction(db, transaction=txn, user=user)
            await db.commit()

    await post_opening(100)
    async with Session() as db:
        assert await _levels(db, item_id, warehouse_id) == Decimal("100")

    # A second opening count must REPLACE the balance, not add to it.
    await post_opening(60)
    async with Session() as db:
        assert await _levels(db, item_id, warehouse_id) == Decimal("60")
