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
from datetime import datetime, timedelta, timezone
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
        display_ref=None,
        branch_id=uuid.uuid4(),
        gross_sales=Decimal("40.00"),
        vat_amount=None,
        delivery_fee=Decimal("0"),
        commission_amount=None,
        payment_fee=None,
        status="40",
        placed_at=None,
        accepted_at=None,
        delivered_at=None,
        business_date="2026-08-27",
        mm_order_id=None,
        promoted_at=None,
        customer_name=None,
        customer_phone=None,
        customer_address=None,
        driver_name=None,
        driver_phone=None,
        driver_status=None,
        cancellation_fee=None,
        marketing_fee=None,
        net_payable=None,
        refund_amount=None,
        raw=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _mm_order(**over):
    """A stand-in MM Order for the GrubOps-owned promotion path — just the fields
    the overlay reads/writes (id, the scraped-contact columns filled fill-only)."""
    base = dict(
        id=uuid.uuid4(),
        customer_name=None,
        customer_phone=None,
        shipping_address_snapshot=None,
        aggregator_driver_name=None,
        aggregator_driver_phone=None,
        aggregator_driver_status=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeDB:
    async def flush(self):
        return None

    async def execute(self, _stmt):
        return None

    async def refresh(self, _obj, _attrs=None):
        return None

    async def scalar(self, _stmt):
        # No priced lines to reconcile against in these header-money unit tests.
        return None


async def _no_drive(db, order, agg):
    """A `_drive_status` stand-in for the tests whose concern is elsewhere
    (attach, build, customer fill): the real one walks the lifecycle against a DB
    and needs an `order.status` the SimpleNamespace fixtures deliberately omit."""
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
    monkeypatch.setattr(promote, "_reconcile_total_to_lines", _noop)
    monkeypatch.setattr(promote, "_stamp_vat_row", _noop)

    agg = _agg(customer_name="Aisha", customer_phone="+971500000000")
    order = SimpleNamespace(
        customer_name=None,
        customer_phone=None,
        shipping_address_snapshot=None,
        aggregator_driver_name=None,
        aggregator_driver_phone=None,
        aggregator_driver_status=None,
    )
    await promote._refresh_order(_FakeDB(), order, agg)
    assert order.customer_name == "Aisha"
    assert order.customer_phone == "+971500000000"


async def test_refresh_order_never_overwrites_an_existing_customer(monkeypatch):
    """A value already on the order (e.g. a GrubOps-sourced one) is preserved."""

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(promote.order_fees, "stamp", _noop)
    monkeypatch.setattr(promote, "_drive_status", _noop)
    monkeypatch.setattr(promote, "_reconcile_total_to_lines", _noop)
    monkeypatch.setattr(promote, "_stamp_vat_row", _noop)

    agg = _agg(customer_name="Scraped Name")
    order = SimpleNamespace(
        customer_name="GrubOps Name",
        customer_phone="+971",
        shipping_address_snapshot={"text": "existing"},
        aggregator_driver_name="GrubOps Rider",
        aggregator_driver_phone="+9715",
        aggregator_driver_status="ASSIGNED",
    )
    await promote._refresh_order(_FakeDB(), order, agg)
    assert order.customer_name == "GrubOps Name"  # not overwritten


# ── status vocabulary ────────────────────────────────────────────────────────
def test_keeta_status_codes_map():
    assert promote._target_status("keeta", "40") == OrderStatusEnum.DELIVERED
    assert promote._target_status("keeta", "50") == OrderStatusEnum.CANCELLED
    assert promote._target_status("keeta", "") is None
    assert promote._target_status("keeta", "99") is None  # unknown → indeterminate


def test_provider_cancelled_but_paid_is_decided_by_net_payable_sign():
    # Cancelled at the marketplace but the ledger still pays the net → we keep it.
    assert promote._provider_cancelled_but_paid(
        _agg(status="cancelled", net_payable=Decimal("37.42"))
    )
    assert promote._provider_cancelled_but_paid(
        _agg(status="50", net_payable=Decimal("26.40"))  # legacy raw code
    )
    # Cancelled and paid nothing → a real (merchant) cancellation, a lost sale.
    assert not promote._provider_cancelled_but_paid(
        _agg(status="cancelled", net_payable=Decimal("0"))
    )
    assert not promote._provider_cancelled_but_paid(
        _agg(status="cancelled", net_payable=None)
    )
    # Not a cancellation at all → never in scope.
    assert not promote._provider_cancelled_but_paid(
        _agg(status="completed", net_payable=Decimal("37.42"))
    )
    # A MERCHANT-initiated cancellation is OUR fault — a lost sale, not revenue —
    # even when the provisional net_payable is still positive. It stays cancelled.
    assert not promote._provider_cancelled_but_paid(
        _agg(
            status="cancelled",
            net_payable=Decimal("26.20"),
            raw={"orderCancelSceneDesc": "Merchant"},
        )
    )
    # Marketplace ("Customer service") and customer ("User") cancellations are not
    # ours — those we were paid for we keep.
    assert promote._provider_cancelled_but_paid(
        _agg(
            status="cancelled",
            net_payable=Decimal("26.20"),
            raw={"orderCancelSceneDesc": "User"},
        )
    )


def test_cancel_reason_reads_keeta_scene_desc():
    assert (
        promote._cancel_reason(_agg(raw={"orderCancelSceneDesc": "Customer service"}))
        == "Customer service"
    )
    assert promote._cancel_reason(_agg(raw={})) is None
    assert promote._cancel_reason(_agg(raw=None)) is None


async def _record_rungs(monkeypatch):
    """Replace the lifecycle transition with one that advances the order in place
    and records each rung, so `_drive_status`'s status walk is observable."""
    rungs: list[OrderStatusEnum] = []

    async def _transition(db, order, rung, **kwargs):
        rungs.append(rung)
        order.status = rung
        return True

    monkeypatch.setattr(promote.order_lifecycle, "transition", _transition)
    return rungs


async def test_drive_status_keeps_a_paid_marketplace_cancellation_delivered(
    monkeypatch,
):
    rungs = await _record_rungs(monkeypatch)
    order = _mm_order(status=OrderStatusEnum.CREATED, aggregator_cancel_reason=None)
    agg = _agg(
        status="cancelled",
        net_payable=Decimal("37.42"),
        raw={"orderCancelSceneDesc": "Customer service"},
    )

    await promote._drive_status(_FakeDB(), order, agg)

    # Booked all the way to delivered (its revenue counts), not cancelled.
    assert order.status == OrderStatusEnum.DELIVERED
    assert OrderStatusEnum.CANCELLED not in rungs
    # And the marketplace cancellation is recorded for display.
    assert order.aggregator_cancel_reason == "Customer service"


async def test_drive_status_still_cancels_an_unpaid_marketplace_cancellation(
    monkeypatch,
):
    rungs = await _record_rungs(monkeypatch)
    order = _mm_order(status=OrderStatusEnum.CONFIRMED, aggregator_cancel_reason=None)
    agg = _agg(
        status="cancelled",
        net_payable=Decimal("0"),
        raw={"orderCancelSceneDesc": "Item unavailable"},
        cancelled_at=None,
    )

    await promote._drive_status(_FakeDB(), order, agg)

    assert rungs == [OrderStatusEnum.CANCELLED]
    assert order.aggregator_cancel_reason == "Item unavailable"


def test_deliveroo_status_words_map():
    assert promote._target_status("deliveroo", "delivered") == OrderStatusEnum.DELIVERED
    assert promote._target_status("deliveroo", "Cancelled") == OrderStatusEnum.CANCELLED
    assert promote._target_status("deliveroo", "rejected") == OrderStatusEnum.CANCELLED
    assert promote._target_status("deliveroo", "en route") is None


def test_deliveroo_marketplace_line_ids_include_drn_id_not_csv_order_number():
    """Promotion must backfill statement lines on CSV Order ID (= detail drn_id)."""
    sales_uuid = "bd627d5f-d304-3a4f-92e4-34f92fbd4304"
    drn_uuid = "b9fa898d-83f7-44a6-a10a-71fe9f2cdbc5"
    agg = _agg(
        channel="deliveroo",
        external_order_id=sales_uuid,
        display_ref="9170",
        raw={"order_id": sales_uuid, "detail": {"drn_id": drn_uuid}},
    )
    ids = promote._marketplace_line_ids(agg)
    assert sales_uuid in ids
    assert "9170" in ids
    assert drn_uuid in ids
    assert "51135384652" not in ids


def test_talabat_status_words_map():
    assert promote._target_status("talabat", "Delivered") == OrderStatusEnum.DELIVERED
    assert promote._target_status("talabat", "cancelled") == OrderStatusEnum.CANCELLED
    assert promote._target_status("talabat", "preparing") is None


def test_unknown_channel_has_no_mapping():
    assert promote._target_status("fake_channel", "delivered") is None


# ── money mapping ────────────────────────────────────────────────────────────
def test_money_fields_derives_inclusive_vat_from_total():
    # Output VAT is derived from the gross the shop charged (inclusive 5%), NOT
    # trusted from the provider figure — a clean 42.00 splits to 40.00 + 2.00, and
    # the wrong provider vat_amount is ignored.
    fields = promote._money_fields(
        _agg(gross_sales=Decimal("42.00"), vat_amount=Decimal("999.00"))
    )
    assert fields["total"] == Decimal("42.00")
    assert fields["vat_amount"] == Decimal("2.00")
    # `subtotal` is VAT-INCLUSIVE (== total), like every other order writer — the
    # F-AGG-6 fix. It was the ex-VAT 40.00 before, which made `sum(subtotal)` mix bases.
    assert fields["subtotal"] == Decimal("42.00")
    assert fields["total_excl_vat"] == Decimal("40.00")
    assert fields["vat_rate"] == Decimal("0.05")
    # MM books no delivery fee or discount for a promoted order.
    assert fields["delivery_fee"] == Decimal("0")
    assert fields["discount_amount"] == Decimal("0")


def test_money_fields_books_item_reversal_onto_refunded_amount():
    # A delivered order with a 70 missing-item reversal on a 140 gross: the gross
    # stays on total/subtotal (what the customer was charged) and the 70 is booked
    # on refunded_amount — the field net revenue subtracts — with refunded_at set
    # to the delivery moment.
    delivered = datetime(2026, 9, 9, 15, 48)
    fields = promote._money_fields(
        _agg(
            gross_sales=Decimal("140.00"),
            refund_amount=Decimal("70.00"),
            delivered_at=delivered,
        )
    )
    assert fields["total"] == Decimal("140.00")
    assert fields["subtotal"] == Decimal("140.00")
    assert fields["refunded_amount"] == Decimal("70.00")
    assert fields["refunded_at"] == delivered


def test_money_fields_no_reversal_leaves_refund_zero_and_unstamped():
    fields = promote._money_fields(_agg(gross_sales=Decimal("40.00")))
    assert fields["refunded_amount"] == Decimal("0")
    assert fields["refunded_at"] is None


def test_money_fields_caps_reversal_at_total():
    fields = promote._money_fields(
        _agg(gross_sales=Decimal("40.00"), refund_amount=Decimal("60.00"))
    )
    # Net can never go below zero — a reversal is capped at the sale.
    assert fields["refunded_amount"] == Decimal("40.00")


def test_money_fields_derives_vat_even_when_provider_reports_none():
    # A provider that itemised no tax must not book the sale VAT-free: 40.00
    # inclusive is 38.10 + 1.90 (the whole reason the zero-VAT backfill exists).
    fields = promote._money_fields(_agg(gross_sales=Decimal("40.00"), vat_amount=None))
    assert fields["total"] == Decimal("40.00")
    assert fields["total_excl_vat"] == Decimal("38.10")
    assert fields["vat_amount"] == Decimal("1.90")
    assert fields["vat_rate"] == Decimal("0.05")
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
    grubops_order = _mm_order()
    attach_calls = []

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext, display_ref=None, **kwargs):
        return grubops_order

    async def fake_stamp(db, order, **kwargs):
        return None

    async def rec_attach(db, order, *, placed_at=None, delivered_at=None):
        attach_calls.append(order)
        return order

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote.order_fees, "stamp", fake_stamp)
    monkeypatch.setattr(promote, "_drive_status", _no_drive)
    monkeypatch.setattr(
        promote.pos_order_service, "attach_promoted_aggregator_order", rec_attach
    )

    await promote.promote_order(_FakeDB(), _agg())
    assert attach_calls == []  # never re-attached — GrubOps owns the register row


