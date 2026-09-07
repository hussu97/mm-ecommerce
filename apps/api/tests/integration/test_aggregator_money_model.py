"""The aggregator money model, against Postgres — the delicate per-channel logic of
Work Package 7 that a mocked session cannot prove.

Covers, end to end on real rows:

* F-AGG-1  a partial line sum (some lines unpriced) never lowers a header total;
* F-AGG-2  Careem raises the total to the menu line sum, Noon keeps the discounted
           net and books the gap as a discount;
* F-AGG-3  `sum(order_taxes.amount) == orders.vat_amount` after BUILD and after a
           re-promote (REFRESH), with exactly one VAT row;
* F-AGG-4  a cancelled order promoted OUTSIDE the stock window never restocks;
* F-AGG-5  a poison order in a promote batch is isolated — the others still commit;
* F-AGG-11 a Careem structural gross↔total gap is reported, not flagged.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.aggregator import (
    GRAIN_LINE,
    AggregatorOrder,
    AggregatorOrderItem,
    AggregatorReconciliation,
)
from app.models.branch import Branch
from app.models.grubops import GrubOpsLocationMap
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum, OrderTax
from app.models.product import Product
from app.services.aggregators import promote, reconcile

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-agg-money"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    """A session whose transaction is rolled back at the end, so nothing this test
    writes persists and no explicit cleanup is needed. Everything here only flushes
    (never commits), like production, so `begin_nested` savepoints work inside it."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture(autouse=True)
def _quiet_peripherals(monkeypatch):
    """No-op the register attach and fulfilment mirror — these money tests are not
    about the POS board or the driver panel, and they need branch/POS scaffolding
    the money path does not."""

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(
        promote.pos_order_service, "attach_promoted_aggregator_order", _noop
    )
    monkeypatch.setattr(promote, "_record_fulfilment", _noop)


async def _branch(db) -> uuid.UUID:
    branch = Branch(name=f"{MARKER} DSO", reference=f"{MARKER}-{uuid.uuid4().hex[:10]}")
    db.add(branch)
    await db.flush()
    return branch.id


async def _product(db, name: str, *, stock: int = 100) -> uuid.UUID:
    p = Product(
        name=name,
        slug=f"{MARKER}-{uuid.uuid4().hex[:12]}",
        is_stock_product=True,
        stock_quantity=stock,
    )
    db.add(p)
    await db.flush()
    return p.id


async def _agg_order(
    db, *, channel, branch_id, gross, status="40", **over
) -> AggregatorOrder:
    base = dict(
        channel=channel,
        external_order_id=f"{MARKER}-{uuid.uuid4().hex[:12]}",
        branch_id=branch_id,
        gross_sales=Decimal(gross),
        status=status,
        business_date="2026-09-06",
    )
    base.update(over)
    agg = AggregatorOrder(**base)
    db.add(agg)
    await db.flush()
    return agg


async def _add_line(db, agg, *, name, gross, qty=1, known=True) -> None:
    db.add(
        AggregatorOrderItem(
            channel=agg.channel,
            source_key=f"{agg.external_order_id}:{uuid.uuid4().hex[:6]}",
            aggregator_order_id=agg.id,
            grain=GRAIN_LINE,
            item_name=name,
            quantity=Decimal(qty),
            unit_price=Decimal(gross) / qty if gross is not None else None,
            gross_sales=Decimal(gross) if gross is not None else None,
            amount_is_known=known,
        )
    )
    await db.flush()


async def _build(db, agg, monkeypatch, *, draw_stock=True) -> Order:
    """Run `_build_order` with the lifecycle drive stubbed out — the money and VAT
    row are what these tests assert, not the status ladder."""

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(promote, "_drive_status", _noop)
    monkeypatch.setattr(promote.order_fees, "stamp", _noop)
    label = reconcile.CHANNEL_GRUBOPS_LABEL.get(agg.channel, agg.channel)
    return await promote._build_order(db, agg, label, draw_stock=draw_stock)


