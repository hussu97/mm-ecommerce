"""
The standing counter discount: every POS-rung order is 15% off, and nothing
else is.

`auto_promotion_service.sync_auto_discounts` is the single place a promotion
becomes an order discount without a cashier. These tests pin the four things
that make it safe to leave running on every re-price: it fires for the counter
and only the counter, it respects a spend floor, it stands down for a manual
discount, and it is idempotent.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.models.base import utcnow
from app.models.marketing import Promotion
from app.models.pos_order import DiscountSourceEnum
from app.services.pos import auto_promotion_service

pytestmark = pytest.mark.asyncio

#: The branch every fixture order is rung at. Promotions run by branch mode —
#: `auto_branch_ids` / `coupon_branch_ids` — so the default fixture promotion is
#: auto here, the way migration 282 backfilled every standing auto promotion.
BRANCH = uuid.uuid4()
OTHER_BRANCH = uuid.uuid4()


def _promo(**overrides) -> Promotion:
    """An always-on 15%-off-order promotion scoped to the counter."""
    fields = dict(
        id=uuid.uuid4(),
        name="Counter 15% Off",
        type="basic",
        trigger="spend",
        trigger_value=Decimal("0"),
        reward="percentage_off_order",
        reward_value=Decimal("15"),
        trigger_product_ids=[],
        reward_product_ids=[],
        category_ids=[],
        branch_ids=[],
        order_types=[],
        sources=["cashier"],
        auto_apply=True,
        auto_branch_ids=[BRANCH],
        coupon_branch_ids=[],
        priority=100,
        max_uses_per_order=1,
        is_active=True,
        deleted_at=None,
        from_date=None,
        to_date=None,
        from_time=0,
        to_time=1439,
        is_mon=True,
        is_tue=True,
        is_wed=True,
        is_thu=True,
        is_fri=True,
        is_sat=True,
        is_sun=True,
        created_at=utcnow(),
    )
    fields.update(overrides)
    return Promotion(**fields)


def _item(
    price: str,
    qty: int = 1,
    *,
    returned: int = 0,
    status: str = "active",
    product_id=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        product_id=product_id if product_id is not None else uuid.uuid4(),
        base_price=Decimal(price),
        options_price=Decimal("0"),
        quantity=qty,
        returned_quantity=returned,
        status=status,
    )


def _order(
    *,
    source="cashier",
    items=None,
    discounts=None,
    order_type="pickup",
    branch_id=None,
    coupon_id=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_pos=True,
        pos_status="active",
        source=source,
        branch_id=branch_id or BRANCH,
        order_type=order_type,
        items=items if items is not None else [_item("100")],
        order_discounts=discounts if discounts is not None else [],
        applied_coupon_promotion_id=coupon_id,
    )


def _db(promos: list[Promotion], products: list[tuple] | None = None):
    """A db that hands back the candidate promotions, and — for the
    category-scoped path — the `(product_id, category_id)` rows the service
    looks up to decide which lines a promotion covers.

    One result object serves both queries because they read it differently:
    `_candidates` calls `.scalars().all()` (promotions), the product lookup
    calls `.all()` (id/category tuples), so neither sees the other's data.
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = promos
    result.all.return_value = products or []
    db = SimpleNamespace()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    return db


@pytest.fixture(autouse=True)
def _fixed_tz(monkeypatch):
    monkeypatch.setattr(
        auto_promotion_service.business_day_service,
        "resolve_timezone",
        AsyncMock(return_value=ZoneInfo("Asia/Dubai")),
    )


def _auto_discounts(order) -> list:
    return [
        d
        for d in order.order_discounts
        if d.source == DiscountSourceEnum.PROMOTION.value
    ]