async def test_grubops_owned_order_carries_scraped_delivered_status(monkeypatch):
    """GrubOps' live push climbs a Barsha/Sharjah order only as far as
    out_for_delivery; the delivered (and cancelled) rung is the scrape's to carry.
    The matched path must drive the linked order's status from the scrape, or an
    order the marketplace has delivered sits at out_for_delivery forever."""
    grubops_order = _mm_order()
    driven = []

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext, display_ref=None, **kwargs):
        return grubops_order

    async def fake_stamp(db, order, **kwargs):
        return None

    async def rec_drive(db, order, agg):
        driven.append(order)

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote.order_fees, "stamp", fake_stamp)
    monkeypatch.setattr(promote, "_drive_status", rec_drive)

    await promote.promote_order(_FakeDB(), _agg(status="completed"))
    assert driven == [grubops_order]  # the scrape's status reached the MM order


def test_keeta_status_accepts_both_numeric_and_words():
    """Keeta emits the word 'completed' now (it used to be numeric '40'); both must
    map to delivered, else a promotion-owned Keeta order stalls at confirmed and
    never reaches the register/reports."""
    assert promote._target_status("keeta", "completed") == OrderStatusEnum.DELIVERED
    assert promote._target_status("keeta", "40") == OrderStatusEnum.DELIVERED
    assert promote._target_status("keeta", "cancelled") == OrderStatusEnum.CANCELLED
    assert promote._target_status("keeta", "50") == OrderStatusEnum.CANCELLED
    assert promote._target_status("keeta", "who-knows") is None


