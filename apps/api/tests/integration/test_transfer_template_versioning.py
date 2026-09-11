"""Append-only versioning for inventory transfer templates, against a real Postgres.

Mirrors the shift-report-template versioning: editing a template inserts a new
version and leaves the old one as history; only the latest revision of a
``(source_branch_id, name)`` lineage is offered to the register or may be
deactivated; and a transfer raised from a template stamps an immutable snapshot of
that version onto the order, so its provenance survives a later edit.
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
    create_transfer_template,
    pos_transfer_templates,
    update_transfer_template,
)
from app.core.exceptions import BadRequestError
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.operations import (
    InventoryTransferTemplate,
    InventoryTransferTemplateItem,
    TransferOrder,
)
from app.models.user import User
from app.schemas.inventory import TransferTemplateItemInput, TransferTemplateUpsert
from app.services.inventory import (
    inventory_service,
    transfer_service,
    transfer_template_service,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-tmpl-ver"


async def _make_branch(db, name: str) -> tuple[Branch, Warehouse]:
    branch = Branch(
        name=f"{MARKER} {name}", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
    )
    db.add(branch)
    await db.flush()
    warehouse = Warehouse(branch_id=branch.id, name="Default stock", is_default=True)
    db.add(warehouse)
    db.add(
        BranchInventorySettings(
            branch_id=branch.id,
            inventory_enabled=True,
            production_enabled=True,
            sales_consumption_enabled=True,
            allow_negative_stock=True,
            go_live_at=inventory_service.utcnow(),
            go_live_sequence=0,
        )
    )
    return branch, warehouse


@pytest.fixture
async def env():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        source, source_wh = await _make_branch(db, "source")
        dest, dest_wh = await _make_branch(db, "dest")
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
        await db.flush()
        for item in items:
            db.add(
                InventoryLevel(
                    item_id=item.id,
                    warehouse_id=source_wh.id,
                    quantity=Decimal("100"),
                    average_cost=Decimal("3"),
                )
            )
            db.add(
                InventoryLevel(
                    item_id=item.id, warehouse_id=dest_wh.id, quantity=Decimal("0")
                )
            )
        await db.commit()
        ids = SimpleNamespace(
            source=source.id,
            dest=dest.id,
            source_wh=source_wh.id,
            dest_wh=dest_wh.id,
            user=user.id,
            items=[i.id for i in items],
        )
    yield Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        branch_ids = [ids.source, ids.dest]
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id.in_(branch_ids)
                    )
                )
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id.in_(branch_ids)
            )
        )
        await db.execute(
            TransferOrder.__table__.delete().where(
                TransferOrder.source_branch_id.in_(branch_ids)
            )
        )
        await db.execute(
            InventoryTransferTemplateItem.__table__.delete().where(
                InventoryTransferTemplateItem.template_id.in_(
                    select(InventoryTransferTemplate.id).where(
                        InventoryTransferTemplate.source_branch_id.in_(branch_ids)
                    )
                )
            )
        )
        await db.execute(
            InventoryTransferTemplate.__table__.delete().where(
                InventoryTransferTemplate.source_branch_id.in_(branch_ids)
            )
        )
        await db.execute(
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id.in_(ids.items)
            )
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id.in_(branch_ids)
            )
        )
        for item_id in ids.items:
            await db.execute(
                InventoryItem.__table__.delete().where(InventoryItem.id == item_id)
            )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id.in_(branch_ids))
        )
        await db.execute(User.__table__.delete().where(User.id == ids.user))
        await db.execute(Branch.__table__.delete().where(Branch.id.in_(branch_ids)))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()
    await engine.dispose()


def _upsert(ids, *, name="Daily run", is_active=True, items=None):
    return TransferTemplateUpsert(
        source_branch_id=ids.source,
        destination_branch_id=None,
        name=name,
        is_active=is_active,
        display_order=0,
        items=[
            TransferTemplateItemInput(item_id=item_id, display_order=i)
            for i, item_id in enumerate(items or [ids.items[0]])
        ],
    )


def _order_items(item_id, dest_id, quantity):
    return [
        SimpleNamespace(
            item_id=item_id,
            unit="storage",
            override=False,
            allocations=[
                SimpleNamespace(branch_id=dest_id, quantity=Decimal(str(quantity)))
            ],
        )
    ]


async def _revisions(db, source_id, name) -> list[InventoryTransferTemplate]:
    return list(
        (
            await db.execute(
                select(InventoryTransferTemplate)
                .where(
                    InventoryTransferTemplate.source_branch_id == source_id,
                    InventoryTransferTemplate.name == name,
                )
                .order_by(InventoryTransferTemplate.version_number)
            )
        )
        .scalars()
        .unique()
        .all()
    )


async def test_editing_a_template_appends_v2_and_keeps_v1_as_history(env):
    Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        created = await create_transfer_template(
            _upsert(ids, items=[ids.items[0]]), db=db, user=user
        )
        await db.commit()
        assert created.version_number == 1

    async with Session() as db:
        user = await db.get(User, ids.user)
        updated = await update_transfer_template(
            created.id, _upsert(ids, items=[ids.items[1]]), db=db, user=user
        )
        await db.commit()
        # A new row at the next version, not a mutation of the first.
        assert updated.version_number == 2
        assert updated.id != created.id

    async with Session() as db:
        rows = await _revisions(db, ids.source, "Daily run")
        assert [r.version_number for r in rows] == [1, 2]
        # v1 is untouched history — still there, still its original item.
        v1 = next(r for r in rows if r.version_number == 1)
        assert [i.item_id for i in v1.items] == [ids.items[0]]


async def test_latest_active_hides_a_superseded_row_after_newest_is_deactivated(env):
    Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        await create_transfer_template(_upsert(ids), db=db, user=user)
        await db.commit()
    async with Session() as db:
        user = await db.get(User, ids.user)
        rows = await _revisions(db, ids.source, "Daily run")
        await update_transfer_template(rows[0].id, _upsert(ids), db=db, user=user)
        await db.commit()

    async with Session() as db:
        rows = await _revisions(db, ids.source, "Daily run")
        current = transfer_template_service.latest_active_templates(rows)
        # Only the newest active revision is current.
        assert [t.version_number for t in current] == [2]

    async with Session() as db:
        rows = await _revisions(db, ids.source, "Daily run")
        v2 = next(r for r in rows if r.version_number == 2)
        await transfer_template_service.deactivate_template(db, template=v2)
        await db.commit()

    async with Session() as db:
        rows = await _revisions(db, ids.source, "Daily run")
        # v1 is still active in the DB, but must NOT resurface now that the newer
        # revision has been explicitly deactivated.
        v1 = next(r for r in rows if r.version_number == 1)
        assert v1.is_active is True
        current = transfer_template_service.latest_active_templates(rows)
        assert current == []


async def test_deactivate_refuses_a_non_latest_revision(env):
    Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        await create_transfer_template(_upsert(ids), db=db, user=user)
        await db.commit()
    async with Session() as db:
        user = await db.get(User, ids.user)
        rows = await _revisions(db, ids.source, "Daily run")
        await update_transfer_template(rows[0].id, _upsert(ids), db=db, user=user)
        await db.commit()

    async with Session() as db:
        rows = await _revisions(db, ids.source, "Daily run")
        v1 = next(r for r in rows if r.version_number == 1)
        with pytest.raises(BadRequestError):
            await transfer_template_service.deactivate_template(db, template=v1)


async def test_pos_seed_returns_only_the_latest_active_revision(env):
    Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        await create_transfer_template(_upsert(ids), db=db, user=user)
        await db.commit()
    async with Session() as db:
        user = await db.get(User, ids.user)
        rows = await _revisions(db, ids.source, "Daily run")
        await update_transfer_template(rows[0].id, _upsert(ids), db=db, user=user)
        await db.commit()

    async with Session() as db:
        user = await db.get(User, ids.user)
        seeded = await pos_transfer_templates(
            source_branch_id=ids.source, db=db, user=user
        )
        assert len(seeded) == 1
        assert seeded[0].version_number == 2


async def test_transfer_snapshot_is_frozen_at_raise_and_survives_a_later_edit(env):
    Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        created = await create_transfer_template(
            _upsert(ids, items=[ids.items[0]]), db=db, user=user
        )
        await db.commit()
        template_v1_id = created.id

    async with Session() as db:
        source = await db.get(Branch, ids.source)
        user = await db.get(User, ids.user)
        order = await transfer_service.create_transfer_order(
            db,
            source_branch=source,
            user=user,
            items=_order_items(ids.items[0], ids.dest, 5),
            template_id=template_v1_id,
        )
        await db.commit()
        order_id = order.id

    async with Session() as db:
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.template_id == template_v1_id
        assert order.template_version == 1
        snapshot = order.template_snapshot
        assert snapshot["name"] == "Daily run"
        assert snapshot["version_number"] == 1
        assert [i["item_id"] for i in snapshot["items"]] == [str(ids.items[0])]
        assert snapshot["items"][0]["item_name"] == "Flour"

    # Edit the template to a new version with a different item.
    async with Session() as db:
        user = await db.get(User, ids.user)
        await update_transfer_template(
            template_v1_id, _upsert(ids, items=[ids.items[1]]), db=db, user=user
        )
        await db.commit()

    async with Session() as db:
        # The order still carries v1's snapshot — the record is immutable.
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.template_version == 1
        assert order.template_snapshot["version_number"] == 1
        assert [i["item_id"] for i in order.template_snapshot["items"]] == [
            str(ids.items[0])
        ]


async def test_ad_hoc_transfer_without_a_template_leaves_the_columns_null(env):
    Session, ids = env
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        user = await db.get(User, ids.user)
        order = await transfer_service.create_transfer_order(
            db,
            source_branch=source,
            user=user,
            items=_order_items(ids.items[0], ids.dest, 3),
        )
        await db.commit()
        order_id = order.id

    async with Session() as db:
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.template_id is None
        assert order.template_version is None
        assert order.template_snapshot is None
