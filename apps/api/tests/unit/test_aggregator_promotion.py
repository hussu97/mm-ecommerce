"""Unit coverage for order promotion — the branch-ownership decision and the
pure mapping logic, without a DB.

The DB write paths run against Postgres in production; what is pinned here is the
logic that would go wrong silently: which branch owns an order, that a
GrubOps-owned Barsha/Sharjah order is never re-created, the status vocabulary per
channel, and the money mapping.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

from app.models.order import OrderStatusEnum
from app.services.aggregators import promote


def _agg(**over):
    base = dict(
        id=uuid.uuid4(),
        channel="keeta",
        external_order_id="EXT1",
        branch_id=uuid.uuid4(),
        gross_sales=Decimal("40.00"),
        vat_amount=None,
        delivery_fee=Decimal("0"),
        status="40",
        placed_at=None,
        business_date="2026-08-27",
        mm_order_id=None,
        promoted_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeDB:
    async def flush(self):
        return None


# ── status vocabulary ────────────────────────────────────────────────────────
def test_keeta_status_codes_map():
    assert promote._target_status("keeta", "40") == OrderStatusEnum.DELIVERED
    assert promote._target_status("keeta", "50") == OrderStatusEnum.CANCELLED
    assert promote._target_status("keeta", "") is None
    assert promote._target_status("keeta", "99") is None  # unknown → indeterminate


def test_deliveroo_status_words_map():
    assert promote._target_status("deliveroo", "delivered") == OrderStatusEnum.DELIVERED
    assert promote._target_status("deliveroo", "Cancelled") == OrderStatusEnum.CANCELLED
    assert promote._target_status("deliveroo", "rejected") == OrderStatusEnum.CANCELLED
    assert promote._target_status("deliveroo", "en route") is None


def test_talabat_status_words_map():
    assert promote._target_status("talabat", "Delivered") == OrderStatusEnum.DELIVERED
    assert promote._target_status("talabat", "cancelled") == OrderStatusEnum.CANCELLED
    assert promote._target_status("talabat", "preparing") is None


def test_unknown_channel_has_no_mapping():
    assert promote._target_status("fake_channel", "delivered") is None


# ── money mapping ────────────────────────────────────────────────────────────
def test_money_fields_split_vat_out_of_total():
    fields = promote._money_fields(
        _agg(gross_sales=Decimal("42.00"), vat_amount=Decimal("2.00"))
    )
    assert fields["total"] == Decimal("42.00")
    assert fields["vat_amount"] == Decimal("2.00")
    assert fields["subtotal"] == Decimal("40.00")
    assert fields["total_excl_vat"] == Decimal("40.00")
    assert fields["vat_rate"] == Decimal("0.05")
    # MM books no delivery fee or discount for a promoted order.
    assert fields["delivery_fee"] == Decimal("0")
    assert fields["discount_amount"] == Decimal("0")


def test_money_fields_no_vat_is_zero_rate():
    fields = promote._money_fields(_agg(gross_sales=Decimal("40.00"), vat_amount=None))
    assert fields["vat_amount"] == Decimal("0")
    assert fields["vat_rate"] == Decimal("0")
    assert fields["total"] == fields["subtotal"] == Decimal("40.00")
    assert fields["aggregator_delivery_fee"] == Decimal("0")


# ── ownership decision ───────────────────────────────────────────────────────
async def test_off_platform_branch_is_created(monkeypatch):
    """DSO/Karama (no GrubOps) is owned by promotion — it builds the order."""
    built = SimpleNamespace(id=uuid.uuid4())

    async def fake_has_grubops(db, branch_id):
        return False

    async def fake_find_conv(db, ext):
        return None

    async def fake_build(db, agg, label):
        return built

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote, "_find_convergence_order", fake_find_conv)
    monkeypatch.setattr(promote, "_build_order", fake_build)

    agg = _agg()
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is built
    assert agg.mm_order_id == built.id
    assert agg.promoted_at is not None


async def test_grubops_owned_order_is_never_recreated(monkeypatch):
    """Barsha/Sharjah with a GrubOps order → link only, never build or edit."""
    grubops_order = SimpleNamespace(id=uuid.uuid4())
    build_calls = {"n": 0}

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext):
        return grubops_order

    async def fake_build(db, agg, label):
        build_calls["n"] += 1
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote, "_build_order", fake_build)

    agg = _agg()
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is grubops_order
    assert agg.mm_order_id == grubops_order.id
    assert build_calls["n"] == 0  # GrubOps owns it — nothing built


async def test_grubops_branch_gap_is_filled(monkeypatch):
    """Barsha/Sharjah with NO GrubOps order → promotion gap-fills."""
    built = SimpleNamespace(id=uuid.uuid4())
    build_calls = {"n": 0}

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext):
        return None  # GrubOps never produced it

    async def fake_find_conv(db, ext):
        return None

    async def fake_build(db, agg, label):
        build_calls["n"] += 1
        return built

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote, "_find_convergence_order", fake_find_conv)
    monkeypatch.setattr(promote, "_build_order", fake_build)

    agg = _agg()
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is built
    assert build_calls["n"] == 1  # gap-filled


async def test_no_branch_is_skipped(monkeypatch):
    agg = _agg(branch_id=None)
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is None
    assert agg.promoted_at is None


# ── product mapping ──────────────────────────────────────────────────────────
class _MatchResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _MatchDB:
    """Fake db: first execute() answers the name query, second the SKU fallback."""

    def __init__(self, name_hit=None, sku_hit=None):
        self._hits = [name_hit, sku_hit]
        self.calls = 0

    async def execute(self, _stmt):
        row = self._hits[self.calls] if self.calls < len(self._hits) else None
        self.calls += 1
        return _MatchResult(row)


async def test_match_product_by_name():
    pid = uuid.uuid4()
    db = _MatchDB(name_hit=(pid, "SKU1"))
    assert await promote._match_product(db, "Basque Cheesecake") == (pid, "SKU1")
    assert db.calls == 1  # matched on name, no SKU fallback needed


async def test_match_product_falls_back_to_sku():
    pid = uuid.uuid4()
    db = _MatchDB(name_hit=None, sku_hit=(pid, "SKU2"))
    assert await promote._match_product(db, "SKU2") == (pid, "SKU2")
    assert db.calls == 2  # name missed, SKU matched


async def test_match_product_unmatched_is_null():
    db = _MatchDB(name_hit=None, sku_hit=None)
    assert await promote._match_product(db, "Nonexistent Item") == (None, "")


async def test_match_product_blank_name_skips_db():
    db = _MatchDB(name_hit=(uuid.uuid4(), "X"))
    assert await promote._match_product(db, "  ") == (None, "")
    assert db.calls == 0  # no query for an empty name