def test_promoter_channel_label_matches_grubops_noon_food():
    """Promoted noon orders must carry "Noon Food" (the marketplace/GrubOps name),
    not "Noon", so they group with GrubOps noon orders everywhere."""
    from app.models.aggregator import CHANNEL_NOON

    assert promote.reconcile.CHANNEL_GRUBOPS_LABEL[CHANNEL_NOON] == "Noon Food"


async def test_find_mm_order_matches_both_noon_names():
    """GrubTech writes grubops_order_map.source_channel="Noon", but MM's display
    label is "Noon Food". _find_mm_order must match BOTH, else every Barsha/Sharjah
    Noon order fails to link, defers the full adopt-grace, and files a duplicate
    standalone — the noon-duplicate bug."""
    from app.models.aggregator import CHANNEL_NOON, CHANNEL_TALABAT

    captured = {}

    class _DB:
        async def scalar(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return None  # no map row → _find_mm_order returns None after this call

    out = await promote.reconcile._find_mm_order(_DB(), CHANNEL_NOON, "FG8UNN1", "9721")
    assert out is None
    assert "'Noon Food'" in captured["sql"]  # the display label
    assert "'Noon'" in captured["sql"]  # GrubTech's own source.channel name

    # A channel whose GrubTech name equals its label queries just the one label.
    await promote.reconcile._find_mm_order(_DB(), CHANNEL_TALABAT, "3857897051", None)
    assert "'Talabat'" in captured["sql"]


async def test_find_mm_order_matches_careem_now_alias():
    """GrubTech writes source_channel='Careem Now'; MM's display label is 'Careem'.
    Without the alias every Barsha Careem order fails to find its GrubOps maker."""
    from app.models.aggregator import CHANNEL_CAREEM

    captured = {}

    class _DB:
        async def scalar(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return None

    await promote.reconcile._find_mm_order(_DB(), CHANNEL_CAREEM, "168934434", None)
    sql = captured["sql"]
    assert "'Careem'" in sql
    assert "'Careem Now'" in sql


def test_leading_zero_stripped_normalises_only_short_numeric_codes():
    """Deliveroo's handoff code is zero-padded on GrubTech ("0127") but unpadded in
    the scrape ("127"); both must normalise to the same value, while long ids (a
    UUID, Keeta's 16-digit orderViewId) are left alone so nothing collapses."""
    strip = promote.reconcile.leading_zero_stripped
    assert strip("0127") == "127"
    assert strip("127") == "127"
    assert strip("0037") == strip("37") == "37"
    # Long / non-numeric ids are matched verbatim (return None → no normalisation).
    assert strip("f6647c5b-a9c3-350b-956a-a394374f228d") is None
    assert strip("5077841337692318") is None  # Keeta orderViewId
    assert strip(None) is None


async def test_find_mm_order_matches_deliveroo_zero_padded_code():
    """GrubTech stores Deliveroo's externalId zero-padded ("0127"); the scrape's
    display_ref drops it ("127"). _find_mm_order must match the padded id too, else
    the Barsha Deliveroo order fails to link and a duplicate standalone is filed."""
    from app.models.aggregator import CHANNEL_DELIVEROO

    captured = {}

    class _DB:
        async def scalar(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return None

    out = await promote.reconcile._find_mm_order(
        _DB(), CHANNEL_DELIVEROO, "f6647c5b-a9c3-350b-956a-a394374f228d", "127"
    )
    assert out is None
    sql = captured["sql"]
    assert "ltrim" in sql.lower()  # the zero-stripped comparison is present
    assert "'127'" in sql  # matched against the stripped short code


async def test_find_mm_order_scopes_to_branch_and_day_for_recurring_code():
    """noon recycles the short orderRef across days, so two grubops_order_map rows
    can share "6227". Given branch + business_date the lookup must scope to that
    branch and Dubai placed-day, else it links an arbitrary one and strands the
    other (AGG-20260906-085 sat out_for_delivery while its delivery landed on the
    Sept-4 order that reused the code)."""
    from app.models.aggregator import CHANNEL_NOON

    captured = {}

    class _DB:
        async def scalar(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return None

    await promote.reconcile._find_mm_order(
        _DB(),
        CHANNEL_NOON,
        "FG96NNC9ZVQT4FA",
        "6227",
        branch_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        business_date="2026-09-06",
    )
    sql = captured["sql"]
    assert "'2026-09-06'" in sql  # the Dubai placed-day scope
    assert "Asia/Dubai" in sql
    assert "branch_id" in sql.lower()
    assert "'6227'" in sql


async def test_grubops_owned_order_is_never_recreated(monkeypatch):
    """Barsha/Sharjah with a GrubOps order → link only, never build/recreate it.

    The one edit promotion is allowed on a GrubOps-owned order is overlaying the
    marketplace's ACTUAL settled fees onto its fee columns (a null-guarded fee
    stamp), so the assertion is: nothing is built, and only the fee overlay runs.
    """
    grubops_order = _mm_order()
    build_calls = {"n": 0}
    stamp_calls = {"n": 0, "kwargs": None}

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext, display_ref=None, **kwargs):
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
    monkeypatch.setattr(promote, "_drive_status", _no_drive)

    agg = _agg()
    agg.commission_amount = Decimal("9.00")  # the marketplace has settled it
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is grubops_order
    assert agg.mm_order_id == grubops_order.id
    assert build_calls["n"] == 0  # GrubOps owns it — nothing built
    # The actual settled fee is overlaid onto the GrubOps order.
    assert stamp_calls["n"] == 1
    assert stamp_calls["kwargs"]["actual_commission"] == Decimal("9.00")


async def test_grubops_owned_order_backfills_scraped_customer_fill_only(monkeypatch):
    """The GrubOps push often lands with an empty customer; the scrape has it. On
    promotion the GrubOps-owned path fills the scraped customer/rider onto the order
    — but FILL-ONLY, so a value GrubTech already provided is never overwritten. This
    is the fix for "GrubOps orders show no scraped customer info"."""
    grubops_order = _mm_order(
        customer_name="",  # GrubTech gave us nothing → should be filled
        aggregator_driver_name="GrubTech Rider",  # GrubTech DID give this → keep it
    )

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext, display_ref=None, **kwargs):
        return grubops_order

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote.order_fees, "stamp", _noop)
    monkeypatch.setattr(promote, "_drive_status", _no_drive)
    monkeypatch.setattr(promote, "_record_fulfilment", _noop)

    agg = _agg(
        customer_name="Ambika",
        customer_phone="+97144451555",
        customer_address={"text": "DSO Tower 3"},
        driver_name="Scraped Rider",
    )
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is grubops_order
    # Empty fields filled from the scrape.
    assert grubops_order.customer_name == "Ambika"
    assert grubops_order.customer_phone == "+97144451555"
    assert grubops_order.shipping_address_snapshot == {"text": "DSO Tower 3"}
    # A field GrubTech already provided is left untouched (fill-only).
    assert grubops_order.aggregator_driver_name == "GrubTech Rider"


async def test_grubops_branch_defers_within_grace(monkeypatch):
    """Barsha/Sharjah, no GrubOps order yet, order still fresh → DEFER, don't build.

    This is the duplicate-prevention: promotion racing ahead of the GrubOps ingest
    (or ahead of the short `display_ref` it converges on) must not file a standalone
    that GrubOps then files again. It skips and retries next tick.
    """
    build_calls = {"n": 0}

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext, display_ref=None, **kwargs):
        return None  # GrubOps has not produced it *yet*

    async def fake_build(db, agg, label, *, draw_stock=True):
        build_calls["n"] += 1
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote, "_build_order", fake_build)

    agg = _agg(placed_at=datetime.now(timezone.utc) - timedelta(minutes=30))
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is None  # deferred
    assert build_calls["n"] == 0  # nothing filed — no duplicate
    assert agg.promoted_at is None  # cursor stays open so it retries