class TestCounterScope:
    async def test_counter_order_gets_the_discount(self):
        order = _order(source="cashier")
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)

        added = _auto_discounts(order)
        assert len(added) == 1
        d = added[0]
        assert d.is_percentage is True
        assert d.value == Decimal("0.15"), "15% must reach pricing as the fraction 0.15"
        assert d.order_item_id is None, "an order-level discount, not a line one"
        assert d.applied_by_id is None, "nobody applied it — the engine did"

    @pytest.mark.parametrize("channel", ["online", "aggregator"])
    async def test_non_counter_channels_get_nothing(self, channel):
        order = _order(source=channel)
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)
        assert _auto_discounts(order) == [], (
            f"a {channel} order inherited a counter-only promotion"
        )

    async def test_fixed_reward_is_an_amount_not_a_fraction(self):
        order = _order(source="cashier")
        promo = _promo(reward="fixed_off_order", reward_value=Decimal("10"))
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)

        d = _auto_discounts(order)[0]
        assert d.is_percentage is False
        assert d.value == Decimal("10")


class TestSpendFloor:
    async def test_below_the_floor_no_discount(self):
        order = _order(items=[_item("50")])  # spend 50
        promo = _promo(trigger_value=Decimal("100"))
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert _auto_discounts(order) == []

    async def test_at_the_floor_discount_applies(self):
        order = _order(items=[_item("100"), _item("50")])  # spend 150
        promo = _promo(trigger_value=Decimal("100"))
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert len(_auto_discounts(order)) == 1

    async def test_voided_and_returned_units_do_not_count_toward_spend(self):
        order = _order(
            items=[_item("100", status="void"), _item("30", qty=2, returned=2)],
        )
        promo = _promo(trigger_value=Decimal("50"))
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert _auto_discounts(order) == [], (
            "a voided line and fully-returned units left nothing billable"
        )


class TestCashierOverride:
    async def test_manual_order_discount_stands_the_promotion_down(self):
        manual = SimpleNamespace(
            order_item_id=None,
            source=DiscountSourceEnum.OPEN.value,
            reference_id=None,
        )
        order = _order(discounts=[manual])
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)

        assert _auto_discounts(order) == [], "the promotion fought a manual discount"
        assert manual in order.order_discounts, "the manual discount was disturbed"

    async def test_a_line_discount_does_not_block_the_order_promotion(self):
        line_discount = SimpleNamespace(
            order_item_id=uuid.uuid4(),  # scoped to a line, not the order
            source=DiscountSourceEnum.OPEN.value,
            reference_id=None,
        )
        order = _order(discounts=[line_discount])
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)
        assert len(_auto_discounts(order)) == 1


class TestIdempotenceAndTeardown:
    async def test_running_twice_keeps_one_discount(self):
        order = _order()
        db = _db([_promo()])
        await auto_promotion_service.sync_auto_discounts(db, order)
        await auto_promotion_service.sync_auto_discounts(db, order)
        assert len(_auto_discounts(order)) == 1, (
            "the second pass duplicated the discount"
        )

    async def test_discount_is_removed_when_the_promotion_stops_qualifying(self):
        order = _order()
        # First pass adds it.
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)
        assert len(_auto_discounts(order)) == 1
        # Now nothing qualifies (e.g. deactivated) — it must be cleared.
        await auto_promotion_service.sync_auto_discounts(_db([]), order)
        assert _auto_discounts(order) == []

    async def test_closed_order_is_left_frozen(self):
        order = _order()
        order.pos_status = "closed"
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)
        assert _auto_discounts(order) == [], "a closed check must not be re-discounted"


