"""The replenishment forecast engine — pure, so every rule is pinned here.

Covers stock-out un-censoring (and its guards), the intraday bucket layout, a
closed weekday not being a stock-out, remaining-day demand following the
intraday profile, the pool branch's window for same-day vs next-day categories,
the tiered allocation under scarcity (never beyond the pool), production
rounding and the stranded-stock rule, and cold start.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest

from app.services.inventory.replenishment import engine as e

POOL = uuid.UUID(int=1)
BARSHA = uuid.UUID(int=2)
KARAMA = uuid.UUID(int=3)
COOKIES = uuid.UUID(int=100)
MELT_CAT = uuid.UUID(int=200)
CAKE_CAT = uuid.UUID(int=201)
ITEM = uuid.UUID(int=10)

EVERY_DAY = {w: ("08:00", "23:30") for w in range(7)}
BARSHA_HOURS = {w: ("08:00", "22:00") for w in range(7)}
KARAMA_HOURS = {w: ("13:00", "22:45") for w in range(7) if w != 5}  # no Friday

#: A Monday.
TODAY = date(2026, 9, 21)

#: Evening-heavy clock-hour shape: little before 16:00, most 18:00–22:00.
EVENING = [0.0] * 24
for _h, _w in {
    8: 0.2,
    9: 0.2,
    10: 0.3,
    11: 0.4,
    12: 0.5,
    13: 0.5,
    14: 0.5,
    15: 0.6,
    16: 1.0,
    17: 1.5,
    18: 2.0,
    19: 2.5,
    20: 2.5,
    21: 2.0,
    22: 1.0,
    23: 0.3,
}.items():
    EVENING[_h] = _w


def branches() -> dict[uuid.UUID, e.BranchInfo]:
    return {
        POOL: e.BranchInfo(POOL, "Sharjah", e.BranchCalendar(EVERY_DAY)),
        BARSHA: e.BranchInfo(BARSHA, "Barsha", e.BranchCalendar(BARSHA_HOURS)),
        KARAMA: e.BranchInfo(KARAMA, "Karama", e.BranchCalendar(KARAMA_HOURS)),
    }


def open_minutes(cal: e.BranchCalendar, day: date) -> list[int]:
    out = [0] * 24
    window = cal.window(day)
    if window is None:
        return out
    for hour in range(24):
        lo, hi = cal.hour_range(hour)
        out[hour] = int(max(0, min(hi, window[1]) - max(lo, window[0])))
    return out


def fact(
    branch_id: uuid.UUID,
    item_id: uuid.UUID,
    day: date,
    units: float,
    *,
    shape: list[float] = EVENING,
    in_stock: list[int] | None | str = "full",
) -> e.Fact:
    cal = branches()[branch_id].calendar
    opened = open_minutes(cal, day)
    weights = [shape[h] if opened[h] else 0.0 for h in range(24)]
    total = sum(weights) or 1.0
    hourly = tuple(units * w / total for w in weights)
    if in_stock == "full":
        stock = tuple(opened)
    elif in_stock is None:
        stock = None
    else:
        stock = tuple(in_stock)
    return e.Fact(
        branch_id=branch_id,
        item_id=item_id,
        business_date=day,
        sales_units=units if sum(opened) else 0.0,
        hourly_units=hourly if sum(opened) else tuple([0.0] * 24),
        hourly_open_minutes=tuple(opened),
        hourly_in_stock_minutes=stock,
    )


def history(
    item_id: uuid.UUID, per_branch: dict[uuid.UUID, float], days: int = 42
) -> list[e.Fact]:
    out = []
    for offset in range(1, days + 1):
        day = TODAY - timedelta(days=offset)
        for branch_id, units in per_branch.items():
            out.append(fact(branch_id, item_id, day, units))
    return out


def snapshot(
    facts: list[e.Fact],
    *,
    items: dict[uuid.UUID, e.ItemInfo] | None = None,
    on_hand: dict | None = None,
    floors: dict | None = None,
    now: datetime | None = None,
    today: date = TODAY,
    settings: e.Settings | None = None,
) -> e.Snapshot:
    return e.Snapshot(
        business_date=today,
        now=now or datetime.combine(today, time(9, 0)),
        pool_branch_id=POOL,
        branches=branches(),
        items=items
        or {ITEM: e.ItemInfo(ITEM, "Cookie", COOKIES, "pc", producible=True)},
        facts=facts,
        on_hand=on_hand or {},
        floors=floors or {},
        settings=settings or e.Settings(next_day_category_ids=frozenset({MELT_CAT})),
    )


def line(result: e.ItemForecast, branch_id: uuid.UUID) -> e.BranchLine:
    return next(x for x in result.lines if x.branch_id == branch_id)


# ─── Distributions ────────────────────────────────────────────────────────────


def test_poisson_quantile_and_overdispersion_widens_it():
    assert e.Demand(4, 4).quantile(0.9) == 7
    assert e.Demand(4, 12).quantile(0.9) > 7
    assert e.Demand(0, 0).quantile(0.9) == 0
    assert e.Demand(4, 4).prob_at_least(0) == 1.0
    assert 0 < e.Demand(4, 4).prob_at_least(5) < 0.5


# ─── Buckets and un-censoring ─────────────────────────────────────────────────


@pytest.mark.parametrize("width", [1, 3, 6])
def test_buckets_start_at_opening_and_end_at_close(width):
    model = e.ProfileModel([], branches(), width)
    buckets = model.buckets_for(POOL, TODAY)
    assert buckets[0][0] == 8 * 60
    assert buckets[-1][1] == 23 * 60 + 30
    assert all(end - start <= width * 60 for start, end in buckets)
    assert all(a[1] == b[0] for a, b in zip(buckets, buckets[1:]))
    profile = model.day(POOL, TODAY)
    assert sum(share for _, _, share in profile.buckets) == pytest.approx(1.0)


def test_in_stock_all_day_is_taken_as_sold():
    facts = history(ITEM, {POOL: 10})
    model = e.ProfileModel(facts, branches(), 3)
    est = e.estimate_demand(fact(POOL, ITEM, TODAY, 10), model)
    assert est.value == pytest.approx(10)
    assert est.weight == 1.0 and not est.censored


def test_running_out_in_the_afternoon_scales_up_by_the_evening_share():
    facts = history(ITEM, {POOL: 10})
    model = e.ProfileModel(facts, branches(), 3)
    cal = branches()[POOL].calendar
    opened = open_minutes(cal, TODAY)
    # In stock until 19:00, then out. (Out from 17:00 would be only ~26% of an
    # evening-heavy day's demand — below the 30% floor, so treated as missing.)
    stock = [opened[h] if h < 19 else 0 for h in range(24)]
    sold = fact(POOL, ITEM, TODAY, 4, in_stock=stock)
    est = e.estimate_demand(sold, model)
    assert est.censored
    assert 0.3 < est.in_stock_share < 0.6
    assert est.value == pytest.approx(4 / est.in_stock_share)
    assert est.value > 4


def test_barely_in_stock_is_missing_and_the_uplift_is_capped():
    facts = history(ITEM, {POOL: 10})
    model = e.ProfileModel(facts, branches(), 3)
    cal = branches()[POOL].calendar
    opened = open_minutes(cal, TODAY)
    morning_only = [opened[h] if h < 12 else 0 for h in range(24)]
    assert (
        e.estimate_demand(
            fact(POOL, ITEM, TODAY, 2, in_stock=morning_only), model
        ).value
        is None
    )

    # The least-in-stock day still above the 30% floor: its scale-up is capped.
    share_needed = []
    for cut in range(14, 22):
        stock = [opened[h] if h < cut else 0 for h in range(24)]
        share = model.in_stock_share(fact(POOL, ITEM, TODAY, 1, in_stock=stock))
        share_needed.append((share, stock))
    share, stock = min((s for s in share_needed if s[0] >= 0.3), key=lambda s: s[0])
    est = e.estimate_demand(fact(POOL, ITEM, TODAY, 4, in_stock=stock), model)
    assert est.value <= 4 * e.MAX_UPLIFT + 1e-9


def test_unknown_stock_is_used_as_sold_at_half_weight():
    model = e.ProfileModel([], branches(), 3)
    est = e.estimate_demand(fact(POOL, ITEM, TODAY, 5, in_stock=None), model)
    assert est.value == 5 and est.weight == e.UNKNOWN_STOCK_WEIGHT


# ─── Closed days ──────────────────────────────────────────────────────────────


def test_karama_friday_is_closed_not_a_stock_out():
    friday = date(2026, 9, 25)
    cal = branches()[KARAMA].calendar
    assert cal.window(friday) is None
    facts = history(ITEM, {POOL: 10, KARAMA: 3})
    snap = snapshot(facts, today=friday, now=datetime.combine(friday, time(9)))
    result = e.forecast(snap)[0]
    karama = line(result, KARAMA)
    assert karama.qty == 0 and karama.window_start is None

    model = e.ForecastModel(snap, e.ProfileModel(facts, snap.branches, 3))
    # Karama's history has no Friday zeros dragging its level down.
    assert model.item_models[(KARAMA, ITEM)].level == pytest.approx(3, rel=0.05)
    assert model.day_mean(KARAMA, ITEM, friday) == 0


# ─── Remaining-day demand ─────────────────────────────────────────────────────


def test_rest_of_day_demand_follows_the_intraday_profile():
    facts = history(ITEM, {POOL: 20})
    snap = snapshot(facts)
    model = e.ForecastModel(snap, e.ProfileModel(facts, snap.branches, 3))
    close = datetime.combine(TODAY, time(23, 30))
    from_morning = model.window_demand(
        POOL, ITEM, datetime.combine(TODAY, time(9)), close
    )
    from_evening = model.window_demand(
        POOL, ITEM, datetime.combine(TODAY, time(20)), close
    )
    assert from_morning.mean == pytest.approx(20, rel=0.1)
    # 20:00→23:30 is 23% of the open time but far more of an evening-heavy day.
    assert from_evening.mean > 0.23 * 20


# ─── The pool branch's window ─────────────────────────────────────────────────


def test_pool_keeps_less_for_same_day_items_than_for_cookie_melt():
    melt = uuid.UUID(int=11)
    items = {
        ITEM: e.ItemInfo(ITEM, "Cookie", CAKE_CAT, "pc", producible=True),
        melt: e.ItemInfo(melt, "Melt", MELT_CAT, "pc", producible=True),
    }
    facts = history(ITEM, {POOL: 20}) + history(melt, {POOL: 20})
    snap = snapshot(facts, items=items, on_hand={(POOL, ITEM): 200, (POOL, melt): 200})
    by_item = {r.item_id: r for r in e.forecast(snap)}
    same_day = line(by_item[ITEM], POOL)
    next_day = line(by_item[melt], POOL)
    assert same_day.window_end == datetime.combine(TODAY, time(18))
    assert next_day.window_end == datetime.combine(TODAY, time(23, 30))
    assert same_day.target < next_day.target


# ─── Allocation ───────────────────────────────────────────────────────────────


def test_plenty_sends_every_need_and_the_rest_stays_at_the_pool():
    facts = history(ITEM, {POOL: 20, BARSHA: 6, KARAMA: 3})
    snap = snapshot(facts, on_hand={(POOL, ITEM): 500})
    result = e.forecast(snap)[0]
    barsha, karama, pool = (
        line(result, BARSHA),
        line(result, KARAMA),
        line(result, POOL),
    )
    assert barsha.qty == barsha.need > 0
    assert karama.qty == karama.need > 0
    assert pool.qty == 500 - barsha.qty - karama.qty
    assert all(x.shortfall == 0 for x in result.lines)


def test_scarcity_protects_the_pool_first_then_floors_and_never_exceeds_the_pool():
    facts = history(ITEM, {POOL: 30, BARSHA: 10, KARAMA: 5})
    floors = {(POOL, ITEM): 3, (BARSHA, ITEM): 3, (KARAMA, ITEM): 3}
    probe = line(e.forecast(snapshot(facts, floors=floors))[0], POOL)
    # Enough for the pool's expected demand and every floor, plus 5 extras.
    pool_units = e._ceil(probe.window_mean) + 9 + 5
    snap = snapshot(facts, on_hand={(POOL, ITEM): pool_units}, floors=floors)
    result = e.forecast(snap)[0]
    pool, barsha, karama = (
        line(result, POOL),
        line(result, BARSHA),
        line(result, KARAMA),
    )
    assert pool.qty + barsha.qty + karama.qty == pool_units
    assert pool.qty >= e._ceil(pool.window_mean) + 3
    assert barsha.qty >= 3 and karama.qty >= 3
    assert barsha.shortfall > 0
    # The bigger branch wins more of the scarce extras than the smaller one.
    assert barsha.qty >= karama.qty


def test_too_little_for_every_floor_splits_the_floors_in_proportion():
    facts = history(ITEM, {POOL: 30, BARSHA: 10, KARAMA: 5})
    floors = {(POOL, ITEM): 3, (BARSHA, ITEM): 3, (KARAMA, ITEM): 3}
    probe = line(e.forecast(snapshot(facts, floors=floors))[0], POOL)
    pool_units = e._ceil(probe.window_mean) + 5
    result = e.forecast(
        snapshot(facts, on_hand={(POOL, ITEM): pool_units}, floors=floors)
    )[0]
    assert sum(x.qty for x in result.lines) == pool_units
    assert line(result, POOL).qty >= e._ceil(probe.window_mean)
    assert all(x.tier in (1, 2) for x in result.lines if x.qty)


def test_destination_stock_on_hand_reduces_what_it_is_sent():
    facts = history(ITEM, {POOL: 10, BARSHA: 6})
    empty = line(e.forecast(snapshot(facts, on_hand={(POOL, ITEM): 500}))[0], BARSHA)
    stocked = line(
        e.forecast(snapshot(facts, on_hand={(POOL, ITEM): 500, (BARSHA, ITEM): 4}))[0],
        BARSHA,
    )
    assert stocked.qty == max(0, empty.qty - 4)


# ─── Production ───────────────────────────────────────────────────────────────


def test_production_rounds_up_to_whole_batches():
    items = {
        ITEM: e.ItemInfo(
            ITEM,
            "Cookie",
            COOKIES,
            "pc",
            producible=True,
            basis="batch",
            batch_yield=24,
        )
    }
    facts = history(ITEM, {POOL: 30, BARSHA: 10})
    result = e.forecast(snapshot(facts, items=items, on_hand={(POOL, ITEM): 10}))[0]
    prod = result.production
    assert prod.batches >= 1
    assert prod.units == prod.batches * 24
    assert prod.units >= prod.raw_units


def test_ample_stock_means_no_production():
    facts = history(ITEM, {POOL: 10, BARSHA: 4})
    result = e.forecast(snapshot(facts, on_hand={(POOL, ITEM): 400}))[0]
    assert result.production.units == 0


def test_surplus_at_a_small_branch_does_not_cancel_production():
    facts = history(ITEM, {POOL: 20, BARSHA: 5})
    base = e.forecast(snapshot(facts, on_hand={(POOL, ITEM): 10}))[0].production
    flooded = e.forecast(
        snapshot(facts, on_hand={(POOL, ITEM): 10, (BARSHA, ITEM): 1000})
    )[0].production
    # Barsha's pile only covers Barsha's own tomorrow, not Sharjah's.
    assert flooded.units > 0
    assert flooded.units >= base.units - e._ceil(5 * 1.2 + 10)


def test_shelf_life_caps_production():
    items = {
        ITEM: e.ItemInfo(
            ITEM, "Cookie", COOKIES, "pc", producible=True, shelf_life_days=1
        )
    }
    facts = history(ITEM, {POOL: 20})
    result = e.forecast(
        snapshot(
            facts, items=items, floors={(POOL, ITEM): 40}, on_hand={(POOL, ITEM): 0}
        )
    )[0]
    assert result.production.capped_by_shelf_life
    assert result.production.units <= 20 * 1.2


def test_non_producible_items_get_no_production_line():
    items = {ITEM: e.ItemInfo(ITEM, "Bought", COOKIES, "pc", producible=False)}
    result = e.forecast(snapshot(history(ITEM, {POOL: 5}), items=items))[0]
    assert result.production is None


# ─── Seasonality and cold start ───────────────────────────────────────────────


def test_a_busy_weekday_gets_a_higher_index():
    facts = []
    for offset in range(1, 57):
        day = TODAY - timedelta(days=offset)
        facts.append(fact(POOL, ITEM, day, 20 if e.model_weekday(day) == 5 else 10))
    snap = snapshot(facts)
    model = e.ForecastModel(snap, e.ProfileModel(facts, snap.branches, 3))
    dow = model.branch_models[POOL].dow
    assert dow[5] > 1.3 > dow[1]
    assert not model.branch_models[POOL].dom_applied  # two months: too few pay cycles


def test_a_new_item_borrows_its_category_level():
    new = uuid.UUID(int=12)
    items = {
        ITEM: e.ItemInfo(ITEM, "Old", COOKIES, "pc", producible=True),
        new: e.ItemInfo(new, "New", COOKIES, "pc", producible=True),
    }
    facts = history(ITEM, {POOL: 20}) + history(new, {POOL: 2}, days=2)
    snap = snapshot(facts, items=items)
    model = e.ForecastModel(snap, e.ProfileModel(facts, snap.branches, 3))
    young = model.item_models[(POOL, new)]
    assert young.cold_start
    assert 2 < young.level < 20


def test_weeks_before_a_branch_ramped_up_are_not_its_demand():
    facts = []
    for offset in range(1, 57):
        day = TODAY - timedelta(days=offset)
        # Two quiet months of partial data, then the real volume for three weeks.
        facts.append(fact(POOL, ITEM, day, 30 if offset <= 21 else 2))
    snap = snapshot(facts)
    model = e.ForecastModel(snap, e.ProfileModel(facts, snap.branches, 3))
    assert model.item_models[(POOL, ITEM)].level == pytest.approx(30, rel=0.1)


def test_a_big_box_floor_is_capped_where_the_item_barely_sells():
    # Barsha sells ~0.1 a day: 14 days of shelf life cover ~2, so the 9-piece
    # box floor drops to 2 — never below the single-piece sale.
    facts = history(ITEM, {POOL: 20, BARSHA: 0.1})
    snap = e.Snapshot(
        **{
            **snapshot(facts, on_hand={(POOL, ITEM): 500}).__dict__,
            "floors": {(POOL, ITEM): 9, (BARSHA, ITEM): 9},
            "min_floors": {(POOL, ITEM): 1, (BARSHA, ITEM): 1},
        }
    )
    result = e.forecast(snap)[0]
    assert line(result, POOL).floor == 9
    assert 1 <= line(result, BARSHA).floor <= 2