async def test_grubops_branch_defers_when_a_map_row_exists_even_past_grace(monkeypatch):
    """F-AGG-10: a GrubOps order whose `_create_order` FAILED has a map row with a
    null `mm_order_id`, so `_find_mm_order` misses it. Existence of the map row means
    GrubOps owns the order — promotion must DEFER (surface the push error) rather
    than file a standalone that duplicates it once the create succeeds, even past the
    adopt grace."""
    build_calls = {"n": 0}

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext, display_ref=None, **kwargs):
        return None  # no MM order yet (the create failed)

    async def fake_find_map(db, channel, ext, display_ref=None):
        return SimpleNamespace(
            grubops_order_id="G9", last_push_error="no branch map for location"
        )

    async def fake_build(db, agg, label, *, draw_stock=True):
        build_calls["n"] += 1
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(promote.reconcile, "_branch_has_grubops", fake_has_grubops)
    monkeypatch.setattr(promote.reconcile, "_find_mm_order", fake_find_mm)
    monkeypatch.setattr(promote.reconcile, "_find_grubops_map", fake_find_map)
    monkeypatch.setattr(promote, "_build_order", fake_build)

    # Placed two days ago — well past the adopt grace, so the ONLY thing stopping a
    # standalone is the map-row-existence check.
    agg = _agg(placed_at=datetime.now(timezone.utc) - timedelta(days=2))
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is None  # deferred to GrubOps
    assert build_calls["n"] == 0  # no duplicate standalone filed
    assert agg.promoted_at is None  # cursor stays open