class TestCategoryScope:
    """
    A promotion with `category_ids` discounts only the lines whose product is in
    one of those categories — as one per-item row each — and leaves the rest of
    the check alone.
    """

    async def test_only_matching_category_lines_get_a_per_item_discount(self):
        cookies = uuid.uuid4()
        cakes = uuid.uuid4()
        cookie = _item("40")  # in cookies → discounted
        cake = _item("100")  # in cakes → untouched
        order = _order(items=[cookie, cake])
        promo = _promo(category_ids=[cookies])
        db = _db(
            [promo],
            products=[(cookie.product_id, cookies), (cake.product_id, cakes)],
        )

        await auto_promotion_service.sync_auto_discounts(db, order)

        added = _auto_discounts(order)
        assert len(added) == 1, "only the cookie line should be discounted"
        d = added[0]
        assert d.order_item_id == cookie.id, "the discount is scoped to the line"
        assert d.is_percentage is True
        assert d.value == Decimal("0.15")

    async def test_every_matching_line_gets_its_own_row(self):
        cookies = uuid.uuid4()
        a = _item("40")
        b = _item("30")
        order = _order(items=[a, b])
        promo = _promo(category_ids=[cookies])
        db = _db([promo], products=[(a.product_id, cookies), (b.product_id, cookies)])

        await auto_promotion_service.sync_auto_discounts(db, order)

        scoped = {d.order_item_id for d in _auto_discounts(order)}
        assert scoped == {a.id, b.id}, "each matching line owns one discount"

    async def test_no_matching_lines_means_no_discount(self):
        cookies = uuid.uuid4()
        cake = _item("100")
        order = _order(items=[cake])
        promo = _promo(category_ids=[cookies])
        db = _db([promo], products=[(cake.product_id, uuid.uuid4())])

        await auto_promotion_service.sync_auto_discounts(db, order)
        assert _auto_discounts(order) == []

    async def test_running_twice_keeps_one_row_per_line(self):
        cookies = uuid.uuid4()
        cookie = _item("40")
        order = _order(items=[cookie])
        promo = _promo(category_ids=[cookies])
        db = _db([promo], products=[(cookie.product_id, cookies)])

        await auto_promotion_service.sync_auto_discounts(db, order)
        await auto_promotion_service.sync_auto_discounts(db, order)
        assert len(_auto_discounts(order)) == 1, "the second pass duplicated a row"

    async def test_narrowing_from_whole_order_clears_the_order_level_row(self):
        cookies = uuid.uuid4()
        cookie = _item("40")
        order = _order(items=[cookie])

        # First: a whole-order promotion (no categories) → one order-level row.
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)
        assert _auto_discounts(order)[0].order_item_id is None

        # Then the same promotion gains a category scope → the order-level row is
        # replaced by a per-item one on the matching line.
        promo = _promo(category_ids=[cookies])
        db = _db([promo], products=[(cookie.product_id, cookies)])
        await auto_promotion_service.sync_auto_discounts(db, order)

        added = _auto_discounts(order)
        assert len(added) == 1
        assert added[0].order_item_id == cookie.id


class TestUnscopedAutoApplyIsRefused:
    """
    An auto-apply promotion with no `sources` would discount every channel — the
    counter, the website AND every marketplace. The API refuses to create one,
    and the evaluator skips any that slipped in before the guard (F-POS-9).
    """

    async def test_create_schema_rejects_auto_apply_with_no_sources(self):
        from pydantic import ValidationError

        from app.api.v1.marketing import PromotionCreate

        with pytest.raises(ValidationError, match="sources"):
            PromotionCreate(
                name="Everywhere 15%",
                reward="percentage_off_order",
                reward_value=Decimal("15"),
                trigger="spend",
                auto_apply=True,
                sources=[],  # the hole
            )

    async def test_create_schema_accepts_auto_apply_with_a_source(self):
        from app.api.v1.marketing import PromotionCreate

        promo = PromotionCreate(
            name="Counter 15%",
            reward="percentage_off_order",
            reward_value=Decimal("15"),
            trigger="spend",
            auto_apply=True,
            sources=["cashier"],
        )
        assert promo.sources == ["cashier"]

    async def test_candidates_skips_an_unscoped_auto_promotion(self):
        # A row written before the guard: auto_apply on, sources empty.
        order = _order(source="cashier")
        unscoped = _promo(sources=[])
        await auto_promotion_service.sync_auto_discounts(_db([unscoped]), order)
        assert _auto_discounts(order) == [], (
            "an unscoped auto promotion discounted an order it must not"
        )


