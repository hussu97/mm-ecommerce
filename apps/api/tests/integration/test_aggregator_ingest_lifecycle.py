"""Run-lifecycle and channel-key behaviour against Postgres — the parts a mocked
session cannot prove.

Covers:

* F-AGG-7  `reap_stale_runs` fails runs stuck 'running' past the cutoff and leaves
           fresh ones alone; `_open_run` commits a run row on its own session.
* F-AGG-8  `promote_channel(since=…)` promotes an order older than the rolling
           30-day clip that the default pass skips; `_unpromotable_backlog` counts it.
* F-AGG-9  a GrubOps-branch sale promoted past the adopt grace, then pushed in by
           GrubOps, resolves to EXACTLY ONE MM order — both writers spell
           `aggregator_channel` as the canonical code, so the widened unique key
           finds the promoted row instead of filing a duplicate.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.aggregator import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_MODE_SALES,
    RUN_RUNNING,
    STATEMENT_GRAIN_ORDER,
    AggregatorOrder,
    AggregatorStatementLine,
    AggregatorSyncRun,
)
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.grubops import GrubOpsLocationMap
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.order import Order
from app.models.pos_order import OrderSourceEnum
from app.models.product import Product
from app.services.aggregators import ingest, promote, reconcile

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-agg-lifecycle"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    """A rolled-back session, like production it only flushes (never commits), so
    `begin_nested` savepoints work and nothing this test writes persists."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture(autouse=True)
def _quiet_peripherals(monkeypatch):
    """No-op the register attach, fulfilment mirror, status drive and fee stamp —
    these tests are about run rows and the channel key, not the POS board."""

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(
        promote.pos_order_service, "attach_promoted_aggregator_order", _noop
    )
    monkeypatch.setattr(promote, "_record_fulfilment", _noop)
    monkeypatch.setattr(promote, "_drive_status", _noop)
    monkeypatch.setattr(promote.order_fees, "stamp", _noop)


async def _branch(db) -> uuid.UUID:
    branch = Branch(name=f"{MARKER}", reference=f"{MARKER}-{uuid.uuid4().hex[:10]}")
    db.add(branch)
    await db.flush()
    return branch.id


async def _agg_order(db, *, channel, branch_id, **over) -> AggregatorOrder:
    base = dict(
        channel=channel,
        external_order_id=f"{MARKER}-{uuid.uuid4().hex[:12]}",
        branch_id=branch_id,
        gross_sales=Decimal("50.00"),
        status="delivered",
        business_date="2026-08-01",
    )
    base.update(over)
    agg = AggregatorOrder(**base)
    db.add(agg)
    await db.flush()
    return agg


# ── F-AGG-7: reap stale 'running' runs, spare the fresh ones ───────────────────
async def test_reap_stale_runs_fails_only_the_orphaned_rows(engine, monkeypatch):
    # reap_stale_runs opens its OWN committed session via the module factory. Point
    # that factory at THIS test's engine so reap runs on the test's event loop and
    # the test DB — not the app scheduler pool, whose connection is bound to
    # whichever earlier test's loop first used it ("attached to a different loop"),
    # and whose URL is the app default, not the test DB. Seed + assert + clean up
    # on the same engine, never through the rolled-back `db` fixture reap can't see.
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ingest, "AsyncSessionFactory", Session)
    ids: dict[str, uuid.UUID] = {}
    async with Session() as s:
        stale = AggregatorSyncRun(
            channel="careem",
            mode=RUN_MODE_SALES,
            status=RUN_RUNNING,
            started_at=utcnow() - timedelta(hours=3),
        )
        fresh = AggregatorSyncRun(
            channel="noon",
            mode=RUN_MODE_SALES,
            status=RUN_RUNNING,
            started_at=utcnow() - timedelta(minutes=5),
        )
        done = AggregatorSyncRun(
            channel="talabat",
            mode=RUN_MODE_SALES,
            status=RUN_COMPLETED,
            started_at=utcnow() - timedelta(hours=5),
            finished_at=utcnow() - timedelta(hours=5),
        )
        s.add_all([stale, fresh, done])
        await s.commit()
        ids = {"stale": stale.id, "fresh": fresh.id, "done": done.id}
    try:
        reaped = await ingest.reap_stale_runs()
        assert reaped >= 1  # at least our 3h-old 'running' row (suite may add others)

        async with Session() as s:
            rows = {
                r.id: r
                for r in (
                    await s.scalars(
                        select(AggregatorSyncRun).where(
                            AggregatorSyncRun.id.in_(ids.values())
                        )
                    )
                ).all()
            }
        assert rows[ids["stale"]].status == RUN_FAILED
        assert rows[ids["stale"]].finished_at is not None
        assert "reaped" in (rows[ids["stale"]].error or "")
        assert rows[ids["fresh"]].status == RUN_RUNNING  # too young to reap
        assert rows[ids["done"]].status == RUN_COMPLETED  # already terminal
    finally:
        async with Session() as s:
            await s.execute(
                AggregatorSyncRun.__table__.delete().where(
                    AggregatorSyncRun.id.in_(ids.values())
                )
            )
            await s.commit()