async def test_grubops_branch_gap_is_filled_past_grace(monkeypatch):
    """Barsha/Sharjah, no GrubOps order after the grace window → recover by
    filing a standalone (GrubOps genuinely never ingested it)."""
    built = SimpleNamespace(id=uuid.uuid4())
    build_calls = {"n": 0}

    async def fake_has_grubops(db, branch_id):
        return True

    async def fake_find_mm(db, channel, ext, display_ref=None, **kwargs):
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

    # Placed two days ago — well past the adopt grace.
    agg = _agg(placed_at=datetime.now(timezone.utc) - timedelta(days=2))
    out = await promote.promote_order(_FakeDB(), agg)
    assert out is built
    assert build_calls["n"] == 1  # gap-filled as recovery


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
        display_ref=None,
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
        customer_address=None,
        driver_name=None,
        driver_phone=None,
        driver_status=None,
        cancellation_fee=None,
        marketing_fee=None,
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


# ── convergence keys on the short customer code too (GrubOps↔promote dedup) ────
class _RecordingDB:
    """Captures the compiled SQL of the one `scalar` the finders issue."""

    def __init__(self):
        self.sql = ""

    async def scalar(self, stmt):
        self.sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return None


async def test_convergence_matches_long_id_and_scoped_short_code():
    db = _RecordingDB()
    agg = _agg(
        external_order_id="FG4LNN5NPGYI0JA",
        display_ref="2253",
        branch_id=uuid.uuid4(),
        business_date="2026-08-28",
    )
    await promote._find_convergence_order(db, agg)
    # Long id always; short code only when scoped to branch + Dubai business day.
    assert "FG4LNN5NPGYI0JA" in db.sql
    assert "2253" in db.sql
    assert "2026-08-28" in db.sql
    assert "Asia/Dubai" in db.sql


async def test_convergence_without_display_ref_keys_on_long_id_only():
    db = _RecordingDB()
    agg = _agg(external_order_id="EXTONLY", display_ref=None)
    await promote._find_convergence_order(db, agg)
    assert "EXTONLY" in db.sql
    # No short-code branch, so no Dubai-day scoping clause is emitted.
    assert "Asia/Dubai" not in db.sql