class TestThroughRecalculate:
    """
    The whole path: `recalculate` runs the sync, then prices the order, and the
    15% actually comes off the total. Proves the wiring, not just the evaluator.
    """

    async def test_a_counter_order_total_is_15_percent_lower(self, monkeypatch):
        from app.services.pos import pos_order_service

        item = SimpleNamespace(
            id=uuid.uuid4(),
            product_id=None,
            base_price=Decimal("100"),
            options_price=Decimal("0"),
            quantity=1,
            returned_quantity=0,
            status="active",
            discount_amount=Decimal("0"),
            tax_amount=Decimal("0"),
            tax_exclusive_unit_price=Decimal("0"),
            tax_exclusive_total_price=Decimal("0"),
            total_price=Decimal("0"),
        )
        order = SimpleNamespace(
            id=uuid.uuid4(),
            is_pos=True,
            pos_status="active",
            source="cashier",
            branch_id=BRANCH,
            order_type="pickup",
            items=[item],
            order_discounts=[],
            order_charges=[],
            order_taxes=[],
            subtotal=Decimal("0"),
            discount_amount=Decimal("0"),
            charges_amount=Decimal("0"),
            vat_amount=Decimal("0"),
            total_excl_vat=Decimal("0"),
            rounding_amount=Decimal("0"),
            total=Decimal("0"),
        )

        db = _db([_promo()])
        db.get = AsyncMock(return_value=None)  # no product row → no tax group
        monkeypatch.setattr(
            pos_order_service.auto_promotion_service.business_day_service,
            "resolve_timezone",
            AsyncMock(return_value=ZoneInfo("Asia/Dubai")),
        )
        monkeypatch.setattr(
            pos_order_service, "get_order", AsyncMock(return_value=order)
        )
        monkeypatch.setattr(
            pos_order_service,
            "_settings",
            AsyncMock(return_value=SimpleNamespace(cash_rounding_step=0)),
        )
        monkeypatch.setattr(
            pos_order_service,
            "_resolve_tax",
            AsyncMock(return_value=(Decimal("0"), "No tax", None, True)),
        )

        await pos_order_service.recalculate(db, order)

        assert order.subtotal == Decimal("100.00")
        assert order.discount_amount == Decimal("15.00"), "15% of 100 did not come off"
        assert order.total == Decimal("85.00")


# ─── Branch modes: auto vs coupon (migration 282) ─────────────────────────────


def _coupon(**overrides) -> Promotion:
    """A coupon-mode promotion at `BRANCH` — never applied unless selected."""
    fields = dict(
        name="Cookies 20% coupon",
        reward_value=Decimal("20"),
        auto_apply=False,
        auto_branch_ids=[],
        coupon_branch_ids=[BRANCH],
    )
    fields.update(overrides)
    return _promo(**fields)