# ── F-AGG-8: a ranged backfill promotes below the rolling clip ─────────────────
async def test_promote_since_reaches_orders_the_default_clip_skips(db, monkeypatch):
    branch_id = await _branch(db)
    # An order well older than AGGREGATOR_PROMOTE_LOOKBACK_DAYS — the daily clip can
    # never reach it (this is the shape of the 831 stranded Keeta orders).
    old_day = (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
    agg = await _agg_order(
        db, channel="keeta", branch_id=branch_id, business_date=old_day
    )

    # Default (clipped) pass leaves it untouched…
    touched_default = await promote.promote_channel(db, "keeta")
    await db.refresh(agg)
    assert touched_default == 0
    assert agg.mm_order_id is None

    # …a ranged backfill with since= reaching back promotes it (no stock drawn, being
    # far outside the sales window — filed for linkage only).
    since = datetime.now(timezone.utc).date() - timedelta(days=420)
    touched_since = await promote.promote_channel(db, "keeta", since=since)
    await db.refresh(agg)
    assert touched_since == 1
    assert agg.mm_order_id is not None


# ── F-AGG-11: promotion is incremental — a re-promote does not re-select ───────
async def test_promotion_does_not_re_select_the_same_order_next_sweep(db, monkeypatch):
    """The incremental promote cursor (`updated_at > promoted_at`) must go quiet once
    an order is promoted. Promotion writes `promoted_at`/`mm_order_id`, and the ORM's
    `onupdate=utcnow` on `updated_at` used to bump it to a hair AFTER `promoted_at` on
    that same flush — so every order re-selected on the very next sweep and the whole
    lookback window re-promoted continuously, pinning CPU. Promotion must advance
    `promoted_at` without touching `updated_at`."""
    branch_id = await _branch(db)
    today = datetime.now(timezone.utc).date().isoformat()
    agg = await _agg_order(
        db, channel="keeta", branch_id=branch_id, business_date=today
    )

    touched_first = await promote.promote_channel(db, "keeta")
    await db.refresh(agg)
    assert touched_first == 1
    assert agg.promoted_at is not None
    assert agg.mm_order_id is not None
    updated_after_promote = agg.updated_at
    # The promotion must NOT have advanced updated_at past promoted_at.
    assert agg.updated_at <= agg.promoted_at

    # A second sweep must not re-select it (nothing scraped it since).
    touched_second = await promote.promote_channel(db, "keeta")
    await db.refresh(agg)
    assert touched_second == 0
    assert agg.updated_at == updated_after_promote


# ── F-AGG-8: the unpromotable backlog is counted ──────────────────────────────
async def test_unpromotable_backlog_counts_orders_below_the_clip(db, monkeypatch):
    monkeypatch.setattr(
        ingest, "AsyncSessionFactory", lambda: _same_conn(db), raising=True
    )
    branch_id = await _branch(db)
    old_day = (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
    # Two below-clip, unpromoted, with a branch → in the backlog.
    await _agg_order(db, channel="keeta", branch_id=branch_id, business_date=old_day)
    await _agg_order(db, channel="keeta", branch_id=branch_id, business_date=old_day)
    # Recent one is NOT backlog (the daily clip reaches it).
    today = datetime.now(timezone.utc).date().isoformat()
    await _agg_order(db, channel="keeta", branch_id=branch_id, business_date=today)
    await db.flush()

    backlog = await ingest._unpromotable_backlog()
    assert backlog.get("keeta", 0) >= 2


# ── F-AGG-9: one sale → one MM order across both writers ───────────────────────
async def test_promotion_then_grubops_push_file_exactly_one_order(db, monkeypatch):
    from types import SimpleNamespace

    from app.services.grubops import grubops_orders_service as g

    branch_id = await _branch(db)
    # A GrubOps branch (so promotion defers to GrubOps, then files a standalone once
    # the adopt grace has elapsed).
    db.add(
        GrubOpsLocationMap(
            branch_id=branch_id,
            grubops_location_id=f"{MARKER}-LOC",
            grubops_partner_id=f"{MARKER}-P",
        )
    )
    await db.flush()

    short = "6227"  # Noon's short externalId, mirrored onto aggregator_display_code
    long_id = f"{MARKER}-NOON-{uuid.uuid4().hex[:8]}"
    placed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)  # old → past adopt grace
    agg = await _agg_order(
        db,
        channel="noon",
        branch_id=branch_id,
        external_order_id=long_id,
        display_ref=short,
        placed_at=placed,
        business_date="2026-08-01",
    )

    # Promotion files a standalone MM order under the CANONICAL code.
    promoted = await promote.promote_order(db, agg, draw_stock=False)
    assert promoted is not None
    assert promoted.aggregator_channel == "noon_food"
    await db.flush()

    # The GrubOps push now lands for the SAME sale — GrubTech spells the channel
    # "Noon" and quotes only the short externalId. It must ADOPT the promoted order.
    order_map = SimpleNamespace(
        location_id=f"{MARKER}-LOC",
        external_id=short,
        grubops_order_id="G-AGG9",
        source_channel="Noon",
        last_push_error=None,
    )
    info = {
        "orderHeader": {
            "foodAggregatorName": "Noon",
            "externalId": short,
            "totalPrice": 50,
        },
        "orderLines": [],
        "customer": {},
    }
    monkeypatch.setattr(g, "_resolve_branch", _as_async(branch_id))
    monkeypatch.setattr(g, "_reverse_maps", _as_async(({}, {})))

    adopted = await g._create_order(db, info, order_map)
    await db.flush()

    assert adopted is not None
    assert adopted.id == promoted.id  # adopted the promotion, did not insert anew
    # Both writers agree on the one canonical spelling — the reason the adopt resolves
    # to the promoted row instead of inserting a second under the widened unique key.
    assert (
        reconcile.canonical_channel_code("Noon")
        == promoted.aggregator_channel
        == "noon_food"
    )

    # Exactly one aggregator MM order exists for this sale on this branch.
    n = await db.scalar(
        select(func.count())
        .select_from(Order)
        .where(
            Order.source == OrderSourceEnum.AGGREGATOR.value,
            Order.branch_id == branch_id,
        )
    )
    assert n == 1


# ── small helpers ─────────────────────────────────────────────────────────────
class _same_conn:
    """A context manager yielding the test's own session, so a helper that opens its
    own `AsyncSessionFactory()` writes on the connection the test rolls back."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        # The helper commits inside; on this shared session `commit` would end the
        # test's outer transaction, so swallow nothing and let the test roll back.
        return False


def _as_async(value):
    async def _fn(*a, **k):
        return value

    return _fn


# ── F-AGG-12: an aggregator modifier resolves against the product's own options ─
async def test_modifier_snapshot_resolves_scoped_product_option(db):
    """A scraped quantity modifier ("3 Pieces") with no external_ref and no global
    option map must still link to the option on THIS product that carries the recipe
    — the fix for aggregator lines like "Fudge Brownies" + "3 Pieces" landing
    modifier_option_id=null and drawing no stock."""
    from app.services.aggregators.normalized import StandardModifier

    product = Product(
        name=f"{MARKER} Fudge Brownies",
        slug=f"{MARKER}-{uuid.uuid4().hex[:12]}",
        is_active=True,
    )
    db.add(product)
    await db.flush()
    modifier = Modifier(
        reference=f"{MARKER}-{uuid.uuid4().hex[:10]}", name="Your Choice of Quantity"
    )
    db.add(modifier)
    await db.flush()
    opt = ModifierOption(
        modifier_id=modifier.id,
        name="3 Pieces",
        sku=f"{MARKER[:6]}-{uuid.uuid4().hex[:8]}",
        price=Decimal("50"),
    )
    db.add(opt)
    db.add(ProductModifier(product_id=product.id, modifier_id=modifier.id))
    await db.flush()

    mods = [
        StandardModifier(
            name="3 Pieces", quantity=Decimal("1"), unit_price=Decimal("50")
        )
    ]
    snap, _ = await promote._build_modifier_snapshot(
        db, "careem", mods, product_id=product.id
    )
    assert snap[0]["modifier_option_id"] == str(opt.id)
    assert snap[0]["option_id"] == str(opt.id)

    # Without the product scope, a bare name has nothing to resolve against.
    snap_none, _ = await promote._build_modifier_snapshot(
        db, "careem", mods, product_id=None
    )
    assert snap_none[0]["modifier_option_id"] is None

    # A modifier name the product does not offer stays unresolved (not a wrong guess).
    other = [StandardModifier(name="9 Pieces", quantity=Decimal("1"))]
    snap_miss, _ = await promote._build_modifier_snapshot(
        db, "careem", other, product_id=product.id
    )
    assert snap_miss[0]["modifier_option_id"] is None


# ── settlement backfill rolls VAT-inclusive commission onto the order ──────────
async def _stmt_line(
    db, *, channel, statement_id, external_order_id, fee_category, amount
):
    line = AggregatorStatementLine(
        channel=channel,
        source_key=f"{MARKER}-{uuid.uuid4().hex[:12]}",
        statement_id=statement_id,
        external_order_id=external_order_id,
        fee_category=fee_category,
        amount=Decimal(amount),
        line_date="2026-09-10",
        grain=STATEMENT_GRAIN_ORDER,
    )
    db.add(line)
    await db.flush()
    return line


async def test_settlement_backfill_fills_commission_gap_talabat_split(db):
    """A Talabat-shaped statement (ex-VAT commission + separate commission_vat)
    fills a fee-less order's commission with the VAT-INCLUSIVE magnitude."""
    branch_id = await _branch(db)
    stmt = f"{MARKER}-stmt-{uuid.uuid4().hex[:8]}"
    agg = await _agg_order(
        db,
        channel="talabat",
        branch_id=branch_id,
        commission_amount=Decimal("0"),
        net_payable=None,
        gross_sales=Decimal("40.00"),
    )
    oid = agg.external_order_id
    await _stmt_line(
        db,
        channel="talabat",
        statement_id=stmt,
        external_order_id=oid,
        fee_category="commission",
        amount="-12.00",
    )
    await _stmt_line(
        db,
        channel="talabat",
        statement_id=stmt,
        external_order_id=oid,
        fee_category="commission_vat",
        amount="-0.60",
    )
    await _stmt_line(
        db,
        channel="talabat",
        statement_id=stmt,
        external_order_id=oid,
        fee_category="net_payable",
        amount="26.42",
    )

    updated = await ingest.backfill_order_economics_from_statement(db, "talabat", stmt)
    assert updated >= 1
    await db.refresh(agg)
    assert agg.commission_amount == Decimal("12.60")  # 12.00 + 0.60, magnitude
    assert agg.net_payable == Decimal("26.42")


async def test_settlement_backfill_commission_noon_single_incl_line(db):
    """noon books one commission line already VAT-inclusive (no commission_vat) —
    rolled onto the order verbatim."""
    branch_id = await _branch(db)
    stmt = f"{MARKER}-stmt-{uuid.uuid4().hex[:8]}"
    agg = await _agg_order(
        db,
        channel="noon",
        branch_id=branch_id,
        commission_amount=None,
        gross_sales=Decimal("40.00"),
    )
    await _stmt_line(
        db,
        channel="noon",
        statement_id=stmt,
        external_order_id=agg.external_order_id,
        fee_category="commission",
        amount="10.50",
    )
    await ingest.backfill_order_economics_from_statement(db, "noon", stmt)
    await db.refresh(agg)
    assert agg.commission_amount == Decimal("10.50")


async def test_settlement_backfill_never_overwrites_sales_feed_commission(db):
    """A commission already stamped by the sales feed is a gap-fill no-op — the
    statement never clobbers it (they are equal by construction anyway)."""
    branch_id = await _branch(db)
    stmt = f"{MARKER}-stmt-{uuid.uuid4().hex[:8]}"
    agg = await _agg_order(
        db,
        channel="talabat",
        branch_id=branch_id,
        commission_amount=Decimal("12.60"),
        gross_sales=Decimal("40.00"),
    )
    await _stmt_line(
        db,
        channel="talabat",
        statement_id=stmt,
        external_order_id=agg.external_order_id,
        fee_category="commission",
        amount="-99.00",
    )  # deliberately wrong
    await ingest.backfill_order_economics_from_statement(db, "talabat", stmt)
    await db.refresh(agg)
    assert agg.commission_amount == Decimal("12.60")  # untouched
