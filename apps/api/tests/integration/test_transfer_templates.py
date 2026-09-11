"""Transfer-template CRUD endpoints, against a real Postgres.

Regression for a MissingGreenlet 500: `_load_template` used `db.get`, which
returns the just-flushed row without its `items` collection, so serialising it
triggered an async lazy load during Pydantic's synchronous attribute access. The
create/update/list endpoints all serialise a template, so they all have to load
its items eagerly.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.operations import (
    TransferTemplateItemInput,
    TransferTemplateUpsert,
    create_transfer_template,
    list_transfer_templates,
    update_transfer_template,
)
from app.models.branch import Branch
from app.models.inventory import InventoryItem, Warehouse
from app.models.operations import (
    InventoryTransferTemplate,
    InventoryTransferTemplateItem,
)
from app.models.user import User

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-tmpl"


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
        # An active branch must have exactly one default stock container.
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_admin=True,
        )
        db.add(user)
        items = []
        for name in ("Flour", "Sugar"):
            item = InventoryItem(
                sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
                name=name,
                kind="raw_material",
                tracking_mode="stocked",
                storage_unit="kg",
                ingredient_unit="kg",
                storage_to_ingredient_factor=Decimal("1"),
                cost=Decimal("3"),
            )
            db.add(item)
            items.append(item)
        await db.commit()
        ids = SimpleNamespace(
            branch=branch.id, user=user.id, items=[i.id for i in items]
        )
    yield Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        await db.execute(
            InventoryTransferTemplateItem.__table__.delete().where(
                InventoryTransferTemplateItem.template_id.in_(
                    select(InventoryTransferTemplate.id).where(
                        InventoryTransferTemplate.source_branch_id == ids.branch
                    )
                )
            )
        )
        await db.execute(
            InventoryTransferTemplate.__table__.delete().where(
                InventoryTransferTemplate.source_branch_id == ids.branch
            )
        )
        for item_id in ids.items:
            await db.execute(
                InventoryItem.__table__.delete().where(InventoryItem.id == item_id)
            )
        await db.execute(User.__table__.delete().where(User.id == ids.user))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids.branch)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == ids.branch))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()
    await engine.dispose()


async def test_create_list_and_update_a_transfer_template(env):
    Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        data = TransferTemplateUpsert(
            source_branch_id=ids.branch,
            destination_branch_id=None,
            name="Daily run",
            is_active=True,
            display_order=0,
            items=[
                TransferTemplateItemInput(item_id=ids.items[0], display_order=0),
                TransferTemplateItemInput(item_id=ids.items[1], display_order=1),
            ],
        )
        # This serialises the created template — the path that 500'd.
        created = await create_transfer_template(data, db=db, user=user)
        await db.commit()
        assert created.name == "Daily run"
        assert len(created.items) == 2
        assert {i.item_name for i in created.items} == {"Flour", "Sugar"}

    async with Session() as db:
        user = await db.get(User, ids.user)
        listed = await list_transfer_templates(
            source_branch_id=ids.branch, db=db, user=user
        )
        assert len(listed) == 1
        assert len(listed[0].items) == 2

    async with Session() as db:
        user = await db.get(User, ids.user)
        template_id = listed[0].id
        updated = await update_transfer_template(
            template_id,
            TransferTemplateUpsert(
                source_branch_id=ids.branch,
                destination_branch_id=None,
                name="Daily run v2",
                is_active=True,
                display_order=1,
                items=[
                    TransferTemplateItemInput(item_id=ids.items[0], display_order=0)
                ],
            ),
            db=db,
            user=user,
        )
        await db.commit()
        assert updated.name == "Daily run v2"
        assert len(updated.items) == 1