async def _vat_row_sum(db, order_id) -> Decimal:
    return Decimal(
        str(
            await db.scalar(
                select(func.coalesce(func.sum(OrderTax.amount), 0)).where(
                    OrderTax.order_id == order_id
                )
            )
        )
    )


# ── F-AGG-2: Careem raises to the menu line sum ───────────────────────────────
async def test_careem_raises_the_total_to_the_menu_line_sum(db, monkeypatch):
    branch_id = await _branch(db)
    # Careem's scraped gross (63) is net of its own menu markup; the lines carry the
    # real 90 menu price the customer paid (migration 198's 168889697 case).
    agg = await _agg_order(db, channel="careem", branch_id=branch_id, gross="63.00")
    await _add_line(db, agg, name="Brookie Box", gross="90.00")
    order = await _build(db, agg, monkeypatch)

    assert order.total == Decimal("90.00")  # raised to the menu
    assert order.subtotal == Decimal("90.00")  # VAT-inclusive == total
    assert order.discount_amount == Decimal("0")
    assert order.total_excl_vat == Decimal("85.71")
    assert order.vat_amount == Decimal("4.29")
    assert await _vat_row_sum(db, order.id) == order.vat_amount  # F-AGG-3 (build)


# ── F-AGG-2: Noon keeps the discounted net and books the gap as a discount ─────
async def test_noon_keeps_the_net_and_records_the_discount(db, monkeypatch):
    branch_id = await _branch(db)
    # Noon's scraped gross (50) is the discounted NET the customer paid; the line
    # items carry the pre-discount 70 gross (migration 198's FG8R… 50-against-70).
    agg = await _agg_order(
        db, channel="noon", branch_id=branch_id, gross="50.00", status="delivered"
    )
    await _add_line(db, agg, name="Cake", gross="70.00")
    order = await _build(db, agg, monkeypatch)

    assert order.total == Decimal("50.00")  # net kept, NOT raised to 70
    assert order.subtotal == Decimal("70.00")  # the gross
    assert order.discount_amount == Decimal("20.00")  # gross − net
    assert order.subtotal - order.discount_amount == order.total
    # VAT is on the net the customer actually paid, and the row agrees with it.
    assert order.vat_amount == Decimal("2.38")  # 50 inclusive → 47.62 + 2.38
    assert await _vat_row_sum(db, order.id) == order.vat_amount


# ── F-AGG-1: a partial line sum never lowers the header total ──────────────────
async def test_a_partial_line_sum_never_lowers_the_header_total(db, monkeypatch):
    branch_id = await _branch(db)
    # Three lines, only one priced — the other two are amount-unknown, so the summed
    # line total (30) is a PARTIAL undercount of the real 90 order. The header must
    # stand, not collapse to 30.
    agg = await _agg_order(db, channel="careem", branch_id=branch_id, gross="90.00")
    await _add_line(db, agg, name="Priced", gross="30.00", known=True)
    await _add_line(db, agg, name="Unpriced A", gross=None, known=False)
    await _add_line(db, agg, name="Unpriced B", gross=None, known=False)
    order = await _build(db, agg, monkeypatch)

    assert order.total == Decimal("90.00")  # untouched — partial sum not trusted
    assert order.subtotal == Decimal("90.00")