async def test_convergence_short_code_is_channel_scoped():
    """The short pickup code is a per-branch-per-day sequence that DIFFERENT
    channels reuse, so the short-code branch must scope to the agg's own channel
    (its GrubTech spellings). Without this a masked Keeta backfill converged onto —
    and overwrote — a Deliveroo GrubOps order sharing the code on the same day."""
    db = _RecordingDB()
    agg = _agg(
        channel="keeta",
        external_order_id="1234567890123456",
        display_ref="127",
        branch_id=uuid.uuid4(),
        business_date="2026-08-28",
    )
    await promote._find_convergence_order(db, agg)
    # Keeta's own spelling scopes the short-code match; Deliveroo's does not appear.
    assert "Keeta 2.0" in db.sql
    assert "Deliveroo" not in db.sql
    assert "127" in db.sql


def test_fill_scraped_contact_skips_masked_values():
    """A masked (`***`) customer/rider must never fill a blank order — it just
    replaces "no data" with "redacted" and, being truthy, blocks the real value
    from filling in later. Mirrors the never-downgrade upsert."""
    agg = _agg(
        customer_name="***",
        customer_phone="***",
        customer_address={"address": "***"},
        driver_name="***",
        driver_phone="***",
    )
    order = _mm_order()
    promote._fill_scraped_contact(order, agg)
    assert order.customer_name is None
    assert order.customer_phone is None
    assert order.shipping_address_snapshot is None
    assert order.aggregator_driver_name is None
    assert order.aggregator_driver_phone is None


def test_fill_scraped_contact_fills_real_values():
    """A real (unmasked) value still backfills a blank order."""
    agg = _agg(
        customer_name="Aisha",
        customer_phone="+971500000000",
        customer_address={"address": "Villa 4"},
    )
    order = _mm_order()
    promote._fill_scraped_contact(order, agg)
    assert order.customer_name == "Aisha"
    assert order.customer_phone == "+971500000000"
    assert order.shipping_address_snapshot == {"address": "Villa 4"}


async def test_find_mm_order_matches_either_id_under_the_channel_label():
    from app.services.aggregators import reconcile

    db = _RecordingDB()
    await reconcile._find_mm_order(db, "noon", "FG4LNN5NPGYI0JA", "2253")
    assert "Noon Food" in db.sql
    assert "FG4LNN5NPGYI0JA" in db.sql
    assert "2253" in db.sql


def test_grubops_channel_names_including_resolves_the_whole_channel():
    """Any one spelling resolves to the full set for that channel; an unknown name
    falls back to itself so a scope built from it never widens to all channels."""
    from app.services.aggregators import reconcile

    assert set(reconcile.grubops_channel_names_including("Noon")) == {
        "Noon",
        "Noon Food",
    }
    assert set(reconcile.grubops_channel_names_including("Noon Food")) == {
        "Noon",
        "Noon Food",
    }
    assert "Careem Now" in reconcile.grubops_channel_names_including("Careem")
    assert reconcile.grubops_channel_names_including("Deliveroo") == ["Deliveroo"]
    assert reconcile.grubops_channel_names_including("Mystery") == ["Mystery"]
    assert reconcile.grubops_channel_names_including(None) == []


def test_branch_has_grubops_does_not_require_stock_push_to_be_on():
    """`is_active` on grubops_location_map gates stock-push, not maker-side recon.

    Barsha's row has been inactive since insert while every Careem GrubOps order
    still lands on that location. Filtering is_active left those orders
    `no_maker_side` after the Careem Now alias and the Foodics map seed.
    """
    import inspect

    from app.services.aggregators import reconcile

    src = inspect.getsource(reconcile._branch_has_grubops)
    assert "GrubOpsLocationMap.branch_id" in src
    assert "is_active.is_" not in src, (
        "recon must treat a GrubOps location as maker-side even when stock-push is off"
    )


def test_promote_lookback_covers_last_7d_and_includes_keeta():
    """Keeta is push-only; promote still walks AGGREGATOR_CHANNELS over 30 days
    so last-7d orders are picked up once they flow — no prod backfill required."""
    from app.core.config import settings
    from app.models.aggregator import AGGREGATOR_CHANNELS, CHANNEL_KEETA

    assert settings.AGGREGATOR_PROMOTE_LOOKBACK_DAYS >= 7
    assert CHANNEL_KEETA in AGGREGATOR_CHANNELS


# ── no invented doorstep: the 2026-09-06 status audit ─────────────────────────


def test_a_rider_holding_the_box_is_not_a_customer_holding_the_box():
    """`picked up` used to map to DELIVERED. It is the same invention as the
    auto-close — reading the last thing we hear as though it were the last thing
    that happens."""
    assert (
        promote._target_status("talabat", "picked up")
        == OrderStatusEnum.OUT_FOR_DELIVERY
    )
    assert (
        promote._target_status("talabat", "out for delivery")
        == OrderStatusEnum.OUT_FOR_DELIVERY
    )
    # A channel's real terminal word is still real evidence, and still lands.
    assert promote._target_status("talabat", "Delivered") == OrderStatusEnum.DELIVERED
    assert promote._target_status("keeta", "completed") == OrderStatusEnum.DELIVERED
    assert promote._target_status("talabat", "Cancelled") == OrderStatusEnum.CANCELLED