class TestBranchModes:
    async def test_auto_at_a_only(self):
        promo = _promo(auto_branch_ids=[BRANCH])
        here = _order(branch_id=BRANCH)
        there = _order(branch_id=OTHER_BRANCH)
        await auto_promotion_service.sync_auto_discounts(_db([promo]), here)
        await auto_promotion_service.sync_auto_discounts(_db([promo]), there)
        assert len(_auto_discounts(here)) == 1
        assert _auto_discounts(there) == [], "auto at A leaked onto branch B"

    async def test_an_auto_promotion_with_no_auto_branches_applies_nowhere(self):
        # Empty means nowhere — unlike `branch_ids`, where empty means everywhere.
        order = _order()
        promo = _promo(auto_branch_ids=[])
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert _auto_discounts(order) == []

    async def test_coupon_at_b_only_needs_selecting(self):
        coupon = _coupon(coupon_branch_ids=[OTHER_BRANCH])
        unselected = _order(branch_id=OTHER_BRANCH)
        await auto_promotion_service.sync_auto_discounts(_db([coupon]), unselected)
        assert _auto_discounts(unselected) == [], "a coupon applied itself"

        selected = _order(branch_id=OTHER_BRANCH, coupon_id=coupon.id)
        await auto_promotion_service.sync_auto_discounts(_db([coupon]), selected)
        [row] = _auto_discounts(selected)
        assert row.reference_id == coupon.id
        assert row.value == Decimal("0.20")

    async def test_a_coupon_for_b_does_nothing_at_a(self):
        coupon = _coupon(coupon_branch_ids=[OTHER_BRANCH])
        order = _order(branch_id=BRANCH, coupon_id=coupon.id)
        await auto_promotion_service.sync_auto_discounts(_db([coupon]), order)
        assert _auto_discounts(order) == []

    async def test_coupon_replaces_auto(self):
        auto = _promo()
        coupon = _coupon()
        order = _order(coupon_id=coupon.id)
        await auto_promotion_service.sync_auto_discounts(_db([auto, coupon]), order)
        rows = _auto_discounts(order)
        assert [r.reference_id for r in rows] == [coupon.id], "one promotion per order"

    async def test_coupon_removal_restores_auto(self):
        auto = _promo()
        coupon = _coupon()
        order = _order(coupon_id=coupon.id)
        db = _db([auto, coupon])
        await auto_promotion_service.sync_auto_discounts(db, order)
        first = _auto_discounts(order)[0]

        order.applied_coupon_promotion_id = None
        await auto_promotion_service.sync_auto_discounts(db, order)
        rows = _auto_discounts(order)
        assert [r.reference_id for r in rows] == [auto.id]
        assert rows[0] is first, "the managed row is re-pointed, not re-created"
        assert rows[0].value == Decimal("0.15")

    async def test_coupon_below_min_spend_is_kept_but_not_applied(self):
        auto = _promo()
        coupon = _coupon(trigger_value=Decimal("150"))
        order = _order(items=[_item("100")], coupon_id=coupon.id)
        db = _db([auto, coupon])

        await auto_promotion_service.sync_auto_discounts(db, order)
        assert [r.reference_id for r in _auto_discounts(order)] == [auto.id], (
            "an ineligible coupon must fall back to the auto promotion"
        )
        assert order.applied_coupon_promotion_id == coupon.id, "selection kept"

        # The check grows past the floor: the kept coupon applies by itself.
        order.items.append(_item("60"))
        await auto_promotion_service.sync_auto_discounts(db, order)
        assert [r.reference_id for r in _auto_discounts(order)] == [coupon.id]

    async def test_ineligible_coupon_with_no_auto_applies_nothing(self):
        coupon = _coupon(trigger_value=Decimal("500"))
        order = _order(coupon_id=coupon.id)
        await auto_promotion_service.sync_auto_discounts(_db([coupon]), order)
        assert _auto_discounts(order) == []

    async def test_manual_order_discount_stands_the_coupon_down_too(self):
        manual = SimpleNamespace(
            order_item_id=None, source=DiscountSourceEnum.OPEN.value, reference_id=None
        )
        coupon = _coupon()
        order = _order(discounts=[manual], coupon_id=coupon.id)
        await auto_promotion_service.sync_auto_discounts(_db([coupon]), order)
        assert _auto_discounts(order) == []
        assert order.order_discounts == [manual]

    async def test_category_scoped_coupon_discounts_only_its_lines(self):
        cookies = uuid.uuid4()
        cookie, cake = _item("40"), _item("100")
        coupon = _coupon(category_ids=[cookies])
        order = _order(items=[cookie, cake], coupon_id=coupon.id)
        db = _db([coupon], products=[(cookie.product_id, cookies)])
        await auto_promotion_service.sync_auto_discounts(db, order)
        assert {d.order_item_id for d in _auto_discounts(order)} == {cookie.id}

    async def test_at_prices_the_schedule_at_that_instant(self):
        from datetime import datetime

        evening = _promo(from_time=1320, to_time=120)  # 22:00 → 02:00
        dxb = ZoneInfo("Asia/Dubai")
        order = _order()
        await auto_promotion_service.sync_auto_discounts(
            _db([evening]), order, at=datetime(2026, 9, 23, 12, 0, tzinfo=dxb)
        )
        assert _auto_discounts(order) == []
        await auto_promotion_service.sync_auto_discounts(
            _db([evening]), order, at=datetime(2026, 9, 23, 23, 30, tzinfo=dxb)
        )
        assert len(_auto_discounts(order)) == 1

    async def test_promos_override_skips_the_fetch(self):
        db = _db([])
        order = _order()
        await auto_promotion_service.sync_auto_discounts(db, order, promos=[_promo()])
        assert len(_auto_discounts(order)) == 1
        db.execute.assert_not_awaited()


class TestAvailableAt:
    async def test_lists_both_modes_best_first_with_live_flag(self):
        auto = _promo(name="auto", priority=50)
        coupon = _coupon(name="coupon", priority=10)
        closed = _coupon(
            name="closed",
            priority=20,
            coupon_branch_ids=[BRANCH],
            from_date=utcnow().date().replace(year=2099),
        )
        online_only = _coupon(name="online", sources=["online"])
        product_level = _coupon(name="products", reward="percentage_off_products")
        elsewhere = _coupon(name="elsewhere", coupon_branch_ids=[OTHER_BRANCH])
        db = _db([auto, coupon, closed, online_only, product_level, elsewhere])

        rows = await auto_promotion_service.available_at(db, BRANCH)

        assert [(p.name, mode, live) for p, mode, live in rows] == [
            ("coupon", "coupon", True),
            ("closed", "coupon", False),
            ("auto", "auto", True),
        ]


