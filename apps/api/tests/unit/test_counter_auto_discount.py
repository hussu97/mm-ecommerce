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
        customer_tag_ids=[],
        auto_apply=True,
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


def _order(*, source="cashier", items=None, discounts=None, order_type="pickup"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_pos=True,
        pos_status="active",
        source=source,
        branch_id=uuid.uuid4(),
        order_type=order_type,
        items=items if items is not None else [_item("100")],
        order_discounts=discounts if discounts is not None else [],
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

    @pytest.mark.parametrize("channel", ["online", "aggregator", "api", "call_center"])
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
            branch_id=uuid.uuid4(),
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