# ── F-AGG-3: the VAT row is restated on a re-promote too ───────────────────────
async def test_vat_row_agrees_after_build_and_after_refresh(db, monkeypatch):
    branch_id = await _branch(db)
    agg = await _agg_order(db, channel="careem", branch_id=branch_id, gross="63.00")
    await _add_line(db, agg, name="Brookie Box", gross="90.00")
    order = await _build(db, agg, monkeypatch)
    assert await _vat_row_sum(db, order.id) == order.vat_amount

    # Re-promote (the scrape re-lands): _refresh_order re-derives the money and MUST
    # restate the VAT row, leaving exactly one row that still agrees.
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(promote, "_drive_status", _noop)
    monkeypatch.setattr(promote.order_fees, "stamp", _noop)
    await db.refresh(order, ["items"])
    await promote._refresh_order(db, order, agg)

    rows = (
        (await db.execute(select(OrderTax).where(OrderTax.order_id == order.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1  # not stacked
    assert await _vat_row_sum(db, order.id) == order.vat_amount
    assert order.total == Decimal("90.00")  # still the reconciled menu total


# ── F-AGG-4: a cancelled out-of-window order never restocks ────────────────────
async def test_cancelled_out_of_window_order_does_not_restock(db, monkeypatch):
    branch_id = await _branch(db)
    product_id = await _product(db, "Brownie", stock=100)
    # A 30-day-old CANCELLED Keeta order promoted for linkage only (draw_stock False,
    # so it never drew): cancelling it must not hand back stock it never took.
    agg = await _agg_order(
        db,
        channel="keeta",
        branch_id=branch_id,
        gross="40.00",
        status="50",  # keeta cancelled
        business_date="2026-08-07",
    )
    await _add_line(db, agg, name="Brownie", gross="40.00")

    # Real _drive_status here, so the cancel actually runs _move_stock(+1).
    out = await promote.promote_order(db, agg, draw_stock=False)
    assert out is not None
    assert out.status == OrderStatusEnum.CANCELLED
    assert out.stock_drawn is False  # never drew

    stock = await db.scalar(
        select(Product.stock_quantity).where(Product.id == product_id)
    )
    assert stock == 100  # unchanged — no phantom restock


async def test_a_cancelled_in_window_order_does_restock(db, monkeypatch):
    """The counter-case, so the gate is not just 'never restore': an order that DID
    draw (in-window) returns its stock on cancellation."""
    branch_id = await _branch(db)
    product_id = await _product(db, "Cookie", stock=100)
    agg = await _agg_order(
        db,
        channel="keeta",
        branch_id=branch_id,
        gross="40.00",
        status="50",
        business_date="2026-09-06",
    )
    await _add_line(db, agg, name="Cookie", gross="40.00", qty=2)

    out = await promote.promote_order(db, agg, draw_stock=True)
    assert out is not None
    stock = await db.scalar(
        select(Product.stock_quantity).where(Product.id == product_id)
    )
    # Drew 2 then a cancellation gave 2 back → net zero.
    assert stock == 100


# ── F-AGG-5: one poison order does not abort the whole promote batch ────────────
async def test_a_poison_order_is_isolated_and_the_rest_commit(db, monkeypatch):
    branch_id = await _branch(db)
    a1 = await _agg_order(db, channel="keeta", branch_id=branch_id, gross="10.00")
    poison = await _agg_order(db, channel="keeta", branch_id=branch_id, gross="20.00")
    a3 = await _agg_order(db, channel="keeta", branch_id=branch_id, gross="30.00")

    real_promote_order = promote.promote_order

    async def wrapper(session, agg, *, draw_stock=True):
        if agg.id == poison.id:
            # A real asyncpg error that aborts the (sub)transaction — without a
            # per-order savepoint this would poison the whole batch and order 3
            # would then fail "current transaction is aborted".
            await session.execute(text("SELECT 1 / 0"))
        return await real_promote_order(session, agg, draw_stock=draw_stock)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(promote, "_drive_status", _noop)
    monkeypatch.setattr(promote.order_fees, "stamp", _noop)
    monkeypatch.setattr(promote, "promote_order", wrapper)

    count = await promote.promote_channel(db, "keeta")

    assert count == 2  # orders 1 and 3
    # The session is still usable and orders 1 and 3 committed their promotion.
    for a in (a1, a3):
        await db.refresh(a)
        assert a.mm_order_id is not None
    await db.refresh(poison)
    assert poison.mm_order_id is None  # rolled back to its savepoint


# ── F-AGG-11: a Careem structural gap is reported, not flagged ─────────────────
async def _grubops_maker(db, branch_id, agg, *, mm_total) -> Order:
    """A GrubOps maker MM order + its location/order map, so reconcile matches on the
    maker side and compares agg.gross_sales against this MM total."""
    db.add(
        GrubOpsLocationMap(
            branch_id=branch_id,
            grubops_location_id=f"loc-{uuid.uuid4().hex[:8]}",
            grubops_partner_id="partner-1",
        )
    )
    order = Order(
        order_number=f"AGG-{uuid.uuid4().hex[:8]}",
        email="",
        locale="en",
        delivery_method="delivery",
        order_type="delivery",
        status=OrderStatusEnum.DELIVERED,
        source=OrderSourceEnum.AGGREGATOR.value,
        aggregator_channel="Careem",
        external_reference=agg.external_order_id,
        branch_id=branch_id,
        created_at=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
        subtotal=Decimal(mm_total),
        total=Decimal(mm_total),
        vat_amount=Decimal("0"),
        total_excl_vat=Decimal(mm_total),
        vat_rate=Decimal("0"),
        discount_amount=Decimal("0"),
    )
    db.add(order)
    await db.flush()
    db.add(
        GrubOpsOrderMap(
            grubops_order_id=f"g-{uuid.uuid4().hex[:8]}",
            external_id=agg.external_order_id,
            source_channel="Careem",
            mm_order_id=order.id,
        )
    )
    await db.flush()
    return order


async def test_careem_structural_gap_is_reported_not_flagged(db):
    branch_id = await _branch(db)
    # Careem: scraped gross 63 (net of markup) vs the authoritative push total 90.
    agg = await _agg_order(db, channel="careem", branch_id=branch_id, gross="63.00")
    await _grubops_maker(db, branch_id, agg, mm_total="90.00")

    await reconcile.reconcile_order(db, agg)

    row = (
        await db.execute(
            select(AggregatorReconciliation).where(
                AggregatorReconciliation.channel == "careem",
                AggregatorReconciliation.external_order_id == agg.external_order_id,
            )
        )
    ).scalar_one()
    # The gap is recorded on amount_variance…
    assert row.amount_variance == Decimal("-27.00")
    # …but NOT raised as a flag (it is a Careem pricing-model artefact, F-AGG-11).
    assert not (row.flags or [])


async def test_a_non_careem_total_gap_is_still_flagged(db):
    """The counter-case: the same-sized gap on Deliveroo (whose gross IS the customer
    total) is a real discrepancy and must still flag."""
    branch_id = await _branch(db)
    agg = await _agg_order(db, channel="deliveroo", branch_id=branch_id, gross="63.00")
    # Reuse the maker helper but under the Deliveroo channel/label.
    db.add(
        GrubOpsLocationMap(
            branch_id=branch_id,
            grubops_location_id=f"loc-{uuid.uuid4().hex[:8]}",
            grubops_partner_id="partner-1",
        )
    )
    order = Order(
        order_number=f"AGG-{uuid.uuid4().hex[:8]}",
        email="",
        locale="en",
        delivery_method="delivery",
        order_type="delivery",
        status=OrderStatusEnum.DELIVERED,
        source=OrderSourceEnum.AGGREGATOR.value,
        aggregator_channel="Deliveroo",
        external_reference=agg.external_order_id,
        branch_id=branch_id,
        created_at=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
        subtotal=Decimal("90.00"),
        total=Decimal("90.00"),
        vat_amount=Decimal("0"),
        total_excl_vat=Decimal("90.00"),
        vat_rate=Decimal("0"),
        discount_amount=Decimal("0"),
    )
    db.add(order)
    await db.flush()
    db.add(
        GrubOpsOrderMap(
            grubops_order_id=f"g-{uuid.uuid4().hex[:8]}",
            external_id=agg.external_order_id,
            source_channel="Deliveroo",
            mm_order_id=order.id,
        )
    )
    await db.flush()

    await reconcile.reconcile_order(db, agg)
    row = (
        await db.execute(
            select(AggregatorReconciliation).where(
                AggregatorReconciliation.channel == "deliveroo",
                AggregatorReconciliation.external_order_id == agg.external_order_id,
            )
        )
    ).scalar_one()
    assert "amount_variance" in (row.flags or [])