class TestCouponEndpoints:
    """`pos_order_service.set_coupon` / `clear_coupon` — the PUT/DELETE bodies."""

    def _order(self, **overrides):
        fields = dict(
            id=uuid.uuid4(),
            pos_status="active",
            source="cashier",
            branch_id=BRANCH,
            applied_coupon_promotion_id=None,
        )
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def _db(self, promo):
        db = SimpleNamespace()
        db.get = AsyncMock(return_value=promo)
        db.flush = AsyncMock()
        return db

    async def test_selects_a_coupon_and_reprices(self, monkeypatch):
        from app.services.pos import pos_order_service

        recalc = AsyncMock(side_effect=lambda db, order: order)
        monkeypatch.setattr(pos_order_service, "recalculate", recalc)
        coupon = _coupon()
        order = self._order()
        await pos_order_service.set_coupon(
            self._db(coupon), order=order, promotion_id=coupon.id
        )
        assert order.applied_coupon_promotion_id == coupon.id
        recalc.assert_awaited_once()

    @pytest.mark.parametrize(
        "promo_factory",
        [
            lambda: _promo(),  # auto-mode here, not a coupon
            lambda: _coupon(coupon_branch_ids=[OTHER_BRANCH]),  # coupon elsewhere
            lambda: _coupon(is_active=False),
            lambda: _coupon(deleted_at=utcnow()),
            lambda: None,  # no such promotion
        ],
    )
    async def test_refuses_anything_not_a_coupon_here(self, monkeypatch, promo_factory):
        from app.core.exceptions import UnprocessableError
        from app.services.pos import pos_order_service

        monkeypatch.setattr(pos_order_service, "recalculate", AsyncMock())
        order = self._order()
        with pytest.raises(UnprocessableError):
            await pos_order_service.set_coupon(
                self._db(promo_factory()), order=order, promotion_id=uuid.uuid4()
            )
        assert order.applied_coupon_promotion_id is None

    @pytest.mark.parametrize(
        "overrides", [{"pos_status": "closed"}, {"source": "online"}]
    )
    async def test_refuses_a_check_the_till_cannot_reprice(
        self, monkeypatch, overrides
    ):
        from app.core.exceptions import ConflictError
        from app.services.pos import pos_order_service

        monkeypatch.setattr(pos_order_service, "recalculate", AsyncMock())
        coupon = _coupon()
        with pytest.raises(ConflictError):
            await pos_order_service.set_coupon(
                self._db(coupon), order=self._order(**overrides), promotion_id=coupon.id
            )

    async def test_clear_is_idempotent_and_reprices(self, monkeypatch):
        from app.services.pos import pos_order_service

        recalc = AsyncMock(side_effect=lambda db, order: order)
        monkeypatch.setattr(pos_order_service, "recalculate", recalc)
        order = self._order(applied_coupon_promotion_id=uuid.uuid4())
        db = self._db(None)
        await pos_order_service.clear_coupon(db, order=order)
        await pos_order_service.clear_coupon(db, order=order)
        assert order.applied_coupon_promotion_id is None
        assert recalc.await_count == 2


