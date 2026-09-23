"""
Counter promotion rules — the pure half of the counter promotion engine.

Everything here takes plain data and an explicit local clock and touches no
database, so the same rules can be golden-tested and mirrored by the register's
local pricing engine (Task 2) without a session. `auto_promotion_service` does
the DB fetch and the `OrderDiscount` reconciliation around it.

The model the rules encode:

* A promotion runs at a branch in one of two **modes** — `auto` (the register
  puts it on every qualifying check by itself) or `coupon` (a one-tap chip the
  cashier chooses). `Promotion.auto_branch_ids` / `coupon_branch_ids` say
  which, per branch; a branch in neither list does not run it at all. The two
  lists never overlap (DB CHECK + API validation).
* **One promotion per order.** A selected coupon replaces the auto winner.
* A selected coupon that is **not eligible right now** (min spend not met,
  window closed) is not applied, and the auto winner applies in its place. The
  coupon stays selected on the order, so it re-applies by itself once the check
  qualifies again. Falling back rather than applying nothing is deliberate: the
  cashier tapped a chip to give the customer *more*, never to take away the
  discount the shop gives everyone anyway.
* A manual order-level discount stands both down — that rule lives in the
  service, because it reads `OrderDiscount` rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

Mode = Literal["auto", "coupon"]

#: The rewards the counter engine can apply unattended: both reduce to one
#: order-level discount (or one per matching line when category-scoped).
ORDER_LEVEL_REWARDS = frozenset({"percentage_off_order", "fixed_off_order"})

PERCENTAGE_REWARD = "percentage_off_order"

#: `order_discounts.value` is `Numeric(10, 4)`.
_FRACTION = Decimal("0.0001")


@dataclass(frozen=True)
class PromoRule:
    """A promotion reduced to the fields the rules read. Plain data."""

    id: uuid.UUID
    name: str
    reward: str
    reward_value: Decimal
    trigger: str
    trigger_value: Decimal
    category_ids: frozenset[uuid.UUID]
    branch_ids: frozenset[uuid.UUID]
    order_types: frozenset[str]
    sources: frozenset[str]
    auto_branch_ids: frozenset[uuid.UUID]
    coupon_branch_ids: frozenset[uuid.UUID]
    priority: int
    #: Tie-break: newest wins among equal priorities. Epoch seconds.
    created_ts: float
    is_active: bool
    is_deleted: bool
    from_date: date | None
    to_date: date | None
    from_time: int
    to_time: int
    #: Monday=0 … Sunday=6, Python's `weekday()` convention.
    weekdays: tuple[bool, bool, bool, bool, bool, bool, bool]


@dataclass(frozen=True)
class OrderFacts:
    """What the rules need to know about the check being priced."""

    source: str | None
    branch_id: uuid.UUID | None
    order_type: str | None
    #: The pre-discount value of the billable lines (see `_spend_basis`).
    spend: Decimal


@dataclass(frozen=True)
class LineFacts:
    """One billable line, for category targeting."""

    id: Any
    category_id: uuid.UUID | None


def _flag(value: Any) -> bool:
    # An unflushed ORM instance has None for server-defaulted booleans; the
    # server default for every weekday flag is true.
    return True if value is None else bool(value)


def rule_from(promo: Any) -> PromoRule:
    """Snapshot a `Promotion` (or anything shaped like one) into a `PromoRule`.

    Tolerates the Nones an unflushed ORM instance carries for server-defaulted
    columns, so a `Promotion(...)` built in memory reads the same as a row.
    """
    created = getattr(promo, "created_at", None)
    return PromoRule(
        id=promo.id,
        name=promo.name,
        reward=promo.reward,
        reward_value=Decimal(str(promo.reward_value or 0)),
        trigger=promo.trigger or "spend",
        trigger_value=Decimal(str(promo.trigger_value or 0)),
        category_ids=frozenset(getattr(promo, "category_ids", None) or ()),
        branch_ids=frozenset(promo.branch_ids or ()),
        order_types=frozenset(promo.order_types or ()),
        sources=frozenset(promo.sources or ()),
        auto_branch_ids=frozenset(getattr(promo, "auto_branch_ids", None) or ()),
        coupon_branch_ids=frozenset(getattr(promo, "coupon_branch_ids", None) or ()),
        priority=promo.priority if promo.priority is not None else 100,
        created_ts=created.timestamp() if created is not None else 0.0,
        is_active=_flag(promo.is_active),
        is_deleted=promo.deleted_at is not None,
        from_date=promo.from_date,
        to_date=promo.to_date,
        from_time=promo.from_time if promo.from_time is not None else 0,
        to_time=promo.to_time if promo.to_time is not None else 1439,
        weekdays=(
            _flag(promo.is_mon),
            _flag(promo.is_tue),
            _flag(promo.is_wed),
            _flag(promo.is_thu),
            _flag(promo.is_fri),
            _flag(promo.is_sat),
            _flag(promo.is_sun),
        ),
    )


# ─── Scope, mode and schedule ─────────────────────────────────────────────────


def mode_at(rule: PromoRule, branch_id: uuid.UUID | None) -> Mode | None:
    """How `rule` runs at `branch_id`: `auto`, `coupon`, or not at all."""
    if branch_id is None:
        return None
    if branch_id in rule.auto_branch_ids:
        return "auto"
    if branch_id in rule.coupon_branch_ids:
        return "coupon"
    return None


def in_window(rule: PromoRule, local_dt: datetime) -> bool:
    """Whether the date range, weekday and minutes window cover `local_dt`.

    `local_dt` is the shop's local wall clock. A minutes window with
    `from_time > to_time` crosses midnight (22:00 → 02:00), and — as in
    `ScheduleMixin.runs_at` — the weekday is the calendar day of `local_dt`,
    not the day the window opened.
    """
    today = local_dt.date()
    if rule.from_date and today < rule.from_date:
        return False
    if rule.to_date and today > rule.to_date:
        return False
    if not rule.weekdays[local_dt.weekday()]:
        return False
    minutes = local_dt.hour * 60 + local_dt.minute
    if rule.from_time <= rule.to_time:
        return rule.from_time <= minutes <= rule.to_time
    return minutes >= rule.from_time or minutes <= rule.to_time


def is_counter_shape(rule: PromoRule) -> bool:
    """A shape the counter engine can apply: order-level reward, spend trigger,
    and an explicit channel scope.

    An unscoped (`sources` empty) promotion would discount every channel — the
    website and every marketplace too — so it is never applied here, even if a
    row slipped past the API's guard.
    """
    return (
        rule.reward in ORDER_LEVEL_REWARDS
        and rule.trigger == "spend"
        and bool(rule.sources)
    )


def matches_scope(rule: PromoRule, facts: OrderFacts) -> bool:
    """Channel / branch / order-type scope — "empty means everything"."""
    if rule.sources and facts.source not in rule.sources:
        return False
    if rule.branch_ids and facts.branch_id not in rule.branch_ids:
        return False
    if rule.order_types and facts.order_type not in rule.order_types:
        return False
    return True


def is_live(rule: PromoRule, local_dt: datetime) -> bool:
    """Switched on, not deleted, and inside its schedule at `local_dt`."""
    return rule.is_active and not rule.is_deleted and in_window(rule, local_dt)


def is_eligible(
    rule: PromoRule, facts: OrderFacts, local_dt: datetime, *, mode: Mode
) -> bool:
    """Whether `rule` applies to this check, right now, in `mode`."""
    return (
        is_counter_shape(rule)
        and mode_at(rule, facts.branch_id) == mode
        and is_live(rule, local_dt)
        and matches_scope(rule, facts)
        and facts.spend >= rule.trigger_value
    )


def rank(rule: PromoRule) -> tuple[int, float]:
    # Lowest priority wins; newest breaks a tie, matching `advertisable`'s rule
    # that publishing a replacement retires the one before it.
    return (rule.priority, -rule.created_ts)


def eligible(
    rules: Iterable[PromoRule],
    facts: OrderFacts,
    local_dt: datetime,
    *,
    mode: Mode = "auto",
) -> list[PromoRule]:
    """Every rule eligible in `mode`, best first."""
    out = [r for r in rules if is_eligible(r, facts, local_dt, mode=mode)]
    out.sort(key=rank)
    return out


def choose(
    rules: Iterable[PromoRule],
    facts: OrderFacts,
    local_dt: datetime,
    coupon_id: uuid.UUID | None = None,
) -> PromoRule | None:
    """The one promotion this check gets.

    The selected coupon when it is eligible as a coupon here and now; otherwise
    the best auto candidate. An ineligible coupon falls back to auto rather than
    to nothing — see the module docstring.
    """
    rules = list(rules)
    if coupon_id is not None:
        coupon = next((r for r in rules if r.id == coupon_id), None)
        if coupon is not None and is_eligible(coupon, facts, local_dt, mode="coupon"):
            return coupon
    autos = eligible(rules, facts, local_dt, mode="auto")
    return autos[0] if autos else None


# ─── Discount shape ───────────────────────────────────────────────────────────


def discount_value(rule: PromoRule) -> tuple[bool, Decimal]:
    """`(is_percentage, value)` as the pricing engine wants them.

    `reward_value` is a percent for a percentage reward (15 == 15%); pricing
    takes a fraction. A fixed reward is already an AED amount.

    The fraction is held to four decimals — the precision `order_discounts.value`
    stores — so the value priced in memory and the value a later re-price reads
    back from the row are the same number (12.345% is 0.1235 either way).
    """
    if rule.reward == PERCENTAGE_REWARD:
        return True, (rule.reward_value / Decimal("100")).quantize(
            _FRACTION, rounding=ROUND_HALF_UP
        )
    return False, rule.reward_value


def per_line_targets(rule: PromoRule, lines: Iterable[LineFacts]) -> set | None:
    """Which lines take a per-item discount, or None for one order-level row.

    A category-scoped rule discounts exactly the billable lines whose product
    sits in one of its categories; an unscoped one is a single order-level
    discount spread across the check.
    """
    if not rule.category_ids:
        return None
    return {
        line.id
        for line in lines
        if line.category_id is not None and line.category_id in rule.category_ids
    }


__all__ = [
    "LineFacts",
    "Mode",
    "ORDER_LEVEL_REWARDS",
    "OrderFacts",
    "PromoRule",
    "choose",
    "discount_value",
    "eligible",
    "in_window",
    "is_counter_shape",
    "is_eligible",
    "is_live",
    "matches_scope",
    "mode_at",
    "per_line_targets",
    "rank",
    "rule_from",
]
