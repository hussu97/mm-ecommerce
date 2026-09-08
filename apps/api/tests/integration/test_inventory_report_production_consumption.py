"""Raw-material report anticipates production consumption at close.

Against a real Postgres, because it exercises the recipe expansion and the ledger
read together, which a mocked session cannot answer:

- The recipe-derived "Used in production" figure appears on a raw-material report as
  soon as a sibling production report has *entered* its produced goods — before that
  production report is approved/posted (the original gap: it used to read 0 until
  production posted).
- It is scoped to *unposted* siblings, so once production posts (its consumption now
  on the ledger), the figure comes from the ledger and the proposed term drops to
  zero — no double count.
- The new "Extra production use" column is a shop-entered extra deduction that posts
  its own EXTRA_PRODUCTION_USE movement on approval.
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
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventoryReportTemplate,
    Recipe,
    RecipeLine,
    RecipeVersion,
    ShiftInventoryReport,
    ShiftInventoryReportLine,
    ShiftInventoryReportStatusEnum,
)
from app.models.user import User
from app.services.inventory import inventory_service, recipe_service, report_service
from app.services.inventory.recipe_service import RecipeLineInput

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
]

MARKER = "epu-test"
BUSINESS_DATE = "2026-09-08"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine):
    """A branch producing "Brownie" (raw: 2g flour per unit), and the raw-material
    report template that must anticipate the flour it draws down."""
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
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
        )
        db.add(user)
        flour = InventoryItem(
            sku=f"{MARKER}-flour-{uuid.uuid4().hex[:8]}",
            name="Flour",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("0.01"),
        )
        brownie = InventoryItem(
            sku=f"{MARKER}-brownie-{uuid.uuid4().hex[:8]}",
            name="Brownie",
            kind="produced_good",
            tracking_mode="stocked",
            storage_unit="unit",
            ingredient_unit="unit",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("1"),
        )
        db.add_all([flour, brownie])
        await db.flush()
        # Brownie consumes 2g flour per unit, yield 1 (no planned waste), so producing
        # N brownies draws exactly 2N flour.
        await recipe_service.draft_and_activate(
            db,
            kind="inventory_item",
            owner_id=brownie.id,
            lines=[RecipeLineInput(item_id=flour.id, quantity=Decimal("2"))],
            user_id=user.id,
        )
        # Seed flour stock so opening/closing are meaningful.
        inventory_service.apply_movement(
            await inventory_service.level_for(db, flour.id, warehouse.id),
            Decimal("100"),
            Decimal("0.01"),
        )
        raw_template = InventoryReportTemplate(
            branch_id=branch.id,
            name=f"{MARKER} raw",
            report_type="raw_materials",
            cadence="per_till",
            version_number=1,
            configuration={},
        )
        prod_template = InventoryReportTemplate(
            branch_id=branch.id,
            name=f"{MARKER} prod",
            report_type="production",
            cadence="per_till",
            version_number=1,
            configuration={},
        )
        db.add_all([raw_template, prod_template])
        await db.commit()
        ids = {
            "branch_id": branch.id,
            "warehouse_id": warehouse.id,
            "user_id": user.id,
            "flour_id": flour.id,
            "brownie_id": brownie.id,
            "raw_template_id": raw_template.id,
            "prod_template_id": prod_template.id,
        }

    yield ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        branch_id = ids["branch_id"]
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id == branch_id
                    )
                )
            )
        )
        await db.execute(
            ShiftInventoryReportLine.__table__.delete().where(
                ShiftInventoryReportLine.report_id.in_(
                    select(ShiftInventoryReport.id).where(
                        ShiftInventoryReport.branch_id == branch_id
                    )
                )
            )
        )
        await db.execute(
            ShiftInventoryReport.__table__.delete().where(
                ShiftInventoryReport.branch_id == branch_id
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == branch_id
            )
        )
        recipe_ids = select(Recipe.id).where(
            Recipe.inventory_item_id.in_([ids["brownie_id"], ids["flour_id"]])
        )
        version_ids = select(RecipeVersion.id).where(
            RecipeVersion.recipe_id.in_(recipe_ids)
        )
        await db.execute(
            RecipeLine.__table__.delete().where(
                RecipeLine.recipe_version_id.in_(version_ids)
            )
        )
        await db.execute(
            RecipeVersion.__table__.delete().where(
                RecipeVersion.recipe_id.in_(recipe_ids)
            )
        )
        await db.execute(Recipe.__table__.delete().where(Recipe.id.in_(recipe_ids)))
        for item_id in (ids["brownie_id"], ids["flour_id"]):
            await db.execute(
                InventoryLevel.__table__.delete().where(
                    InventoryLevel.item_id == item_id
                )
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
            InventoryItem.__table__.delete().where(
                InventoryItem.id.in_([ids["brownie_id"], ids["flour_id"]])
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(User.__table__.delete().where(User.id == ids["user_id"]))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


def _production_report(ids, *, status, produced):
    """A production report with `produced` brownies entered but not yet posted."""
    report = ShiftInventoryReport(
        branch_id=ids["branch_id"],
        template_id=ids["prod_template_id"],
        business_date=BUSINESS_DATE,
        status=status,
        idempotency_key=f"{MARKER}-prod:{uuid.uuid4()}",
        base_posting_sequence=None,
        template_snapshot={"report_type": "production", "opening_count": False},
    )
    report.lines = [
        ShiftInventoryReportLine(
            item_id=ids["brownie_id"],
            unit="unit",
            confirmed=True,
            production_quantity=Decimal(str(produced)),
            entered_quantity=Decimal(str(produced)),
            expected_quantity=Decimal(str(produced)),
            opening_quantity=Decimal("0"),
            source_summary={
                "required_input": "physical_count",
                "item_name": "Brownie",
                "entered_columns": ["production_quantity"],
                "prefilled": {"production_quantity": "0"},
            },
        )
    ]
    return report


async def test_unposted_production_shows_up_as_used_in_production(engine, env):
    """A DRAFT production report of 5 brownies makes the flour report anticipate
    10g used in production — before production is posted."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        report = _production_report(
            env, status=ShiftInventoryReportStatusEnum.DRAFT.value, produced=5
        )
        db.add(report)
        await db.flush()

        raw_report = ShiftInventoryReport(
            branch_id=env["branch_id"],
            template_id=env["raw_template_id"],
            business_date=BUSINESS_DATE,
            status=ShiftInventoryReportStatusEnum.OUTSTANDING.value,
            idempotency_key=f"{MARKER}-raw:{uuid.uuid4()}",
            base_posting_sequence=None,
            template_snapshot={"report_type": "raw_materials", "opening_count": False},
        )
        db.add(raw_report)
        await db.flush()

        proposed = await report_service._proposed_production_consumption(db, raw_report)
        assert proposed.get(env["flour_id"]) == Decimal("10")


