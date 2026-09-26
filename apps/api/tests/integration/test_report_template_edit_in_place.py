"""Editing a branch's current report template changes it in place, against a real
Postgres.

Creating a template for a report type still adds a new revision that replaces the
current one; editing keeps the row, its id and its version number, rewrites the
item list by item (so keeping an item never trips the per-template unique
constraint), and refuses an older, superseded revision.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import ConflictError
from app.models.branch import Branch
from app.models.inventory import InventoryItem, Warehouse
from app.models.inventory_v2 import (
    InventoryReportTemplate,
    InventoryReportTemplateItem,
)
from app.schemas.inventory_v2 import ReportTemplateUpsert
from app.services.inventory import report_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-rpt-tmpl-edit"


@pytest.fixture
async def env():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))
        items = []
        for name in ("Flour", "Sugar", "Butter"):
            item = InventoryItem(
                sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
                name=name,
                kind="raw_material",
                tracking_mode="stocked",
                storage_unit="kg",
                ingredient_unit="kg",
                storage_to_ingredient_factor=Decimal("1"),
            )
            db.add(item)
            items.append(item)
        await db.commit()
        ids = SimpleNamespace(branch=branch.id, items=[item.id for item in items])
    yield Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        template_ids = select(InventoryReportTemplate.id).where(
            InventoryReportTemplate.branch_id == ids.branch
        )
        await db.execute(
            InventoryReportTemplateItem.__table__.delete().where(
                InventoryReportTemplateItem.template_id.in_(template_ids)
            )
        )
        await db.execute(
            InventoryReportTemplate.__table__.delete().where(
                InventoryReportTemplate.branch_id == ids.branch
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(InventoryItem.id.in_(ids.items))
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids.branch)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == ids.branch))
        await db.commit()
    await engine.dispose()


def _data(ids, item_ids, **overrides) -> ReportTemplateUpsert:
    return ReportTemplateUpsert(
        branch_id=ids.branch,
        name=overrides.pop("name", "Raw materials closing count"),
        report_type="raw_materials",
        cadence=overrides.pop("cadence", "per_business_day"),
        is_required=True,
        is_active=overrides.pop("is_active", True),
        display_order=30,
        configuration={"visible_columns": ["opening", "physical"]},
        approval_cost_threshold=Decimal("100"),
        approval_variance_percent=Decimal("10"),
        items=[
            {"item_id": item_id, "display_order": index}
            for index, item_id in enumerate(item_ids)
        ],
        **overrides,
    )


async def test_editing_the_current_template_changes_it_in_place(env):
    Session, ids = env
    flour, sugar, butter = ids.items
    async with Session() as db:
        created = await report_service.create_template(
            db, data=_data(ids, [flour, sugar])
        )
        await db.commit()
        created_at = created.updated_at

    async with Session() as db:
        template = await db.get(InventoryReportTemplate, created.id)
        # Sugar is dropped, Butter added, Flour kept but moved to the end.
        edited = await report_service.update_template(
            db,
            template=template,
            data=_data(ids, [butter, flour], name="Raw materials", cadence="per_till"),
        )
        await db.commit()

    assert edited.id == created.id
    assert edited.version_number == created.version_number == 1
    assert edited.name == "Raw materials"
    assert edited.cadence == "per_till"
    assert edited.updated_at > created_at
    lines = sorted(edited.items, key=lambda row: row.display_order)
    assert [row.item_id for row in lines] == [butter, flour]

    async with Session() as db:
        rows = (
            (
                await db.execute(
                    select(InventoryReportTemplate).where(
                        InventoryReportTemplate.branch_id == ids.branch
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, "an edit must not add a revision"


async def test_an_edit_can_reactivate_a_deactivated_current_template(env):
    Session, ids = env
    async with Session() as db:
        created = await report_service.create_template(
            db, data=_data(ids, [ids.items[0]])
        )
        await report_service.deactivate_template(db, template=created)
        await db.commit()

    async with Session() as db:
        template = await db.get(InventoryReportTemplate, created.id)
        edited = await report_service.update_template(
            db, template=template, data=_data(ids, [ids.items[0]], is_active=True)
        )
        await db.commit()
    assert edited.is_active is True


async def test_a_superseded_revision_cannot_be_edited(env):
    Session, ids = env
    async with Session() as db:
        first = await report_service.create_template(
            db, data=_data(ids, [ids.items[0]])
        )
        second = await report_service.create_template(
            db, data=_data(ids, [ids.items[1]])
        )
        await db.commit()
    assert second.version_number == first.version_number + 1

    async with Session() as db:
        template = await db.get(InventoryReportTemplate, first.id)
        with pytest.raises(ConflictError):
            await report_service.update_template(
                db, template=template, data=_data(ids, [ids.items[2]])
            )
        await db.rollback()
