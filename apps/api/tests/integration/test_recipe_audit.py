"""Recipe create / update / activate leave an audit trail.

The Recipes console's write endpoints must record who changed a recipe and how,
the same way every other admin mutation does. This drives the endpoints against
a real Postgres (audit_service writes a real row, and the recipe activation
touches the immutable-ledger triggers) rather than the mocked unit session.

The whole exercise runs in one uncommitted transaction that is rolled back, so
no trigger-disable cleanup is needed — nothing is ever committed.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.inventory_v2 import activate_recipe_version, put_recipe_draft
from app.models.audit_log import AuditLog
from app.models.inventory import InventoryItem
from app.models.user import User
from app.schemas.inventory_v2 import RecipeDraftRequest, VersionedRecipeLineInput

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-recipe-audit"


class _Req:
    """The slice of a Request that audit_service.client_ip reads."""

    headers: dict = {}

    class client:
        host = "1.2.3.4"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _item(name: str, *, kind: str) -> InventoryItem:
    return InventoryItem(
        name=name,
        sku=f"{MARKER[:6].upper()}-{uuid.uuid4().hex[:8]}",
        kind=kind,
        ingredient_unit="g",
        minimum_level=Decimal("0"),
        par_level=Decimal("0"),
    )


async def _audit_rows(db, owner_id) -> list[AuditLog]:
    return list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "recipe",
                    AuditLog.entity_id == str(owner_id),
                )
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )


async def test_recipe_lifecycle_is_audited(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        admin = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com",
            display_name=f"{MARKER} Chef",
            is_staff=True,
            is_admin=True,
        )
        made = _item(f"{MARKER} Sponge", kind="produced_good")
        flour = _item(f"{MARKER} Flour", kind="raw_material")
        db.add_all([admin, made, flour])
        await db.flush()

        def _payload(qty: str, basis="unit", batch_yield=None) -> RecipeDraftRequest:
            return RecipeDraftRequest(
                ingredients=[
                    VersionedRecipeLineInput(item_id=flour.id, quantity=Decimal(qty))
                ],
                basis=basis,
                batch_yield=batch_yield,
            )

        # First save → CREATE.
        await put_recipe_draft(
            "inventory_item", made.id, _payload("100"), _Req(), db=db, user=admin
        )
        rows = await _audit_rows(db, made.id)
        assert [r.action for r in rows] == ["CREATE"]
        assert rows[0].admin_email == admin.email
        assert rows[0].entity_label.endswith(f"{MARKER} Sponge")
        assert rows[0].changes["ingredient_count"] == 1

        # Second save → UPDATE (the owner already has a version).
        version = await put_recipe_draft(
            "inventory_item",
            made.id,
            _payload("120", basis="batch", batch_yield=Decimal("6")),
            _Req(),
            db=db,
            user=admin,
        )
        rows = await _audit_rows(db, made.id)
        assert [r.action for r in rows] == ["CREATE", "UPDATE"]
        assert rows[1].changes["basis"] == "batch"
        assert rows[1].changes["batch_yield"] == "6"

        # Activate → STATUS_CHANGE.
        await activate_recipe_version(version.id, _Req(), db=db, user=admin)
        rows = await _audit_rows(db, made.id)
        assert [r.action for r in rows] == ["CREATE", "UPDATE", "STATUS_CHANGE"]
        assert rows[2].changes["status"] == "active"
        assert rows[2].changes["activated_version"] == version.version_number

        await db.rollback()