def test_the_ladder_passes_through_out_for_delivery():
    i = promote._LADDER.index
    assert i(OrderStatusEnum.PACKED) < i(OrderStatusEnum.OUT_FOR_DELIVERY)
    assert i(OrderStatusEnum.OUT_FOR_DELIVERY) < i(OrderStatusEnum.DELIVERED)


@pytest.mark.asyncio
async def test_a_marketplace_cancel_outranks_our_bookkeeping(monkeypatch):
    """The map refuses `packed → cancelled` — our rider failing does not cancel a
    paid, boxed order. A marketplace cancelling is the order ENDING: it owns the
    customer and has already refunded them. Talabat 3872488968 sat `delivered` in
    MM for a day because nothing let that fact through."""
    calls: list[tuple] = []

    async def fake_transition(db, o, new_status, *, extra_from=(), on_invalid="raise"):
        calls.append((new_status, tuple(extra_from)))
        return True

    monkeypatch.setattr(promote.order_lifecycle, "transition", fake_transition)
    agg = SimpleNamespace(
        channel="talabat",
        status="Cancelled",
        external_order_id="3872488968",
        cancelled_at=None,
        placed_at=datetime(2026, 9, 5, 18, 54, tzinfo=timezone.utc),
        net_payable=None,
        raw=None,
    )
    order = SimpleNamespace(
        status=OrderStatusEnum.PACKED, aggregator_cancel_reason=None
    )
    await promote._drive_status(None, order, agg)

    assert calls[0][0] == OrderStatusEnum.CANCELLED
    assert OrderStatusEnum.PACKED in calls[0][1]
    assert OrderStatusEnum.OUT_FOR_DELIVERY in calls[0][1]


def test_delivered_is_never_rewound_into_a_cancellation():
    """Once the channel has told us the customer received it, a later cancellation
    is a refund or dispute question, not a status to quietly undo."""
    assert OrderStatusEnum.DELIVERED not in promote._CANCEL_EXTRA_FROM


# ── every channel really does tell us the order finished ──────────────────────


def test_every_status_word_prod_has_ever_sent_maps_to_a_terminal_state():
    """The whole point of dropping the auto-close is that DELIVERED now has to come
    FROM the channel. So it matters that every channel actually sends one.

    This is the complete vocabulary prod has recorded across 2,422 aggregator
    orders (2026-07-01 → 2026-09-05), taken from
    `SELECT channel, status, count(*) FROM aggregator_order GROUP BY 1,2`. Every
    value maps to a terminal state — there is no channel that leaves an order
    hanging, and no unmapped word waiting to strand one at `confirmed`.
    """
    observed = {
        "careem": {"delivered": 37},
        "deliveroo": {"delivered": 41, "cancelled": 1},
        "keeta": {"completed": 1748, "50": 25},
        "noon": {"delivered": 186, "canceled": 2},
        "talabat": {"Delivered": 374, "Cancelled": 6},
    }
    terminal = {OrderStatusEnum.DELIVERED, OrderStatusEnum.CANCELLED}
    for channel, words in observed.items():
        for word in words:
            got = promote._target_status(channel, word)
            assert got in terminal, f"{channel} {word!r} → {got!r}"

    # And each channel can actually reach DELIVERED, not only CANCELLED.
    for channel, words in observed.items():
        assert any(
            promote._target_status(channel, w) == OrderStatusEnum.DELIVERED
            for w in words
        ), f"{channel} has no word that means delivered"


def test_an_unknown_word_strands_the_order_rather_than_guessing():
    """The 2 keeta rows in prod with a blank status (of 1,775) take this path: no
    guess, left at `confirmed`, and logged — which is the honest outcome now that
    nothing downstream invents the rest."""
    assert promote._target_status("keeta", "") is None
    assert promote._target_status("talabat", "some new word") is None


# ── each channel's vocabulary is its own ──────────────────────────────────────


def test_the_numeric_codes_belong_to_keeta_alone():
    """Meituan's codes are Keeta's, and a bare "40" arriving on another channel is
    not a delivery. The maps used to be one shared English bag for four channels,
    which made that distinction impossible to state."""
    assert promote._target_status("keeta", "40") == OrderStatusEnum.DELIVERED
    assert promote._target_status("keeta", "50") == OrderStatusEnum.CANCELLED
    for channel in ("careem", "deliveroo", "noon", "talabat"):
        assert promote._target_status(channel, "40") is None
        assert promote._target_status(channel, "50") is None


def test_stored_numeric_keeta_rows_still_map_after_the_decoder_learned_the_word():
    """`keeta_provider._decode_status` turns `50` into "cancelled" from now on, but
    25 rows are already stored with the raw "50" — re-promoting one must still
    land, so the numeric entries stay in the map alongside the word."""
    assert promote._target_status("keeta", "50") == OrderStatusEnum.CANCELLED
    assert promote._target_status("keeta", "cancelled") == OrderStatusEnum.CANCELLED
    assert promote._target_status("keeta", "completed") == OrderStatusEnum.DELIVERED


def test_every_channel_has_its_own_map_object():
    """Five names, so each channel's real vocabulary is visible and testable rather
    than hidden behind a shared default."""
    maps = promote._STATUS_MAPS
    assert set(maps) == {"careem", "deliveroo", "keeta", "noon", "talabat"}
    assert len({id(m) for m in maps.values()}) == len(maps)


