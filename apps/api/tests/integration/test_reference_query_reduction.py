"""
Fewer round-trips for the same objects, on a real Postgres.

Two kinds of change, each proven here against the statements the engine
actually runs:

* **Joined instead of `selectin`, at hot loads.** `Branch` drags four
  `selectin` relationships behind every load, `Order` one (`legal_entity`) and
  `User` one (`role`). At the hot call sites those now arrive on the same row
  via `joinedload`. Nothing is skipped — the tests compare the joined load with
  a default one attribute by attribute, and check that no relationship is left
  unloaded (an unloaded attribute read later would be a `MissingGreenlet`).
* **`business_settings` through `reference_cache`.** One row, read by nearly
  every request that touches a business day; now read once per change. The
  tests pin the invalidation contract: a committed ORM write (flush or bulk) is
  seen at once, a session sees its own uncommitted write, a raw-SQL write is
  seen within `TTL_CEILING`, and a non-session caller reads straight through.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, inspect, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core import deps, reference_cache
from app.core.security import create_access_token
from app.models.aggregator import AggregatorBranchMap, FoodicsBranchMap
from app.models.base import utcnow
from app.models.branch import Branch, branch_joined_loads
from app.models.branch_channel_tax_config import BranchChannelTaxConfig
from app.models.business_settings import BusinessSettings
from app.models.grubops import GrubOpsLocationMap
from app.models.grubops_order import GrubOpsOrderMap
from app.models.legal_entity import LegalEntity
from app.models.order import Order, OrderSourceEnum, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.role import Role
from app.models.user import User
from app.services.aggregators import promote, reconcile
from app.services.delivery import driver_routing
from app.services.pos import business_day_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

_BRANCH_RELATIONSHIPS = (
    "channel_tax_configs",
    "aggregator_maps",
    "foodics_map",
    "grubops_location",
)


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
def Session(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def statements(engine):
    """Every SQL statement the engine runs, from the moment it is cleared."""
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", _record)


def _selects(sql: list[str]) -> list[str]:
    return [s for s in sql if s.lstrip().upper().startswith("SELECT")]


async def _fatema(db) -> LegalEntity:
    entity = await db.scalar(
        select(LegalEntity).where(LegalEntity.reference == "fatema")
    )
    assert entity is not None, "migration 237 seeds the default legal entity"
    return entity


async def _integrated_branch(db) -> uuid.UUID:
    """A branch with something behind every one of its four relationships."""
    tag = uuid.uuid4().hex[:8]
    branch = Branch(name=f"refq {tag}", reference=f"RQ{tag}")
    db.add(branch)
    await db.flush()
    entity = await _fatema(db)
    db.add_all(
        [
            AggregatorBranchMap(
                channel="talabat", branch_id=branch.id, external_outlet_id=tag
            ),
            AggregatorBranchMap(channel="keeta", branch_id=branch.id, is_active=False),
            AggregatorBranchMap(channel="noon", branch_id=branch.id),
            FoodicsBranchMap(branch_id=branch.id, foodics_branch_id=f"f-{tag}"),
            GrubOpsLocationMap(
                branch_id=branch.id,
                grubops_location_id=f"l-{tag}",
                grubops_partner_id=f"p-{tag}",
            ),
            BranchChannelTaxConfig(
                branch_id=branch.id,
                channel_class="counter",
                legal_entity_id=entity.id,
            ),
            BranchChannelTaxConfig(
                branch_id=branch.id,
                channel_class="website",
                legal_entity_id=entity.id,
            ),
        ]
    )
    await db.flush()
    return branch.id


def _branch_state(branch: Branch) -> dict:
    return {
        "has_grubops": branch.has_grubops,
        "has_foodics": branch.has_foodics,
        "aggregators": branch.aggregators,
        "maps": sorted(m.id for m in branch.aggregator_maps),
        "configs": sorted(c.id for c in branch.channel_tax_configs),
        "foodics": branch.foodics_map.id if branch.foodics_map else None,
        "grubops": branch.grubops_location.id if branch.grubops_location else None,
    }


# ─── Branch ───────────────────────────────────────────────────────────────────


async def test_a_joined_branch_load_is_a_default_load_in_one_query(Session, statements):
    async with Session() as db:
        branch_id = await _integrated_branch(db)

        db.expunge_all()
        statements.clear()
        default = await db.get(Branch, branch_id)
        default_queries = len(_selects(statements))
        expected = _branch_state(default)

        db.expunge_all()
        statements.clear()
        joined = await db.get(Branch, branch_id, options=branch_joined_loads())
        joined_queries = len(_selects(statements))
        assert not inspect(joined).unloaded & set(_BRANCH_RELATIONSHIPS), (
            "nothing left to lazy-load"
        )
        statements.clear()
        assert _branch_state(joined) == expected
        assert statements == [], "reading the relationships costs no IO"
        await db.rollback()

    print(f"\nBranch load: default {default_queries} queries, joined {joined_queries}")
    assert default_queries == 5
    assert joined_queries == 1
    assert expected["aggregators"] == ["noon", "talabat"]
    assert len(expected["configs"]) == 2


async def test_a_bare_branch_joins_to_empty_relationships(Session, statements):
    """No maps and no configs — the outer join must still say 'loaded, empty'."""
    async with Session() as db:
        branch = Branch(name="bare", reference=f"RB{uuid.uuid4().hex[:8]}")
        db.add(branch)
        await db.flush()
        db.expunge_all()
        statements.clear()
        joined = await db.get(Branch, branch.id, options=branch_joined_loads())
        assert len(_selects(statements)) == 1
        statements.clear()
        assert joined.aggregator_maps == []
        assert joined.channel_tax_configs == []
        assert joined.foodics_map is None and joined.grubops_location is None
        assert not joined.has_grubops and not joined.has_foodics
        assert statements == []
        await db.rollback()


# ─── Order.legal_entity ───────────────────────────────────────────────────────


async def _grubops_order(db, *, legal_entity_id) -> tuple[Order, str]:
    branch = Branch(name="refq agg", reference=f"RA{uuid.uuid4().hex[:8]}")
    db.add(branch)
    await db.flush()
    external = uuid.uuid4().hex[:12]
    order = Order(
        order_number=f"AGG-RQ-{uuid.uuid4().hex[:10]}",
        email="",
        locale="en",
        delivery_method="delivery",
        order_type="delivery",
        status=OrderStatusEnum.CONFIRMED,
        source=OrderSourceEnum.AGGREGATOR.value,
        aggregator_channel="careem",
        external_reference=external,
        branch_id=branch.id,
        legal_entity_id=legal_entity_id,
        subtotal=Decimal("40"),
        total=Decimal("40"),
        vat_amount=Decimal("0"),
        total_excl_vat=Decimal("40"),
        vat_rate=Decimal("0"),
        discount_amount=Decimal("0"),
    )
    db.add(order)
    await db.flush()
    db.add(
        GrubOpsOrderMap(
            grubops_order_id=f"g-{uuid.uuid4().hex[:8]}",
            external_id=external,
            source_channel="Careem",
            mm_order_id=order.id,
        )
    )
    await db.flush()
    return order, external


async def test_find_mm_order_brings_the_legal_entity_on_its_row(Session, statements):
    async with Session() as db:
        entity = await _fatema(db)
        order, external = await _grubops_order(db, legal_entity_id=entity.id)
        order_id, entity_id = order.id, entity.id
        db.expunge_all()

        statements.clear()
        found = await reconcile._find_mm_order(db, "careem", external)
        queries = len(_selects(statements))
        assert found is not None and found.id == order_id
        assert not inspect(found).unloaded & {"legal_entity", "items"}
        statements.clear()
        assert found.legal_entity.id == entity_id
        assert found.items == []
        assert statements == []

        # The same statement with the model's default loading, for the record.
        db.expunge_all()
        statements.clear()
        await db.scalar(
            select(Order)
            .join(GrubOpsOrderMap, GrubOpsOrderMap.mm_order_id == Order.id)
            .where(GrubOpsOrderMap.external_id == external)
            .options(selectinload(Order.items))
        )
        before = len(_selects(statements))
        print(f"\n_find_mm_order: default {before} queries, joined {queries}")
        # Order + items + legal entity before; order (legal entity joined) +
        # items now — the saving is exactly the legal-entity round-trip.
        assert (before, queries) == (3, 2)
        await db.rollback()


async def test_find_mm_order_without_a_legal_entity(Session, statements):
    async with Session() as db:
        order, external = await _grubops_order(db, legal_entity_id=None)
        db.expunge_all()
        found = await reconcile._find_mm_order(db, "careem", external)
        assert found is not None and found.id == order.id
        statements.clear()
        assert found.legal_entity is None
        assert statements == []
        await db.rollback()


async def test_convergence_order_brings_the_legal_entity_on_its_row(
    Session, statements
):
    async with Session() as db:
        entity = await _fatema(db)
        order, external = await _grubops_order(db, legal_entity_id=entity.id)
        order_id = order.id
        db.expunge_all()
        agg = SimpleNamespace(
            external_order_id=external,
            display_ref=None,
            branch_id=None,
            business_date=None,
            channel="careem",
        )
        statements.clear()
        found = await promote._find_convergence_order(db, agg)
        assert found is not None and found.id == order_id
        assert len(_selects(statements)) == 1
        statements.clear()
        assert found.legal_entity.reference == "fatema"
        assert statements == []
        await db.rollback()


# ─── Auth ─────────────────────────────────────────────────────────────────────


async def test_auth_loads_the_user_and_role_in_one_query(Session, statements):
    async with Session() as db:
        role = Role(
            name=f"refq {uuid.uuid4().hex[:8]}", permissions=["pos.orders.view"]
        )
        db.add(role)
        await db.flush()
        user = User(
            email=f"refq-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_staff=True,
            is_active=True,
            role_id=role.id,
        )
        db.add(user)
        await db.flush()
        token = create_access_token(str(user.id), user.email)
        user_id = user.id
        db.expunge_all()

        statements.clear()
        resolved = await deps._get_user_from_token(token, db)
        assert len(_selects(statements)) == 1
        assert resolved is not None and resolved.id == user_id
        assert "role" not in inspect(resolved).unloaded
        statements.clear()
        assert resolved.can("pos.orders.view")
        assert not resolved.can("admin.branches.manage")
        assert resolved.permissions == ["pos.orders.view"]
        assert statements == []

        # A user with no role still resolves, role None, in one query.
        role_less = User(
            email=f"refq-nr-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_active=True,
        )
        db.add(role_less)
        await db.flush()
        token = create_access_token(str(role_less.id), role_less.email)
        db.expunge_all()
        statements.clear()
        resolved = await deps._get_user_from_token(token, db)
        assert len(_selects(statements)) == 1
        assert resolved.role is None and not resolved.can("pos.orders.view")
        await db.rollback()


# ─── business_settings through reference_cache ────────────────────────────────


@pytest.fixture
async def settings_row(Session):
    """The one `business_settings` row, created if the database has none, and
    put back exactly as it was afterwards."""
    async with Session() as db:
        row = await db.scalar(select(BusinessSettings).limit(1))
        created = row is None
        if created:
            row = BusinessSettings()
            db.add(row)
            await db.flush()
        row_id, original = row.id, row.timezone
        await db.commit()
    reference_cache.clear()
    yield row_id
    async with Session() as db:
        if created:
            await db.execute(
                BusinessSettings.__table__.delete().where(BusinessSettings.id == row_id)
            )
        else:
            await db.execute(
                update(BusinessSettings)
                .where(BusinessSettings.id == row_id)
                .values(timezone=original)
            )
        await db.commit()
    reference_cache.clear()


async def _tz(Session) -> str:
    async with Session() as db:
        return str(await business_day_service.resolve_timezone(db))


def _settings_reads(sql: list[str]) -> int:
    return sum("FROM business_settings" in s for s in _selects(sql))


async def test_the_timezone_is_read_once_per_change(Session, statements, settings_row):
    async with Session() as db:
        await db.execute(
            update(BusinessSettings)
            .where(BusinessSettings.id == settings_row)
            .values(timezone="Asia/Dubai")
        )
        await db.commit()

    statements.clear()
    assert await _tz(Session) == "Asia/Dubai"
    assert await _tz(Session) == "Asia/Dubai"
    assert await _tz(Session) == "Asia/Dubai"
    assert _settings_reads(statements) == 1, "three requests, one read"


async def test_an_orm_edit_is_seen_on_the_next_read(Session, statements, settings_row):
    await _tz(Session)
    async with Session() as db:
        (await db.get(BusinessSettings, settings_row)).timezone = "Asia/Riyadh"
        await db.commit()
    statements.clear()
    assert await _tz(Session) == "Asia/Riyadh"
    assert _settings_reads(statements) == 1


async def test_a_bulk_update_is_seen_on_the_next_read(Session, settings_row):
    await _tz(Session)
    async with Session() as db:
        await db.execute(
            update(BusinessSettings)
            .where(BusinessSettings.id == settings_row)
            .values(timezone="Asia/Muscat")
        )
        await db.commit()
    assert await _tz(Session) == "Asia/Muscat"


async def test_a_session_reads_its_own_uncommitted_write(Session, settings_row):
    async with Session() as db:
        await db.execute(
            update(BusinessSettings)
            .where(BusinessSettings.id == settings_row)
            .values(timezone="Asia/Dubai")
        )
        await db.commit()
    assert await _tz(Session) == "Asia/Dubai"  # cached

    async with Session() as db:
        (await db.get(BusinessSettings, settings_row)).timezone = "Asia/Kolkata"
        await db.flush()
        assert str(await business_day_service.resolve_timezone(db)) == "Asia/Kolkata"
        # Everyone else still sees the committed row until this commits …
        assert await _tz(Session) == "Asia/Dubai"
        await db.rollback()
    # … and a rollback leaves it as it was.
    assert await _tz(Session) == "Asia/Dubai"


async def test_a_pending_unflushed_edit_is_autoflushed_and_read_back(
    Session, settings_row
):
    """The query the cache replaces autoflushed first; the cached read does too,
    so an edit made but not yet flushed is still what the session reads."""
    async with Session() as db:
        await db.execute(
            update(BusinessSettings)
            .where(BusinessSettings.id == settings_row)
            .values(timezone="Asia/Dubai")
        )
        await db.commit()
    assert await _tz(Session) == "Asia/Dubai"  # cached

    async with Session() as db:
        (await db.get(BusinessSettings, settings_row)).timezone = "Asia/Tehran"
        assert str(await business_day_service.resolve_timezone(db)) == "Asia/Tehran"
        await db.rollback()

    async with Session() as db:
        row = await db.get(BusinessSettings, settings_row)
        with db.no_autoflush:
            row.timezone = "Asia/Tokyo"
            # Inside `no_autoflush` the edit stays pending, and the session's
            # own instance — what the old query returned from the identity
            # map — is what is read, edit and all.
            tz = await business_day_service.resolve_timezone(db)
            assert str(tz) == "Asia/Tokyo"
            assert db.dirty, "not flushed"
        await db.rollback()
    assert await _tz(Session) == "Asia/Dubai"


async def test_a_write_inside_a_savepoint_only_lands_with_the_outer_commit(
    Session, settings_row
):
    await _tz(Session)
    async with Session() as db:
        async with db.begin_nested():
            (await db.get(BusinessSettings, settings_row)).timezone = "Asia/Baku"
        # The savepoint released: not committed yet, so not retired either.
        assert await _tz(Session) != "Asia/Baku"
        await db.commit()
    assert await _tz(Session) == "Asia/Baku"


async def test_raw_sql_is_picked_up_within_the_ttl(Session, settings_row, monkeypatch):
    async with Session() as db:
        await db.execute(
            update(BusinessSettings)
            .where(BusinessSettings.id == settings_row)
            .values(timezone="Asia/Dubai")
        )
        await db.commit()
    assert await _tz(Session) == "Asia/Dubai"
    async with Session() as db:
        await db.execute(
            text("UPDATE business_settings SET timezone = 'Asia/Qatar' WHERE id = :id"),
            {"id": settings_row},
        )
        await db.commit()
    assert await _tz(Session) == "Asia/Dubai", "raw SQL is invisible to the events"
    monkeypatch.setattr(reference_cache, "TTL_CEILING", 0.0)
    assert await _tz(Session) == "Asia/Qatar"


async def test_the_cached_row_is_read_only_plain_values(Session, settings_row):
    async with Session() as db:
        row = await reference_cache.business_settings_row(db)
    assert row is not None and row["id"] == settings_row
    with pytest.raises(TypeError):
        row["timezone"] = "Europe/London"  # type: ignore[index]
    assert not any(hasattr(v, "_sa_instance_state") for v in row.values())


async def test_an_unopened_lazy_session_uses_the_cache(
    Session, statements, settings_row
):
    await _tz(Session)
    lazy = deps._LazyDBSession()
    statements.clear()
    await business_day_service.resolve_timezone(lazy)
    assert statements == []
    assert not lazy.opened, "a cache hit checks no connection out"


async def test_a_non_session_reads_straight_through(mock_db):
    mock_db.execute.return_value.scalar_one_or_none.return_value = None
    assert str(await business_day_service.resolve_timezone(mock_db)) == "Asia/Dubai"
    assert str(await business_day_service.resolve_timezone(mock_db)) == "Asia/Dubai"
    assert mock_db.execute.await_count == 2


# ─── The route sweep's read ───────────────────────────────────────────────────


async def test_the_route_sweep_reads_deliveries_orders_and_branches_in_two_queries(
    engine, statements, monkeypatch
):
    """`driver_routing.refresh_routes` phase 1: the deliveries, then their
    orders with the branch (and everything both load by default) joined on —
    two statements where the model defaults issue eight — and the coordinates
    it hands Mapbox are the branch's."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        factory = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as db:
            entity = await _fatema(db)
            branch_id = await _integrated_branch(db)
            branch = await db.get(Branch, branch_id)
            branch.latitude = Decimal("25.2")
            branch.longitude = Decimal("55.3")
            order, _external = await _grubops_order(db, legal_entity_id=entity.id)
            order.branch_id = branch_id
            delivery = OrderDelivery(
                order_id=order.id,
                provider="slider_bike",
                courier_status="assigned",
                driver_name="Rider",
                driver_latitude=Decimal("25.1"),
                driver_longitude=Decimal("55.1"),
                driver_location_at=utcnow(),
            )
            db.add(delivery)
            await db.flush()
            delivery_id = delivery.id
            # Releases the savepoint only; `outer` still rolls it all back.
            await db.commit()

        calls: list[dict] = []

        async def route(**kwargs):
            calls.append(kwargs)
            return None  # no answer: nothing is written back

        monkeypatch.setattr(driver_routing, "SchedulerSessionFactory", factory)
        monkeypatch.setattr(
            driver_routing.mapbox_provider, "is_configured", lambda: True
        )
        monkeypatch.setattr(driver_routing.mapbox_provider, "route", route)

        statements.clear()
        await driver_routing.refresh_routes(limit=1000)
        reads = _selects(statements)
        await outer.rollback()

    ours = [
        c
        for c in calls
        if (c["from_latitude"], c["from_longitude"]) == (25.1, 55.1)
        and (c["to_latitude"], c["to_longitude"]) == (25.2, 55.3)
    ]
    assert ours, f"delivery {delivery_id} routed from the rider to its branch"
    print(f"\nroute sweep read: {len(reads)} queries")
    assert len(reads) == 2