async def test_posted_production_is_not_double_counted(engine, env):
    """Once the production report posts, its flour consumption is on the ledger; the
    proposed term drops to zero so the raw-material figure is not counted twice."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, env["user_id"])
        report = _production_report(
            env, status=ShiftInventoryReportStatusEnum.APPROVED.value, produced=5
        )
        db.add(report)
        await db.flush()
        await report_service.post_report(db, report=report, user=user)
        await db.commit()

    async with Session() as db:
        # Ledger now records the recipe consumption (5 x 2g = 10g).
        consumed = (
            await db.execute(
                select(InventoryTransactionItem.quantity)
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryTransactionItem.transaction_id,
                )
                .where(
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value,
                    InventoryTransactionItem.item_id == env["flour_id"],
                )
            )
        ).scalar_one()
        assert Decimal(str(consumed)) == Decimal("10")

        raw_report = ShiftInventoryReport(
            branch_id=env["branch_id"],
            template_id=env["raw_template_id"],
            business_date=BUSINESS_DATE,
            status=ShiftInventoryReportStatusEnum.OUTSTANDING.value,
            idempotency_key=f"{MARKER}-raw:{uuid.uuid4()}",
            base_posting_sequence=None,
            template_snapshot={"report_type": "raw_materials", "opening_count": False},
        )
        db.add(raw_report)
        await db.flush()
        # The posted report is excluded, so nothing is proposed on top of the ledger.
        proposed = await report_service._proposed_production_consumption(db, raw_report)
        assert proposed.get(env["flour_id"], Decimal("0")) == Decimal("0")


async def test_extra_production_use_posts_its_own_movement(engine, env):
    """Entering the extra column deducts extra flour and posts an EXTRA_PRODUCTION_USE
    transaction on approval."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, env["user_id"])
        before = Decimal(
            str(
                (
                    await inventory_service.level_for(
                        db, env["flour_id"], env["warehouse_id"]
                    )
                ).quantity
            )
        )
        report = ShiftInventoryReport(
            branch_id=env["branch_id"],
            template_id=env["raw_template_id"],
            business_date=BUSINESS_DATE,
            status=ShiftInventoryReportStatusEnum.APPROVED.value,
            idempotency_key=f"{MARKER}-raw:{uuid.uuid4()}",
            base_posting_sequence=None,
            template_snapshot={"report_type": "raw_materials", "opening_count": False},
        )
        report.lines = [
            ShiftInventoryReportLine(
                item_id=env["flour_id"],
                unit="g",
                confirmed=True,
                extra_production_consumption_quantity=Decimal("3"),
                entered_quantity=before - Decimal("3"),
                expected_quantity=before - Decimal("3"),
                opening_quantity=before,
                source_summary={
                    "required_input": "physical_count",
                    "item_name": "Flour",
                    "entered_columns": ["extra_production_consumption_quantity"],
                    "prefilled": {"extra_production_consumption_quantity": "0"},
                },
            )
        ]
        db.add(report)
        await db.flush()
        await report_service.post_report(db, report=report, user=user)
        await db.commit()

    async with Session() as db:
        extra = (
            await db.execute(
                select(InventoryTransactionItem.quantity)
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryTransactionItem.transaction_id,
                )
                .where(
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.EXTRA_PRODUCTION_USE.value,
                    InventoryTransactionItem.item_id == env["flour_id"],
                )
            )
        ).scalar_one()
        assert Decimal(str(extra)) == Decimal("3")