class _OrderedScalarDB:
    """A fake DB that answers `_reconcile_total_to_lines`'s two scalar queries in
    order: first the count of unknown-amount lines, then the priced line sum."""

    def __init__(self, *, unknown_count, line_sum):
        self._answers = [unknown_count, line_sum]
        self.calls = 0

    async def scalar(self, _stmt):
        answer = self._answers[self.calls] if self.calls < len(self._answers) else None
        self.calls += 1
        return answer


async def test_reconcile_total_to_lines_uses_the_line_sum_not_the_scrape_gross():
    """A Careem order's total is the sum of its priced lines (the menu), not the
    scrape's low net-of-markup gross — so a re-promote cannot revert it. All line
    amounts are known here, so the line sum is trusted."""
    order = SimpleNamespace(
        total=Decimal("63.00"),
        subtotal=Decimal("63.00"),
        total_excl_vat=Decimal("60.00"),
        vat_amount=Decimal("3.00"),
        vat_rate=Decimal("0.05"),
        discount_amount=Decimal("0"),
        id=uuid.uuid4(),
    )
    agg = _agg(channel="careem")

    db = _OrderedScalarDB(unknown_count=0, line_sum=Decimal("90.00"))
    await promote._reconcile_total_to_lines(db, order, agg)
    assert order.total == Decimal("90.00")
    assert order.subtotal == Decimal("90.00")  # VAT-inclusive == total for Careem


async def test_reconcile_total_keeps_the_scrape_total_when_no_lines_are_priced():
    """A Talabat statement carries the order total but no line prices; with a zero
    line sum the header total must stand rather than collapse to 0."""
    order = SimpleNamespace(
        total=Decimal("140.00"),
        subtotal=Decimal("140.00"),
        total_excl_vat=Decimal("133.33"),
        vat_amount=Decimal("6.67"),
        vat_rate=Decimal("0.05"),
        discount_amount=Decimal("0"),
        id=uuid.uuid4(),
    )
    agg = _agg(channel="talabat")

    db = _OrderedScalarDB(unknown_count=0, line_sum=Decimal("0"))
    await promote._reconcile_total_to_lines(db, order, agg)
    assert order.total == Decimal("140.00")  # untouched


async def test_reconcile_total_skips_when_any_line_amount_is_unknown():
    """F-AGG-1: a single unpriced line makes the summed line total a PARTIAL
    undercount, so the header total must be left as the scrape reported it and never
    lowered to that partial sum. Here two of three lines are priced (line sum 60),
    below the real 90 total — the total must stand."""
    order = SimpleNamespace(
        total=Decimal("90.00"),
        subtotal=Decimal("90.00"),
        total_excl_vat=Decimal("85.71"),
        vat_amount=Decimal("4.29"),
        vat_rate=Decimal("0.05"),
        discount_amount=Decimal("0"),
        id=uuid.uuid4(),
    )
    agg = _agg(channel="careem")

    # unknown_count > 0 → the guard returns before the line-sum query is even read.
    db = _OrderedScalarDB(unknown_count=1, line_sum=Decimal("60.00"))
    await promote._reconcile_total_to_lines(db, order, agg)
    assert order.total == Decimal("90.00")  # untouched — partial sum not trusted


async def test_reconcile_never_lowers_a_header_total():
    """Even with all amounts known, a line sum BELOW the header must not lower it —
    the header is only ever raised."""
    order = SimpleNamespace(
        total=Decimal("90.00"),
        subtotal=Decimal("90.00"),
        total_excl_vat=Decimal("85.71"),
        vat_amount=Decimal("4.29"),
        vat_rate=Decimal("0.05"),
        discount_amount=Decimal("0"),
        id=uuid.uuid4(),
    )
    agg = _agg(channel="careem")

    db = _OrderedScalarDB(unknown_count=0, line_sum=Decimal("80.00"))
    await promote._reconcile_total_to_lines(db, order, agg)
    assert order.total == Decimal("90.00")  # not lowered


async def test_reconcile_noon_keeps_net_and_records_the_discount():
    """F-AGG-2: Noon genuinely discounts. The customer paid the net (70) the scrape
    gave us; the line items carry the pre-discount gross (90). Keep the net total,
    set subtotal to the gross, and book gross − net (20) as the discount — never
    re-inflate the total to the line sum."""
    order = SimpleNamespace(
        total=Decimal("70.00"),  # the discounted net gross_sales gave us
        subtotal=Decimal("70.00"),
        total_excl_vat=Decimal("66.67"),
        vat_amount=Decimal("3.33"),
        vat_rate=Decimal("0.05"),
        discount_amount=Decimal("0"),
        id=uuid.uuid4(),
    )
    agg = _agg(channel="noon")

    db = _OrderedScalarDB(unknown_count=0, line_sum=Decimal("90.00"))
    await promote._reconcile_total_to_lines(db, order, agg)
    assert order.total == Decimal("70.00")  # net kept, NOT raised to 90
    assert order.subtotal == Decimal("90.00")  # the gross
    assert order.discount_amount == Decimal("20.00")  # gross − net
    # subtotal − discount == total (the invariant migration 198 encodes)
    assert order.subtotal - order.discount_amount == order.total