class TestBranchModeValidation:
    """The API side: overlap refused, shape enforced, `auto_apply` kept in step."""

    async def test_overlap_refused_on_create(self):
        from pydantic import ValidationError

        from app.schemas.marketing import PromotionCreate

        with pytest.raises(ValidationError, match="both automatically and as a coupon"):
            PromotionCreate(
                name="x",
                reward="percentage_off_order",
                sources=["cashier"],
                auto_branch_ids=[BRANCH],
                coupon_branch_ids=[BRANCH],
            )

    async def test_overlap_refused_on_update_payload(self):
        from pydantic import ValidationError

        from app.schemas.marketing import PromotionUpdate

        with pytest.raises(ValidationError):
            PromotionUpdate(auto_branch_ids=[BRANCH], coupon_branch_ids=[BRANCH])

    @pytest.mark.parametrize(
        "overrides",
        [
            {"reward": "percentage_off_products"},
            {"trigger": "quantity"},
            {"sources": []},
        ],
    )
    async def test_branch_modes_need_an_order_level_scoped_spend_shape(self, overrides):
        from pydantic import ValidationError

        from app.schemas.marketing import PromotionCreate

        fields = dict(
            name="x",
            reward="percentage_off_order",
            trigger="spend",
            sources=["cashier"],
            coupon_branch_ids=[BRANCH],
        )
        fields.update(overrides)
        with pytest.raises(ValidationError):
            PromotionCreate(**fields)

    async def test_merged_update_overlap_is_refused(self):
        from app.api.v1.marketing import _prepare_promotion_write
        from app.core.exceptions import UnprocessableError

        stored = _promo(auto_branch_ids=[BRANCH])
        with pytest.raises(UnprocessableError, match="both"):
            _prepare_promotion_write({"coupon_branch_ids": [BRANCH]}, stored)

    async def test_moving_a_branch_from_auto_to_coupon_is_allowed(self):
        from app.api.v1.marketing import _prepare_promotion_write

        stored = _promo(auto_branch_ids=[BRANCH, OTHER_BRANCH])
        extra = _prepare_promotion_write(
            {"auto_branch_ids": [OTHER_BRANCH], "coupon_branch_ids": [BRANCH]}, stored
        )
        assert extra == {"auto_apply": True}

    async def test_auto_apply_follows_auto_branch_ids(self):
        from app.api.v1.marketing import _prepare_promotion_write

        stored = _promo(auto_branch_ids=[BRANCH])
        assert _prepare_promotion_write({"auto_branch_ids": []}, stored) == {
            "auto_apply": False
        }
        # Legacy "turn it off": clears every auto branch.
        assert _prepare_promotion_write({"auto_apply": False}, stored) == {
            "auto_branch_ids": [],
            "auto_apply": False,
        }
        # Untouched lists keep what is stored.
        assert _prepare_promotion_write({"reward_value": 20}, stored) == {
            "auto_apply": True
        }

    async def test_auto_apply_true_without_branches_is_refused(self):
        from app.api.v1.marketing import _prepare_promotion_write
        from app.core.exceptions import UnprocessableError

        with pytest.raises(UnprocessableError, match="auto_branch_ids"):
            _prepare_promotion_write(
                {
                    "name": "x",
                    "reward": "percentage_off_order",
                    "sources": ["cashier"],
                    "auto_apply": True,
                },
                None,
            )

    async def test_create_with_coupon_branches_is_not_auto(self):
        from app.api.v1.marketing import _prepare_promotion_write

        assert _prepare_promotion_write(
            {
                "name": "x",
                "reward": "percentage_off_order",
                "sources": ["cashier"],
                "coupon_branch_ids": [BRANCH],
            },
            None,
        ) == {"auto_apply": False}


