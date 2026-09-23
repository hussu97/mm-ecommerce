"""
The counter pricing engine is one function, pinned by golden vectors.

Local-first checkout prices a counter sale on the iPad with a Swift port of
`counter_pricing`. Three properties keep the two — and the server-authoritative
path — from drifting apart:

1. The committed fixture (`tests/fixtures/counter_pricing_vectors.json`) is
   exactly what `scripts/export_counter_pricing_vectors.py` writes today. A
   change to the arithmetic that is not re-exported fails here, before a
   terminal can disagree with the server about a receipt.
2. The engine reproduces every committed `expected` from its `input` — the same
   check the Swift port runs over the same file.
3. `counter_pricing.price_check(ctx, …)` and `pos_order_service.recalculate(db,
   order, ctx=ctx)` — the writer the ingest re-prices a synced sale with — agree
   on every figure, over seeded random orders.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.money import money
from app.models.pos_order import OrderTax
from app.services.pos import counter_pricing as cp
from app.services.pos import pos_order_service, promotion_rules
from scripts import export_counter_pricing_vectors as vectors


def test_the_committed_fixture_is_what_the_exporter_writes():
    committed = vectors.OUT_PATH.read_text()
    assert committed == vectors.render(vectors.build()), (
        "tests/fixtures/counter_pricing_vectors.json is stale — run "
        "`python -m scripts.export_counter_pricing_vectors` and copy it into mm-pos"
    )


def test_the_fixture_is_small_enough_to_ship_in_the_app_tests():
    assert vectors.OUT_PATH.stat().st_size < 3_000_000


def test_every_committed_vector_reproduces():
    document = json.loads(vectors.OUT_PATH.read_text())
    assert document["engine_version"] == cp.ENGINE_VERSION
    assert document["count"] == len(document["vectors"]) > 1000
    names = [v["name"] for v in document["vectors"]]
    assert len(names) == len(set(names))
    assert sum(n.startswith("hand/") for n in names) >= 200
    for vector in document["vectors"]:
        assert vectors.price_vector(vector) == vector["expected"], vector["name"]


def test_the_fixture_exercises_every_branch_of_the_engine():
    document = json.loads(vectors.OUT_PATH.read_text())
    expected = [v["expected"] for v in document["vectors"]]
    promos = [e["promotion"] for e in expected if e["promotion"]]
    assert {p["mode"] for p in promos} == {"auto", "coupon"}
    assert {p["scope"] for p in promos} == {"order", "lines"}
    assert {p["is_percentage"] for p in promos} == {True, False}
    assert any(e["rounding"] != "0.00" for e in expected)
    assert any(len(e["taxes"]) > 1 for e in expected)
    assert any(not e["taxes"] and e["total"] != "0.00" for e in expected)


# ─── price_check == recalculate(ctx) ──────────────────────────────────────────

BRANCH = uuid.uuid4()
CATEGORIES = [uuid.uuid4() for _ in range(3)]
GROUPS = {
    uuid.uuid4(): cp.TaxTuple(Decimal("0.05"), "VAT 5%", str(uuid.uuid4()), True),
    uuid.uuid4(): cp.TaxTuple(Decimal("0.05"), "VAT ex", str(uuid.uuid4()), False),
    uuid.uuid4(): cp.TaxTuple(Decimal("0"), "Zero", str(uuid.uuid4()), True),
    uuid.uuid4(): cp.TaxTuple(Decimal("0.12"), "Combo", str(uuid.uuid4()), True),
}


def _context(rng: random.Random) -> cp.PricingContext:
    products = {}
    for _ in range(12):
        products[uuid.uuid4()] = cp.ProductFacts(
            category_id=rng.choice(CATEGORIES + [None]),
            tax_group_id=rng.choice(list(GROUPS) + [None]),
            is_non_revenue=rng.random() < 0.1,
        )
    promotions = []
    for rank in range(rng.choice([0, 1, 2, 3])):
        reward = rng.choice(["percentage_off_order", "fixed_off_order"])
        promotions.append(
            cp.promo_rule_from_wire(
                {
                    "id": str(uuid.uuid4()),
                    "name": f"P{rank}",
                    "reward": reward,
                    "reward_value": (
                        f"{rng.choice([10, 12.5, 15, 33.33, 50])}"
                        if reward == "percentage_off_order"
                        else f"{rng.randint(1, 3000) / 100:.2f}"
                    ),
                    "trigger_value": f"{rng.choice([0, 0, 20, 60]):.2f}",
                    "category_ids": (
                        [str(c) for c in rng.sample(CATEGORIES, 1)]
                        if rng.random() < 0.5
                        else []
                    ),
                    "sources": ["cashier"],
                    "mode": rng.choice(["auto", "auto", "coupon"]),
                    "rank": rank,
                },
                BRANCH,
            )
        )
    return cp.PricingContext(
        branch_id=BRANCH,
        timezone="Asia/Dubai",
        rounding_step=Decimal(rng.choice(["0", "0.25", "0.05"])),
        vat_registered=rng.random() < 0.85,
        tax_groups=GROUPS,
        products=products,
        promotions=tuple(promotions),
    )


def _lines(rng: random.Random, ctx: cp.PricingContext) -> list[cp.CheckLine]:
    lines = []
    for _ in range(rng.choice([1, 2, 3, 4, 6])):
        weighed = rng.random() < 0.1
        lines.append(
            cp.CheckLine(
                id=uuid.uuid4(),
                product_id=rng.choice(list(ctx.products)),
                quantity=rng.choice([1, 1, 2, 3, 5]),
                unit_price=Decimal(rng.randint(0, 20000)) / 100,
                options_price=Decimal(rng.choice([0, 0, rng.randint(0, 900)])) / 100,
                weight=Decimal(rng.randint(1, 2000)) / 1000 if weighed else None,
                voided=rng.random() < 0.05,
            )
        )
    return lines


def _order_for(lines: list[cp.CheckLine], coupon_id) -> SimpleNamespace:
    items = [
        SimpleNamespace(
            id=line.id,
            product_id=line.product_id,
            quantity=line.quantity,
            returned_quantity=line.returned_quantity,
            base_price=cp.line_base_price(line.unit_price, line.weight),
            options_price=money(line.options_price),
            status="void" if line.voided else "active",
        )
        for line in lines
    ]
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_pos=True,
        pos_status="active",
        source="cashier",
        branch_id=BRANCH,
        order_type="pickup",
        applied_coupon_promotion_id=coupon_id,
        items=items,
        order_discounts=[],
        order_charges=[],
        order_taxes=[],
        legal_entity_id=None,
    )


async def _recalculate(monkeypatch, order, ctx, at):
    added: list = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=added.append)
    monkeypatch.setattr(pos_order_service, "get_order", AsyncMock(return_value=order))
    await pos_order_service.recalculate(db, order, at=at, ctx=ctx)
    # recalculate(ctx) must not have reached for the database for an input.
    db.get.assert_not_called()
    db.execute.assert_not_called()
    return [a for a in added if isinstance(a, OrderTax)]


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(250))
async def test_price_check_equals_recalculate_with_the_same_bundle(monkeypatch, seed):
    rng = random.Random(seed)
    ctx = _context(rng)
    lines = _lines(rng, ctx)
    coupons = [p.id for p in ctx.promotions if p.coupon_branch_ids]
    coupon_id = rng.choice(coupons) if coupons and rng.random() < 0.6 else None
    at = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc) + timedelta(
        minutes=rng.randint(0, 1440)
    )

    pricing = cp.price_check(ctx, lines, at, coupon_id)
    order = _order_for(lines, coupon_id)
    taxes = await _recalculate(monkeypatch, order, ctx, at)

    assert order.subtotal == pricing.subtotal
    assert order.discount_amount == pricing.discount_total
    assert order.vat_amount == pricing.tax_total
    assert order.total_excl_vat == pricing.total_excl_tax
    assert order.rounding_amount == pricing.rounding
    assert order.total == pricing.total
    assert order.vat_rate == pricing.vat_rate

    live = [i for i in order.items if i.status != "void"]
    assert [i.id for i in live] == [p.id for p in pricing.lines]
    for item, priced in zip(live, pricing.lines):
        assert item.base_price == priced.base_price
        assert item.discount_amount == priced.discount
        assert item.total_price == priced.total_price
        assert item.tax_amount == priced.tax_amount
        assert item.tax_exclusive_total_price == priced.tax_exclusive_total
        assert item.tax_exclusive_unit_price == priced.tax_exclusive_unit

    assert [
        (t.tax_id and str(t.tax_id), t.name, t.rate, t.taxable_amount, t.amount)
        for t in taxes
    ] == [(t.tax_id, t.name, t.rate, t.taxable_amount, t.amount) for t in pricing.taxes]

    rows = [d for d in order.order_discounts if d.source == "promotion"]
    if pricing.promotion is None:
        assert rows == []
    else:
        assert {r.reference_id for r in rows} == {pricing.promotion.id}
        assert money(sum((r.amount for r in rows), Decimal("0"))) == (
            pricing.promotion.amount
        )
        if pricing.promotion.scope == "lines":
            assert {r.order_item_id for r in rows} == set(pricing.promotion.line_ids)


@pytest.mark.asyncio
async def test_recalculate_without_a_bundle_still_reads_the_database(monkeypatch):
    """`ctx=None` is the path every existing caller takes; it must still
    resolve its inputs itself (settings, entity, tax groups, products)."""
    order = _order_for(
        [
            cp.CheckLine(
                id=uuid.uuid4(),
                product_id=uuid.uuid4(),
                quantity=1,
                unit_price=Decimal("10.00"),
            )
        ],
        None,
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.get = AsyncMock(return_value=None)
    monkeypatch.setattr(pos_order_service, "get_order", AsyncMock(return_value=order))
    settings = AsyncMock(return_value=SimpleNamespace(cash_rounding_step=0))
    monkeypatch.setattr(pos_order_service, "_settings", settings)
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(pos_order_service.tax_identity_service, "resolve", resolve)
    sync = AsyncMock()
    monkeypatch.setattr(
        pos_order_service.auto_promotion_service, "sync_auto_discounts", sync
    )
    await pos_order_service.recalculate(db, order)
    settings.assert_awaited_once()
    resolve.assert_awaited_once()
    db.get.assert_awaited()
    assert sync.await_args.kwargs["ctx"] is None
    assert order.total == Decimal("10.00")


def test_tax_tuple_filters_inactive_taxes_and_sums_the_rest():
    rows = [
        cp.TaxRow(id="a", name="Old VAT", rate=Decimal("0.05"), is_active=False),
        cp.TaxRow(id="b", name="VAT", rate=Decimal("0.05")),
        cp.TaxRow(id="c", name="Fee", rate=Decimal("0.02"), type="exclusive"),
    ]
    assert cp.tax_tuple(rows) == cp.TaxTuple(Decimal("0.07"), "VAT", "b", True)
    assert cp.tax_tuple(rows[:1]) == cp.NO_TAX
    assert cp.effective_tax(cp.tax_tuple(rows), False).rate == Decimal("0")


def test_a_percentage_is_held_to_the_precision_the_row_stores():
    rule = cp.promo_rule_from_wire(
        {
            "id": str(uuid.uuid4()),
            "name": "odd",
            "reward": "percentage_off_order",
            "reward_value": "12.3450",
            "sources": ["cashier"],
            "mode": "auto",
            "rank": 0,
        },
        BRANCH,
    )
    assert promotion_rules.discount_value(rule) == (True, Decimal("0.1235"))
