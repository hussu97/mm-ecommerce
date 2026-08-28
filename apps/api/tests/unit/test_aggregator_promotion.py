"""Unit coverage for order promotion — the branch-ownership decision and the
pure mapping logic, without a DB.

The DB write paths run against Postgres in production; what is pinned here is the
logic that would go wrong silently: which branch owns an order, that a
GrubOps-owned Barsha/Sharjah order is never re-created, the status vocabulary per
channel, the money mapping, modifier→snapshot conversion, customer field fill,
and per-rung timestamp selection.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.order import OrderStatusEnum
from app.services.aggregators import promote
from app.services.aggregators.normalized import StandardModifier


def _agg(**over):
    base = dict(
        id=uuid.uuid4(),
        channel="keeta",
        external_order_id="EXT1",
        branch_id=uuid.uuid4(),
        gross_sales=Decimal("40.00"),
        vat_amount=None,
        delivery_fee=Decimal("0"),
        commission_amount=None,
        payment_fee=None,
        status="40",
        placed_at=None,
        delivered_at=None,
        business_date="2026-08-27",
        mm_order_id=None,
        promoted_at=None,
        customer_name=None,
        customer_phone=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeDB:
    async def flush(self):
        return None

    async def execute(self, _stmt):
        return None


@pytest.fixture(autouse=True)
def _noop_pos_attach(monkeypatch):
    """Promotion now files the order onto the register via
    `pos_order_service.attach_promoted_aggregator_order`; the ownership/build
    tests here don't exercise the register, so no-op it by default (a test that
    cares overrides this with its own recorder)."""

    async def _attach(db, order, *, placed_at=None, delivered_at=None):
        return order

    monkeypatch.setattr(
        promote.pos_order_service, "attach_promoted_aggregator_order", _attach
    )


# ── _refresh_order backfills a customer onto an existing order ────────────────


async def test_refresh_order_backfills_missing_customer(monkeypatch):
    """An order first filed without a customer (early promote / convergence) gets
    the scraped customer on the next refresh."""

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(promote.order_fees, "stamp", _noop)
    monkeypatch.setattr(promote, "_drive_status", _noop)

    agg = _agg(customer_name="Aisha", customer_phone="+971500000000")
    order = SimpleNamespace(customer_name=None, customer_phone=None)
    await promote._refresh_order(_FakeDB(), order, agg)
    assert order.customer_name == "Aisha"
    assert order.customer_phone == "+971500000000"


async def test_refresh_order_never_overwrites_an_existing_customer(monkeypatch):
    """A value already on the order (e.g. a GrubOps-sourced one) is preserved."""

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(promote.order_fees, "stamp", _noop)
    monkeypatch.setattr(promote, "_drive_status", _noop)

    agg = _agg(customer_name="Scraped Name")
    order = SimpleNamespace(customer_name="GrubOps Name", customer_phone="+971")
    await promote._refresh_order(_FakeDB(), order, agg)
    assert order.customer_name == "GrubOps Name"  # not overwritten


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


def test_display_code_shortens_the_long_keeta_id():
    # Keeta's scraped orderViewId is a 16-digit machine string; DSO/Al-Karama
    # orders are never on GrubTech, so promotion is the only place a short
    # driver code can be set. It must be the last four, not the whole id.
    assert promote._display_code("5047842447122109") == "2109"


def test_display_code_keeps_an_already_short_marketplace_number():
    # Noon/Deliveroo hand out a short numeric order number that IS the code.
    assert promote._display_code("5717") == "5717"
    assert promote._display_code("0037") == "0037"


def test_display_code_none_when_no_reference():
    assert promote._display_code(None) is None
    assert promote._display_code("") is None


# ── ownership decision ───────────────────────────────────────────────────────
async def test_off_platform_branch_is_created(monkeypatch):
    """DSO/Karama (no GrubOps) is owned by promotion — it builds the order."""
    built = SimpleNamespace(id=uuid.uuid4())

    async def fake_has_grubops(db, branch_id):
        return False

    async def fake_find_conv(db, ext):
        return None

    async def fake_build(db, agg, label, *, draw_stock=True):
        return built

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote, "_find_convergence_order", fake_find_conv)
    monkeypatch.setattr(promote, "_build_order", fake_build)

    agg = _agg()
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is built
    assert agg.mm_order_id == built.id
    assert agg.promoted_at is not None


async def test_promotion_owned_order_is_attached_to_pos(monkeypatch):
    """A promotion-owned (off-platform) order is filed onto the register, so it
    shows up like a GrubOps order — with the marketplace's placed/delivered
    timestamps."""
    built = SimpleNamespace(id=uuid.uuid4())
    attach_calls = []

    async def fake_has_grubops(db, branch_id):
        return False

    async def fake_find_conv(db, ext):
        return None

    async def fake_build(db, agg, label, *, draw_stock=True):
        return built

    async def rec_attach(db, order, *, placed_at=None, delivered_at=None):
        attach_calls.append((order, placed_at, delivered_at))
        return order

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote, "_find_convergence_order", fake_find_conv)
    monkeypatch.setattr(promote, "_build_order", fake_build)
    monkeypatch.setattr(
        promote.pos_order_service, "attach_promoted_aggregator_order", rec_attach
    )

    placed = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    delivered = datetime(2026, 8, 27, 12, 40, tzinfo=timezone.utc)
    agg = _agg(placed_at=placed, delivered_at=delivered)
    await promote.promote_order(_FakeDB(), agg)
    assert len(attach_calls) == 1
    order, p, d = attach_calls[0]
    assert order is built and p == placed and d == delivered


async def test_grubops_owned_order_is_not_re_attached_to_pos(monkeypatch):
    """A GrubOps-owned order (Barsha/Sharjah) is already on the register — the
    promotion overlay must NOT attach it a second time."""
    grubops_order = SimpleNamespace(id=uuid.uuid4())
    attach_calls = []

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext):
        return grubops_order

    async def fake_stamp(db, order, **kwargs):
        return None

    async def rec_attach(db, order, *, placed_at=None, delivered_at=None):
        attach_calls.append(order)
        return order

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote.order_fees, "stamp", fake_stamp)
    monkeypatch.setattr(
        promote.pos_order_service, "attach_promoted_aggregator_order", rec_attach
    )

    await promote.promote_order(_FakeDB(), _agg())
    assert attach_calls == []  # never re-attached — GrubOps owns the register row


def test_promoter_channel_label_matches_grubops_noon_food():
    """Promoted noon orders must carry "Noon Food" (the marketplace/GrubOps name),
    not "Noon", so they group with GrubOps noon orders everywhere."""
    from app.models.aggregator import CHANNEL_NOON

    assert promote.reconcile.CHANNEL_GRUBOPS_LABEL[CHANNEL_NOON] == "Noon Food"


async def test_grubops_owned_order_is_never_recreated(monkeypatch):
    """Barsha/Sharjah with a GrubOps order → link only, never build/recreate it.

    The one edit promotion is allowed on a GrubOps-owned order is overlaying the
    marketplace's ACTUAL settled fees onto its fee columns (a null-guarded fee
    stamp), so the assertion is: nothing is built, and only the fee overlay runs.
    """
    grubops_order = SimpleNamespace(id=uuid.uuid4())
    build_calls = {"n": 0}
    stamp_calls = {"n": 0, "kwargs": None}

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext):
        return grubops_order

    async def fake_build(db, agg, label, *, draw_stock=True):
        build_calls["n"] += 1
        return SimpleNamespace(id=uuid.uuid4())

    async def fake_stamp(db, order, **kwargs):
        stamp_calls["n"] += 1
        stamp_calls["kwargs"] = kwargs
        return None

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote, "_build_order", fake_build)
    monkeypatch.setattr(promote.order_fees, "stamp", fake_stamp)

    agg = _agg()
    agg.commission_amount = Decimal("9.00")  # the marketplace has settled it
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is grubops_order
    assert agg.mm_order_id == grubops_order.id
    assert build_calls["n"] == 0  # GrubOps owns it — nothing built
    # The actual settled fee is overlaid onto the GrubOps order.
    assert stamp_calls["n"] == 1
    assert stamp_calls["kwargs"]["actual_commission"] == Decimal("9.00")


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

    async def fake_build(db, agg, label, *, draw_stock=True):
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
    """Fake db for the direct name/SKU match: first execute() answers the name
    query, second the SKU fallback. Proposal writes (also execute()) are harmless
    extra calls that return an empty result."""

    def __init__(self, name_hit=None, sku_hit=None):
        self._hits = [name_hit, sku_hit]
        self.calls = 0

    async def execute(self, _stmt):
        row = self._hits[self.calls] if self.calls < len(self._hits) else None
        self.calls += 1
        return _MatchResult(row)


@pytest.fixture
def no_override(monkeypatch):
    """No approved map override, and swallow the proposal write — isolates the
    direct name/SKU match path."""

    async def _no_override(db, system, name):
        return None, ""

    async def _noop(db, system, name, **kw):
        return None

    monkeypatch.setattr(
        promote.external_item_map_service, "resolve_product", _no_override
    )
    monkeypatch.setattr(promote.external_item_map_service, "record_proposal", _noop)


async def test_match_product_by_name(no_override):
    pid = uuid.uuid4()
    db = _MatchDB(name_hit=(pid, "SKU1"))
    assert await promote._match_product(db, "keeta", "Basque Cheesecake") == (
        pid,
        "SKU1",
    )
    assert db.calls == 1  # matched on name, no SKU fallback needed


async def test_match_product_falls_back_to_sku(no_override):
    pid = uuid.uuid4()
    db = _MatchDB(name_hit=None, sku_hit=(pid, "SKU2"))
    assert await promote._match_product(db, "keeta", "SKU2") == (pid, "SKU2")
    assert db.calls == 2  # name missed, SKU matched


async def test_match_product_unmatched_is_null(no_override):
    db = _MatchDB(name_hit=None, sku_hit=None)
    assert await promote._match_product(db, "keeta", "Nonexistent Item") == (None, "")


async def test_match_product_blank_name_skips_db(no_override):
    db = _MatchDB(name_hit=(uuid.uuid4(), "X"))
    assert await promote._match_product(db, "keeta", "  ") == (None, "")
    assert db.calls == 0  # no query for an empty name


async def test_approved_override_wins_over_name_match(monkeypatch):
    """An approved map override short-circuits the direct match entirely."""
    override_pid = uuid.uuid4()

    async def _override(db, system, name):
        assert system == "keeta"
        return override_pid, "OVERRIDE-SKU"

    called = {"proposal": 0}

    async def _record(db, system, name, **kw):
        called["proposal"] += 1

    monkeypatch.setattr(promote.external_item_map_service, "resolve_product", _override)
    monkeypatch.setattr(promote.external_item_map_service, "record_proposal", _record)

    db = _MatchDB(name_hit=(uuid.uuid4(), "WRONG"))  # a different name-match, ignored
    assert await promote._match_product(
        db, "keeta", "Brookie Cookie Melt (500 grams)"
    ) == (
        override_pid,
        "OVERRIDE-SKU",
    )
    assert db.calls == 0  # never reached the direct name query
    assert called["proposal"] == 0  # an approved override records no proposal


# ── modifier → snapshot ──────────────────────────────────────────────────────


class _OptionDB:
    """Fake db for modifier snapshot tests. Always returns no approved map."""

    def __init__(self, *, opt_id=None):
        self._opt_id = opt_id
        self.proposals: list[str] = []
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1

        class _R:
            def first(inner_self):
                return None

        return _R()


async def test_modifier_snapshot_with_quantity(monkeypatch):
    """Modifiers round-trip through the snapshot with the correct quantity field."""

    async def _no_opt(db, system, name, *, ref=None):
        return None, None, None

    proposals: list[str] = []

    async def _record_opt(db, system, name, *, ref=None, guess_modifier_option_id=None):
        proposals.append(name)

    monkeypatch.setattr(promote.external_item_map_service, "resolve_option", _no_opt)
    monkeypatch.setattr(
        promote.external_item_map_service, "record_option_proposal", _record_opt
    )

    mods = [
        StandardModifier(
            name="Dark Chocolate", quantity=Decimal("2"), unit_price=Decimal("3.00")
        ),
        StandardModifier(
            name="Caramel Sauce", quantity=Decimal("1"), unit_price=Decimal("1.50")
        ),
    ]
    db = _OptionDB()
    snapshot, options_price = await promote._build_modifier_snapshot(
        db, "deliveroo", mods
    )

    assert len(snapshot) == 2
    assert snapshot[0]["option_name"] == "Dark Chocolate"
    assert snapshot[0]["quantity"] == 2
    assert snapshot[0]["option_price"] == 3.00
    assert snapshot[1]["option_name"] == "Caramel Sauce"
    assert snapshot[1]["quantity"] == 1

    # options_price = (3.00 * 2) + (1.50 * 1)
    assert options_price == Decimal("7.50")

    # Both names proposed for review since no approved map exists.
    assert set(proposals) == {"Dark Chocolate", "Caramel Sauce"}


async def test_modifier_snapshot_unknown_price_contributes_zero(monkeypatch):
    """A modifier with no price from the aggregator does not invent a price."""

    async def _no_opt(db, system, name, *, ref=None):
        return None, None, None

    async def _noop_record(db, system, name, **kw):
        pass

    monkeypatch.setattr(promote.external_item_map_service, "resolve_option", _no_opt)
    monkeypatch.setattr(
        promote.external_item_map_service, "record_option_proposal", _noop_record
    )

    mods = [
        StandardModifier(name="Extra Nuts", quantity=Decimal("1"), unit_price=None),
        StandardModifier(
            name="Brownie", quantity=Decimal("2"), unit_price=Decimal("5.00")
        ),
    ]
    db = _OptionDB()
    snapshot, options_price = await promote._build_modifier_snapshot(db, "keeta", mods)

    # Extra Nuts has no price → contributes 0; Brownie contributes 5.00 * 2 = 10.00
    assert options_price == Decimal("10.00")
    assert snapshot[0]["option_price"] == 0.0
    assert snapshot[1]["option_price"] == 5.0


async def test_modifier_snapshot_approved_map_sets_option_id(monkeypatch):
    """An approved option map row links modifier_option_id in the snapshot."""
    opt_uuid = uuid.uuid4()

    async def _approved(db, system, name, *, ref=None):
        if name == "Salted Caramel":
            return opt_uuid, "Salted Caramel", Decimal("2.00")
        return None, None, None

    async def _noop_record(db, system, name, **kw):
        pass

    monkeypatch.setattr(promote.external_item_map_service, "resolve_option", _approved)
    monkeypatch.setattr(
        promote.external_item_map_service, "record_option_proposal", _noop_record
    )

    mods = [
        StandardModifier(
            name="Salted Caramel", quantity=Decimal("1"), unit_price=Decimal("2.00")
        )
    ]
    db = _OptionDB()
    snapshot, _ = await promote._build_modifier_snapshot(db, "noon", mods)

    assert snapshot[0]["modifier_option_id"] == str(opt_uuid)
    assert snapshot[0]["option_id"] == str(opt_uuid)


async def test_modifier_snapshot_no_proposal_when_approved(monkeypatch):
    """An approved map hit must not emit a proposal — it is already mapped."""
    opt_uuid = uuid.uuid4()

    async def _approved(db, system, name, *, ref=None):
        return opt_uuid, "Matched", Decimal("1.00")

    proposals: list[str] = []

    async def _should_not_be_called(db, system, name, **kw):
        proposals.append(name)

    monkeypatch.setattr(promote.external_item_map_service, "resolve_option", _approved)
    monkeypatch.setattr(
        promote.external_item_map_service,
        "record_option_proposal",
        _should_not_be_called,
    )

    mods = [
        StandardModifier(
            name="Matched Option", quantity=Decimal("1"), unit_price=Decimal("1.00")
        )
    ]
    db = _OptionDB()
    await promote._build_modifier_snapshot(db, "careem", mods)
    assert proposals == []


# ── customer field fill ──────────────────────────────────────────────────────


def _agg_with_customer(**over):
    base = dict(
        id=uuid.uuid4(),
        channel="keeta",
        external_order_id="EXT2",
        branch_id=uuid.uuid4(),
        gross_sales=Decimal("30.00"),
        vat_amount=None,
        delivery_fee=Decimal("0"),
        status="40",
        placed_at=None,
        accepted_at=None,
        delivered_at=None,
        cancelled_at=None,
        business_date="2026-08-27",
        mm_order_id=None,
        promoted_at=None,
        customer_name=None,
        customer_phone=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_rung_at_uses_delivered_at_for_delivered_rung():
    delivered = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    placed = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    agg = _agg_with_customer(placed_at=placed, delivered_at=delivered)
    assert promote._rung_at(agg, OrderStatusEnum.DELIVERED) == delivered


def test_rung_at_falls_back_to_placed_at_for_delivered_when_absent():
    placed = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    agg = _agg_with_customer(placed_at=placed, delivered_at=None)
    assert promote._rung_at(agg, OrderStatusEnum.DELIVERED) == placed


def test_rung_at_uses_cancelled_at_for_cancelled_rung():
    cancelled = datetime(2026, 8, 27, 11, 0, tzinfo=timezone.utc)
    agg = _agg_with_customer(placed_at=None, cancelled_at=cancelled)
    assert promote._rung_at(agg, OrderStatusEnum.CANCELLED) == cancelled


def test_rung_at_uses_accepted_at_for_confirmed_rung():
    accepted = datetime(2026, 8, 27, 9, 5, tzinfo=timezone.utc)
    placed = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    agg = _agg_with_customer(placed_at=placed, accepted_at=accepted)
    assert promote._rung_at(agg, OrderStatusEnum.CONFIRMED) == accepted


def test_rung_at_falls_back_to_placed_at_for_confirmed_when_no_accepted_at():
    placed = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    agg = _agg_with_customer(placed_at=placed, accepted_at=None)
    assert promote._rung_at(agg, OrderStatusEnum.CONFIRMED) == placed


def test_rung_at_uses_accepted_at_for_packed_rung():
    accepted = datetime(2026, 8, 27, 9, 5, tzinfo=timezone.utc)
    agg = _agg_with_customer(accepted_at=accepted)
    assert promote._rung_at(agg, OrderStatusEnum.PACKED) == accepted