class TestBackfill:
    """Migration 282's backfill, read as SQL: it keeps today's behaviour and
    cannot fight a later console edit."""

    def _sql(self) -> str:
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "282_counter_promo_branch_modes.py"
        )
        return " ".join(path.read_text().split())

    async def test_revision_chain(self):
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "282_counter_promo_branch_modes.py"
        )
        spec = importlib.util.spec_from_file_location("_mig282", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "282_counter_promo_branch_modes"
        assert module.down_revision == "281_fifo_costing_v3"
        assert len(module.revision) <= 32

    async def test_auto_rows_keep_their_scope_or_every_pos_branch(self):
        sql = self._sql()
        assert "WHEN cardinality(branch_ids) > 0 THEN branch_ids" in sql
        assert "WHERE b.uses_pos = true AND b.deleted_at IS NULL" in sql
        assert "WHERE auto_apply = true AND auto_branch_ids = '{}'" in sql, (
            "the backfill must touch only auto rows not yet given a branch list"
        )

    async def test_grant_is_by_role_name_and_idempotent(self):
        sql = self._sql()
        assert '_ROLES = ("Cashier Staff", "Manager")' in sql
        assert "WHERE name IN (" in sql
        assert "NOT (permissions @> ARRAY['{_PERMISSION}']::varchar[])" in sql


class TestPermissionAndRoutes:
    async def test_the_slug_is_in_the_catalogue(self):
        from app.models.role import ALL_PERMISSIONS

        assert "pos.promotions.apply" in ALL_PERMISSIONS

    @pytest.mark.parametrize("app_module", ["app.main", "app.pos_main"])
    async def test_coupon_and_available_routes_are_on_both_apps(self, app_module):
        import importlib

        app = importlib.import_module(app_module).app
        paths = app.openapi()["paths"]
        coupon = paths["/api/v1/pos/orders/{order_id}/coupon"]
        assert {"put", "delete"} <= set(coupon)
        assert "get" in paths["/api/v1/pos/promotions/available"]

    async def test_coupon_routes_are_gated_on_the_new_slug(self):
        from app.api.v1 import pos_orders

        for endpoint in (pos_orders.apply_coupon, pos_orders.remove_coupon):
            import inspect

            gates = [
                getattr(p.default.dependency, "permission", None)
                for p in inspect.signature(endpoint).parameters.values()
                if hasattr(p.default, "dependency")
            ]
            assert "pos.promotions.apply" in gates

    async def test_order_response_carries_the_selected_coupon(self):
        from app.schemas.pos_order import OrderDiscountResponse, PosOrderResponse

        assert "applied_coupon_promotion_id" in PosOrderResponse.model_fields
        assert "reference_id" in OrderDiscountResponse.model_fields


class TestCouponBuildGate:
    @pytest.mark.parametrize(
        "build,minimum,ok",
        [
            ("1060", 1057, True),
            ("1057", 1057, True),
            ("1056", 1057, False),
            (None, 0, False),
            ("dev", 0, False),
            (" 12 ", 12, True),
        ],
    )
    async def test_build_at_least(self, build, minimum, ok):
        from app.core.pos_builds import build_at_least

        assert build_at_least(build, minimum) is ok


# ─── Usage limit ──────────────────────────────────────────────────────────────


def _used(monkeypatch, counts: dict):
    """Pretend `counts` completed orders already carry each promotion."""

    async def fake_counts(db, promotion_ids=None):
        return {pid: n for pid, n in counts.items() if pid in (promotion_ids or [])}

    monkeypatch.setattr(auto_promotion_service, "usage_counts", fake_counts)


class TestUsageLimit:
    async def test_a_promotion_under_its_limit_still_applies(self, monkeypatch):
        promo = _promo(usage_limit=4)
        _used(monkeypatch, {promo.id: 3})
        order = _order()
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert len(_auto_discounts(order)) == 1

    async def test_a_used_up_promotion_is_no_longer_applied(self, monkeypatch):
        promo = _promo(usage_limit=4)
        _used(monkeypatch, {promo.id: 4})
        order = _order()
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert _auto_discounts(order) == []

    async def test_a_used_up_coupon_falls_back_to_auto(self, monkeypatch):
        auto = _promo()  # unlimited
        coupon = _coupon(usage_limit=1)
        _used(monkeypatch, {coupon.id: 1})
        order = _order(coupon_id=coupon.id)
        await auto_promotion_service.sync_auto_discounts(_db([auto, coupon]), order)
        assert [r.reference_id for r in _auto_discounts(order)] == [auto.id]

    async def test_an_open_check_loses_it_once_the_limit_is_reached(self, monkeypatch):
        promo = _promo(usage_limit=4)
        order = _order()
        _used(monkeypatch, {promo.id: 3})
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert len(_auto_discounts(order)) == 1
        _used(monkeypatch, {promo.id: 4})  # another till completes the 4th
        await auto_promotion_service.sync_auto_discounts(_db([promo]), order)
        assert _auto_discounts(order) == []

    async def test_unlimited_promotions_never_query_the_count(self, monkeypatch):
        async def boom(*a, **k):  # pragma: no cover — must not be reached
            raise AssertionError("counted an unlimited promotion")

        monkeypatch.setattr(auto_promotion_service, "usage_counts", boom)
        order = _order()
        await auto_promotion_service.sync_auto_discounts(_db([_promo()]), order)
        assert len(_auto_discounts(order)) == 1
