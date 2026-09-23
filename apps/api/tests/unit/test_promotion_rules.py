"""
`promotion_rules` — the pure counter promotion rules, no database.

These pin the decisions the register's local pricing engine will mirror: which
mode a branch runs a promotion in, when a schedule is live (including a window
that crosses midnight), what a check must spend, which promotion wins, and which
lines a category-scoped one covers.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.marketing import Promotion
from app.services.pos import promotion_rules as rules
from app.services.pos.promotion_rules import LineFacts, OrderFacts

DXB = ZoneInfo("Asia/Dubai")
A = uuid.uuid4()
B = uuid.uuid4()
# 2026-09-23 is a Wednesday.
WED_NOON = datetime(2026, 9, 23, 12, 0, tzinfo=DXB)


def _rule(**overrides) -> rules.PromoRule:
    fields = dict(
        id=uuid.uuid4(),
        name="Counter 15% Off",
        reward="percentage_off_order",
        reward_value=Decimal("15"),
        trigger="spend",
        trigger_value=Decimal("0"),
        category_ids=frozenset(),
        branch_ids=frozenset(),
        order_types=frozenset(),
        sources=frozenset({"cashier"}),
        auto_branch_ids=frozenset({A}),
        coupon_branch_ids=frozenset(),
        priority=100,
        created_ts=0.0,
        is_active=True,
        is_deleted=False,
        from_date=None,
        to_date=None,
        from_time=0,
        to_time=1439,
        weekdays=(True,) * 7,
    )
    for key in (
        "category_ids",
        "branch_ids",
        "sources",
        "auto_branch_ids",
        "coupon_branch_ids",
        "order_types",
    ):
        if key in overrides:
            overrides[key] = frozenset(overrides[key])
    fields.update(overrides)
    return rules.PromoRule(**fields)


def _facts(**overrides) -> OrderFacts:
    fields = dict(
        source="cashier", branch_id=A, order_type="pickup", spend=Decimal("100")
    )
    fields.update(overrides)
    return OrderFacts(**fields)


class TestModeAt:
    def test_auto_coupon_and_nowhere(self):
        rule = _rule(auto_branch_ids=[A], coupon_branch_ids=[B])
        assert rules.mode_at(rule, A) == "auto"
        assert rules.mode_at(rule, B) == "coupon"
        assert rules.mode_at(rule, uuid.uuid4()) is None
        assert rules.mode_at(rule, None) is None

    def test_empty_lists_mean_nowhere_not_everywhere(self):
        rule = _rule(auto_branch_ids=[], coupon_branch_ids=[])
        assert rules.mode_at(rule, A) is None


class TestWindow:
    def test_all_day_every_day(self):
        assert rules.in_window(_rule(), WED_NOON)

    def test_weekday_off(self):
        weekdays = [True] * 7
        weekdays[2] = False  # Wednesday
        assert not rules.in_window(_rule(weekdays=tuple(weekdays)), WED_NOON)

    def test_date_range_is_inclusive(self):
        today = WED_NOON.date()
        assert rules.in_window(_rule(from_date=today, to_date=today), WED_NOON)
        assert not rules.in_window(_rule(from_date=today + timedelta(days=1)), WED_NOON)
        assert not rules.in_window(_rule(to_date=today - timedelta(days=1)), WED_NOON)

    @pytest.mark.parametrize(
        "hh,mm,live",
        [(9, 59, False), (10, 0, True), (14, 0, True), (14, 1, False)],
    )
    def test_same_day_window_edges(self, hh, mm, live):
        rule = _rule(from_time=600, to_time=840)  # 10:00 → 14:00
        assert rules.in_window(rule, WED_NOON.replace(hour=hh, minute=mm)) is live

    @pytest.mark.parametrize(
        "hh,mm,live",
        [
            (21, 59, False),
            (22, 0, True),
            (23, 59, True),
            (0, 0, True),
            (2, 0, True),
            (2, 1, False),
            (12, 0, False),
        ],
    )
    def test_window_crossing_midnight(self, hh, mm, live):
        rule = _rule(from_time=1320, to_time=120)  # 22:00 → 02:00
        assert rules.in_window(rule, WED_NOON.replace(hour=hh, minute=mm)) is live

    def test_inactive_or_deleted_is_never_live(self):
        assert not rules.is_live(_rule(is_active=False), WED_NOON)
        assert not rules.is_live(_rule(is_deleted=True), WED_NOON)


class TestEligibility:
    def test_spend_floor_is_inclusive(self):
        rule = _rule(trigger_value=Decimal("100"))
        assert rules.is_eligible(
            rule, _facts(spend=Decimal("100")), WED_NOON, mode="auto"
        )
        assert not rules.is_eligible(
            rule, _facts(spend=Decimal("99.99")), WED_NOON, mode="auto"
        )

    def test_mode_must_match_the_branch(self):
        rule = _rule(auto_branch_ids=[A], coupon_branch_ids=[B])
        assert rules.is_eligible(rule, _facts(branch_id=A), WED_NOON, mode="auto")
        assert not rules.is_eligible(rule, _facts(branch_id=A), WED_NOON, mode="coupon")
        assert rules.is_eligible(rule, _facts(branch_id=B), WED_NOON, mode="coupon")
        assert not rules.is_eligible(rule, _facts(branch_id=B), WED_NOON, mode="auto")

    @pytest.mark.parametrize("source", ["online", "aggregator"])
    def test_counter_only(self, source):
        assert not rules.is_eligible(
            _rule(), _facts(source=source), WED_NOON, mode="auto"
        )

    def test_unscoped_sources_never_apply(self):
        assert not rules.is_eligible(_rule(sources=[]), _facts(), WED_NOON, mode="auto")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"reward": "percentage_off_products"},
            {"reward": "free_product"},
            {"trigger": "quantity"},
        ],
    )
    def test_only_order_level_spend_shapes(self, overrides):
        assert not rules.is_eligible(
            _rule(**overrides), _facts(), WED_NOON, mode="auto"
        )

    def test_order_type_and_branch_scope(self):
        rule = _rule(order_types=["delivery"])
        assert not rules.is_eligible(rule, _facts(), WED_NOON, mode="auto")
        scoped = _rule(branch_ids=[B], auto_branch_ids=[A])
        assert not rules.is_eligible(scoped, _facts(branch_id=A), WED_NOON, mode="auto")

    def test_ranking_lowest_priority_then_newest(self):
        old = _rule(name="old", priority=10, created_ts=1.0)
        new = _rule(name="new", priority=10, created_ts=2.0)
        low = _rule(name="low", priority=50, created_ts=3.0)
        ranked = rules.eligible([low, old, new], _facts(), WED_NOON)
        assert [r.name for r in ranked] == ["new", "old", "low"]


class TestChoose:
    def test_no_coupon_gets_the_auto_winner(self):
        auto = _rule(name="auto")
        assert rules.choose([auto], _facts(), WED_NOON) is auto

    def test_eligible_coupon_replaces_auto(self):
        auto = _rule(name="auto")
        coupon = _rule(name="coupon", auto_branch_ids=[], coupon_branch_ids=[A])
        assert rules.choose([auto, coupon], _facts(), WED_NOON, coupon.id) is coupon

    def test_ineligible_coupon_falls_back_to_auto(self):
        auto = _rule(name="auto")
        coupon = _rule(
            name="coupon",
            auto_branch_ids=[],
            coupon_branch_ids=[A],
            trigger_value=Decimal("500"),
        )
        assert rules.choose([auto, coupon], _facts(), WED_NOON, coupon.id) is auto

    def test_ineligible_coupon_and_no_auto_is_nothing(self):
        coupon = _rule(
            auto_branch_ids=[], coupon_branch_ids=[A], trigger_value=Decimal("500")
        )
        assert rules.choose([coupon], _facts(), WED_NOON, coupon.id) is None

    def test_a_coupon_id_naming_an_auto_promotion_is_not_a_coupon(self):
        auto = _rule(name="auto", trigger_value=Decimal("0"))
        other_auto = _rule(name="other", priority=500)
        # Selecting an auto-mode promotion as the "coupon" does not promote it
        # over the winner: it is not a coupon at this branch.
        assert (
            rules.choose([auto, other_auto], _facts(), WED_NOON, other_auto.id) is auto
        )

    def test_coupon_outside_its_window_falls_back(self):
        auto = _rule(name="auto")
        coupon = _rule(
            auto_branch_ids=[], coupon_branch_ids=[A], from_time=1320, to_time=120
        )
        assert rules.choose([auto, coupon], _facts(), WED_NOON, coupon.id) is auto
        late = WED_NOON.replace(hour=23)
        assert rules.choose([auto, coupon], _facts(), late, coupon.id) is coupon


class TestDiscountShape:
    def test_percentage_is_a_fraction(self):
        assert rules.discount_value(_rule(reward_value=Decimal("15"))) == (
            True,
            Decimal("0.15"),
        )

    def test_fixed_is_an_amount(self):
        assert rules.discount_value(
            _rule(reward="fixed_off_order", reward_value=Decimal("10"))
        ) == (False, Decimal("10"))

    def test_per_line_targets(self):
        cookies, cakes = uuid.uuid4(), uuid.uuid4()
        lines = [
            LineFacts(id="cookie", category_id=cookies),
            LineFacts(id="cake", category_id=cakes),
            LineFacts(id="open-price", category_id=None),
        ]
        assert rules.per_line_targets(_rule(category_ids=[cookies]), lines) == {
            "cookie"
        }
        assert rules.per_line_targets(_rule(), lines) is None, "unscoped = order-level"


class TestRuleFrom:
    def test_an_unflushed_promotion_reads_like_a_row(self):
        # Built in memory: server-defaulted columns are None until a flush.
        promo = Promotion(
            id=uuid.uuid4(),
            name="Counter",
            reward="percentage_off_order",
            reward_value=Decimal("15"),
            branch_ids=[],
            order_types=[],
            sources=["cashier"],
            deleted_at=None,
        )
        rule = rules.rule_from(promo)
        assert rule.auto_branch_ids == frozenset()
        assert rule.coupon_branch_ids == frozenset()
        assert rule.weekdays == (True,) * 7
        assert (rule.from_time, rule.to_time) == (0, 1439)
        assert rule.trigger == "spend" and rule.is_active

    def test_carries_the_branch_modes(self):
        promo = SimpleNamespace(
            id=uuid.uuid4(),
            name="x",
            reward="fixed_off_order",
            reward_value=Decimal("5"),
            trigger="spend",
            trigger_value=Decimal("20"),
            category_ids=[],
            branch_ids=[],
            order_types=[],
            sources=["cashier"],
            auto_branch_ids=[A],
            coupon_branch_ids=[B],
            priority=1,
            created_at=datetime(2026, 1, 1, tzinfo=DXB),
            is_active=True,
            deleted_at=None,
            from_date=date(2026, 1, 1),
            to_date=None,
            from_time=0,
            to_time=1439,
            is_mon=True,
            is_tue=True,
            is_wed=False,
            is_thu=True,
            is_fri=True,
            is_sat=True,
            is_sun=True,
        )
        rule = rules.rule_from(promo)
        assert rules.mode_at(rule, A) == "auto" and rules.mode_at(rule, B) == "coupon"
        assert rule.weekdays[2] is False
        assert rule.trigger_value == Decimal("20")
