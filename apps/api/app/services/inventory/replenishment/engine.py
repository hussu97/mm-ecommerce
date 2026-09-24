"""
The replenishment forecast — a pure function of a snapshot.

Given what each branch sold of each produced good (by clock hour, per business
day), when it had stock, what it holds now, and the branches' trading calendars,
this answers two questions for the morning transfer & production form:

* how many units of each produced good to **send** each destination branch from
  the production branch's pool, and how many the production branch should keep;
* how many units to **produce** today.

Nothing here touches the database; ``loaders.build_snapshot`` gathers the inputs
as of a moment, so the form, the daily shadow snapshot and the backtest all run
exactly this code.

**Demand.** Sales undercount demand on a day an item ran out. Each day's sales
are scaled up by the share of that day's demand that falls in the hours the item
was in stock (Lau & Lau): ``demand = sales / in_stock_share``, read off an
intraday profile of *bucket_hours*-wide buckets starting at the branch's opening
time (mornings sell far less than evenings). Days less than 30 % in stock are
treated as missing; the uplift is capped at 2.5×. Closed days (a holiday, Karama
on a Friday) are not stock-outs — they are skipped. Before a branch's first
physical count its stock is unknown, so those days are used as sold and given
half weight.

**Forecast.** ``mean(b, i, d) = level(b, i) × dow(b, weekday) × dom(b, d)``. The
level is an exponentially weighted mean of de-seasonalised, winsorised demand.
The weekday index is per branch, pooled over items and shrunk toward 1. The
payday (day-of-month) index is measured but only applied once enough pay cycles
exist. Items with under a week of history borrow their category's level at the
branch. Uncertainty is negative binomial with a per branch × category
dispersion from the residuals.

**Targets.** Stock to cover a window = its demand quantile at the service level
plus the availability floor — the most one sale of any menu product or option
draws of the item at that branch, so the biggest box stays sellable to close.

**Transfers.** Today's production is not in the morning pool. The production
branch keeps what carries it until its production lands (the evening, or the
whole day for next-day categories like cookie melt); each destination gets
enough to reach its target for the rest of today. When the pool is short it is
shared in tiers: (1) the production branch's expected demand, (2) a floor at
every open branch, (3) unit by unit to whichever branch gains the most expected
sales (``weight × P(demand > stock)``). The forecast never exceeds the pool.

**Production.** Periodic-review base stock: today's batch must carry the
network until the next batch lands — the production branch from today's ready
time to tomorrow's, and each destination's tomorrow (its morning transfer comes
out of this batch) — less the stock that will still be usable then. Rounded up
to whole batches, capped at the item's shelf life of forecast demand.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable

ALGO_VERSION = "v1"

#: A day less in stock than this tells too little to scale up; treat as missing.
MIN_IN_STOCK_SHARE = 0.3
#: Never believe demand was more than this multiple of what sold.
MAX_UPLIFT = 2.5
#: Weight of a day whose stock position is unknown (before the first count).
UNKNOWN_STOCK_WEIGHT = 0.5
#: Units of a branch's own sales it takes to outweigh the network's intraday shape.
PROFILE_PRIOR_UNITS = 50.0
#: Weeks of evidence a weekday index needs to move halfway from 1.
DOW_SHRINK_WEEKS = 4.0
#: Pay cycles needed before the payday index is applied at all.
DOM_MIN_CYCLES = 4
#: Days of history below which an item borrows its category's level.
COLD_START_DAYS = 7
#: Negative-binomial dispersion when there is nothing to estimate it from.
DEFAULT_DISPERSION = 0.3
MAX_DISPERSION = 3.0
#: Winsorise de-seasonalised demand at median ± this many robust SDs.
WINSOR_SDS = 3.0
#: A branch's history starts once its trailing week sells at least this share of
#: its recent daily rate — weeks before that (a branch still ramping up, or sales
#: channels not yet flowing into MM) are not today's demand.
REGIME_SHARE = 0.3

#: Day classes the intraday profile is learnt per (weekday numbering 0=Sunday).
_DAY_CLASS = {
    0: "weekend",
    1: "weekday",
    2: "weekday",
    3: "weekday",
    4: "weekday",
    5: "friday",
    6: "weekend",
}

_EPS = 1e-9


def model_weekday(day: date) -> int:
    """0=Sunday…6=Saturday, as `branch_weekly_hours` numbers weekdays."""
    return (day.weekday() + 1) % 7


def day_class(day: date) -> str:
    return _DAY_CLASS[model_weekday(day)]


def _hhmm(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


# ─── Inputs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BranchCalendar:
    """When a branch trades. Times are minutes from local midnight of the
    business date; a close past midnight runs past 1440."""

    weekly: dict[int, tuple[str, str]] | None
    closed_dates: frozenset[date] = frozenset()
    day_start_hour: int = 4

    def window(self, day: date) -> tuple[int, int] | None:
        if day in self.closed_dates:
            return None
        if self.weekly is None:
            # No schedule entered: the trading-hours engine reads that as open.
            start = self.day_start_hour * 60
            return start, start + 1440
        shift = self.weekly.get(model_weekday(day))
        if shift is None:
            return None
        opens, closes = _hhmm(shift[0]), _hhmm(shift[1])
        if closes <= opens:
            closes += 1440
        return opens, closes

    def business_position(self, moment: datetime) -> tuple[date, float]:
        """`moment` (local, naive) as (business date, minutes from its midnight)."""
        business_date = (moment - timedelta(hours=self.day_start_hour)).date()
        minutes = (
            moment - datetime.combine(business_date, time())
        ).total_seconds() / 60
        return business_date, minutes

    def moment(self, day: date, minutes: float) -> datetime:
        return datetime.combine(day, time()) + timedelta(minutes=minutes)

    def hour_range(self, hour: int) -> tuple[int, int]:
        """Clock hour `hour` as a minute range of the business day it falls in."""
        start = hour * 60 if hour >= self.day_start_hour else (hour + 24) * 60
        return start, start + 60

    def next_open_date(self, after: date, *, horizon: int = 14) -> date | None:
        for offset in range(1, horizon + 1):
            day = after + timedelta(days=offset)
            if self.window(day) is not None:
                return day
        return None


@dataclass(frozen=True)
class BranchInfo:
    id: uuid.UUID
    name: str
    calendar: BranchCalendar


@dataclass(frozen=True)
class ItemInfo:
    id: uuid.UUID
    name: str
    category_id: uuid.UUID | None
    storage_unit: str
    producible: bool
    #: Recipe basis for production: 'batch' rounds to whole batches of `batch_yield`.
    basis: str = "unit"
    batch_yield: float | None = None
    shelf_life_days: int = 14


@dataclass(frozen=True)
class Fact:
    branch_id: uuid.UUID
    item_id: uuid.UUID
    business_date: date
    sales_units: float
    hourly_units: tuple[float, ...]
    hourly_open_minutes: tuple[int, ...]
    #: None when the branch's stock was not yet known that day.
    hourly_in_stock_minutes: tuple[int, ...] | None

    @property
    def open_minutes(self) -> int:
        return sum(self.hourly_open_minutes)

    @property
    def fully_in_stock(self) -> bool:
        if self.hourly_in_stock_minutes is None:
            return True
        return all(
            s >= o
            for s, o in zip(self.hourly_in_stock_minutes, self.hourly_open_minutes)
        )


@dataclass(frozen=True)
class Settings:
    bucket_hours: int = 3
    service_level: float = 0.9
    pool_weight: float = 1.25
    same_day_ready_time: time = time(18, 0)
    next_day_category_ids: frozenset[uuid.UUID] = frozenset()
    half_life_days: int = 14
    window_days: int = 56


@dataclass(frozen=True)
class Snapshot:
    """Everything the forecast reads, as of `now` (local, naive)."""

    business_date: date
    now: datetime
    pool_branch_id: uuid.UUID
    branches: dict[uuid.UUID, BranchInfo]
    items: dict[uuid.UUID, ItemInfo]
    facts: list[Fact]
    #: (branch, item) → on hand now, storage units.
    on_hand: dict[tuple[uuid.UUID, uuid.UUID], float]
    #: (branch, item) → the most one sale of anything sold there draws.
    floors: dict[tuple[uuid.UUID, uuid.UUID], float]
    settings: Settings
    #: Whether the pool branch produces at all (branch inventory settings).
    pool_produces: bool = True
    #: (branch, item) → the least one sale of anything sold there draws. The
    #: floor never drops below it, so the smallest option stays sellable.
    min_floors: dict[tuple[uuid.UUID, uuid.UUID], float] = field(default_factory=dict)


# ─── Distributions ────────────────────────────────────────────────────────────


class Demand:
    """Count demand with a mean and a variance: Poisson when the variance does
    not exceed the mean, negative binomial above it (moment-matched)."""

    def __init__(self, mean: float, var: float) -> None:
        self.mean = max(mean, 0.0)
        self.var = max(var, self.mean)
        self._cdf: list[float] | None = None

    def _build(self) -> list[float]:
        m, v = self.mean, self.var
        if m <= _EPS:
            return [1.0]
        cap = int(m + 12 * math.sqrt(v) + 30)
        cdf: list[float] = []
        total = 0.0
        if v <= m * (1 + 1e-6):
            if m > 600:  # e^-m underflows; a normal is exact enough here
                return self._normal_cdf(cap)
            p = math.exp(-m)
            for k in range(cap + 1):
                total += p
                cdf.append(min(total, 1.0))
                p *= m / (k + 1)
            return cdf
        r = m * m / (v - m)
        q = r / (r + m)
        log_p0 = r * math.log(q)
        if log_p0 < -700:
            return self._normal_cdf(cap)
        p = math.exp(log_p0)
        for k in range(cap + 1):
            total += p
            cdf.append(min(total, 1.0))
            p *= (k + r) / (k + 1) * (1 - q)
        return cdf

    def _normal_cdf(self, cap: int) -> list[float]:
        sd = math.sqrt(self.var) or 1.0
        return [
            0.5 * (1 + math.erf((k + 0.5 - self.mean) / (sd * math.sqrt(2))))
            for k in range(cap + 1)
        ]

    @property
    def cdf(self) -> list[float]:
        if self._cdf is None:
            self._cdf = self._build()
        return self._cdf

    def quantile(self, level: float) -> int:
        for k, value in enumerate(self.cdf):
            if value >= level - _EPS:
                return k
        return len(self.cdf) - 1

    def prob_at_least(self, k: int) -> float:
        """P(demand ≥ k)."""
        if k <= 0:
            return 1.0
        cdf = self.cdf
        if k - 1 >= len(cdf):
            return 0.0
        return max(0.0, 1.0 - cdf[k - 1])


def _ceil(value: float) -> int:
    return int(math.ceil(value - 1e-6)) if value > 0 else 0


# ─── Intraday profile ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DayProfile:
    """One business day's demand shape: buckets as (start, end, share) in
    minutes from the business date's midnight, shares summing to 1."""

    buckets: tuple[tuple[int, int, float], ...]

    def share_between(self, start: float, end: float) -> float:
        if end <= start:
            return 0.0
        total = 0.0
        for b_start, b_end, share in self.buckets:
            overlap = min(end, b_end) - max(start, b_start)
            if overlap > 0:
                total += share * overlap / (b_end - b_start)
        return total


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


class ProfileModel:
    """Hourly sales weights per (branch, day class), pooled over items, from days
    with no known stock-out; bucketed per day on demand."""

    def __init__(
        self,
        facts: Iterable[Fact],
        branches: dict[uuid.UUID, BranchInfo],
        bucket_hours: int,
    ) -> None:
        self.branches = branches
        self.bucket_hours = bucket_hours
        self.branch_hourly: dict[tuple[uuid.UUID, str], list[float]] = defaultdict(
            lambda: [0.0] * 24
        )
        self.network_hourly: dict[str, list[float]] = defaultdict(lambda: [0.0] * 24)
        for fact in facts:
            if not fact.fully_in_stock or fact.sales_units <= 0:
                continue
            cls = day_class(fact.business_date)
            branch_row = self.branch_hourly[(fact.branch_id, cls)]
            network_row = self.network_hourly[cls]
            for hour, units in enumerate(fact.hourly_units):
                if units:
                    branch_row[hour] += units
                    network_row[hour] += units
        self._cache: dict[tuple[uuid.UUID, date], DayProfile | None] = {}

    def buckets_for(
        self, branch_id: uuid.UUID, day: date
    ) -> list[tuple[int, int]] | None:
        window = self.branches[branch_id].calendar.window(day)
        if window is None:
            return None
        opens, closes = window
        width = self.bucket_hours * 60
        out = []
        start = opens
        while start < closes:
            out.append((start, min(start + width, closes)))
            start += width
        return out

    def day(self, branch_id: uuid.UUID, day: date) -> DayProfile | None:
        key = (branch_id, day)
        if key in self._cache:
            return self._cache[key]
        buckets = self.buckets_for(branch_id, day)
        if buckets is None:
            self._cache[key] = None
            return None
        calendar = self.branches[branch_id].calendar
        cls = day_class(day)
        own = self.branch_hourly.get((branch_id, cls), [0.0] * 24)
        net = self.network_hourly.get(cls, [0.0] * 24)

        def bucketed(hourly: list[float]) -> list[float]:
            sums = []
            for bucket in buckets:
                total = 0.0
                for hour in range(24):
                    if hourly[hour]:
                        total += (
                            hourly[hour]
                            * _overlap(calendar.hour_range(hour), bucket)
                            / 60
                        )
                sums.append(total)
            return sums

        own_b, net_b = bucketed(own), bucketed(net)
        minutes = [end - start for start, end in buckets]
        net_total = sum(net_b)
        net_share = (
            [value / net_total for value in net_b]
            if net_total > 0
            else [m / sum(minutes) for m in minutes]
        )
        own_total = sum(own_b)
        shares = [
            (own_b[k] + PROFILE_PRIOR_UNITS * net_share[k])
            / (own_total + PROFILE_PRIOR_UNITS)
            for k in range(len(buckets))
        ]
        norm = sum(shares) or 1.0
        profile = DayProfile(
            tuple((s, e, share / norm) for (s, e), share in zip(buckets, shares))
        )
        self._cache[key] = profile
        return profile

    def in_stock_share(self, fact: Fact) -> float | None:
        """Share of the day's expected demand that fell in the hours the item was
        in stock. None when stock was unknown or the branch was closed."""
        if fact.hourly_in_stock_minutes is None:
            return None
        profile = self.day(fact.branch_id, fact.business_date)
        if profile is None:
            return None
        calendar = self.branches[fact.branch_id].calendar
        share = 0.0
        for start, end, weight in profile.buckets:
            open_k = in_k = 0.0
            for hour in range(24):
                open_h = fact.hourly_open_minutes[hour]
                if not open_h:
                    continue
                portion = _overlap(calendar.hour_range(hour), (start, end)) / 60
                if portion <= 0:
                    continue
                open_k += open_h * portion
                in_k += min(fact.hourly_in_stock_minutes[hour], open_h) * portion
            share += weight * (in_k / open_k if open_k > 0 else 1.0)
        return min(max(share, 0.0), 1.0)


@dataclass(frozen=True)
class DemandEstimate:
    value: float | None
    weight: float
    in_stock_share: float | None
    censored: bool


def estimate_demand(fact: Fact, profiles: ProfileModel) -> DemandEstimate:
    """A day's demand, scaling up for the hours it was out of stock."""
    share = profiles.in_stock_share(fact)
    if share is None:
        return DemandEstimate(fact.sales_units, UNKNOWN_STOCK_WEIGHT, None, False)
    if share >= 1 - 1e-6:
        return DemandEstimate(fact.sales_units, 1.0, 1.0, False)
    if share < MIN_IN_STOCK_SHARE:
        return DemandEstimate(None, 0.0, share, True)
    value = min(fact.sales_units / share, fact.sales_units * MAX_UPLIFT)
    return DemandEstimate(value, 1.0, share, True)


# ─── Daily forecast ───────────────────────────────────────────────────────────


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _payday(day: date) -> bool:
    return day.day >= 25 or day.day <= 3


def _pay_cycle(day: date) -> tuple[int, int]:
    """The (year, month) whose salary a payday-window date belongs to."""
    if day.day >= 25:
        return day.year, day.month
    first = day.replace(day=1) - timedelta(days=1)
    return first.year, first.month


@dataclass
class ItemModel:
    level: float
    n_obs: int
    cold_start: bool
    dispersion: float


@dataclass
class BranchModel:
    dow: dict[int, float]
    dom_payday: float
    dom_rest: float
    dom_applied: bool
    dom_measured: float | None
    dom_cycles: int


class ForecastModel:
    """Levels, seasonal indices and dispersions learnt from the facts before the
    snapshot date."""

    def __init__(self, snapshot: Snapshot, profiles: ProfileModel) -> None:
        self.snapshot = snapshot
        self.profiles = profiles
        settings = snapshot.settings
        end = snapshot.business_date
        start = end - timedelta(days=settings.window_days)

        # Where each branch's and item's history begins: the first day it sold
        # anything. Zeros before a product existed are not demand.
        branch_first: dict[uuid.UUID, date] = {}
        item_first: dict[uuid.UUID, date] = {}
        for fact in snapshot.facts:
            if fact.sales_units > 0:
                if (
                    fact.branch_id not in branch_first
                    or fact.business_date < branch_first[fact.branch_id]
                ):
                    branch_first[fact.branch_id] = fact.business_date
                if (
                    fact.item_id not in item_first
                    or fact.business_date < item_first[fact.item_id]
                ):
                    item_first[fact.item_id] = fact.business_date

        # A branch's trading regime starts once its volume is representative.
        for branch_id, start_day in self._regime_starts(snapshot.facts, end).items():
            branch_first[branch_id] = max(
                branch_first.get(branch_id, start_day), start_day
            )

        # Observations: (branch, item) → [(date, demand, weight)].
        self.observations: dict[
            tuple[uuid.UUID, uuid.UUID], list[tuple[date, float, float]]
        ] = defaultdict(list)
        self.estimates: dict[tuple[uuid.UUID, uuid.UUID, date], DemandEstimate] = {}
        branch_daily: dict[uuid.UUID, dict[date, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for fact in snapshot.facts:
            if not (start <= fact.business_date < end):
                continue
            if fact.branch_id not in snapshot.branches:
                continue
            if fact.open_minutes <= 0:
                continue  # closed that day: a true zero, not a stock-out
            first = max(
                branch_first.get(fact.branch_id, date.max),
                item_first.get(fact.item_id, date.max),
            )
            if fact.business_date < first:
                continue
            est = estimate_demand(fact, profiles)
            self.estimates[(fact.branch_id, fact.item_id, fact.business_date)] = est
            if est.value is None:
                continue
            self.observations[(fact.branch_id, fact.item_id)].append(
                (fact.business_date, est.value, est.weight)
            )
            branch_daily[fact.branch_id][fact.business_date] += est.value

        self.branch_models = {
            branch_id: self._branch_model(branch_daily.get(branch_id, {}))
            for branch_id in snapshot.branches
        }
        self.item_models: dict[tuple[uuid.UUID, uuid.UUID], ItemModel] = {}
        for branch_id in snapshot.branches:
            for item_id in snapshot.items:
                self.item_models[(branch_id, item_id)] = self._level(branch_id, item_id)
        self._cold_start()
        self._dispersion()

    @staticmethod
    def _regime_starts(facts: Iterable[Fact], end: date) -> dict[uuid.UUID, date]:
        """Per branch, the first day its trailing 7-day sales reached
        `REGIME_SHARE` of its daily rate over the last 28 days."""
        daily: dict[uuid.UUID, dict[date, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for fact in facts:
            if fact.business_date < end:
                daily[fact.branch_id][fact.business_date] += fact.sales_units
        out: dict[uuid.UUID, date] = {}
        for branch_id, by_day in daily.items():
            days = sorted(by_day)
            recent = [by_day[d] for d in days if (end - d).days <= 28]
            rate = sum(recent) / 28 if recent else 0.0
            if rate <= 0:
                continue
            for day in days:
                week = (
                    sum(by_day.get(day - timedelta(days=k), 0.0) for k in range(7)) / 7
                )
                if week >= REGIME_SHARE * rate:
                    out[branch_id] = day
                    break
        return out

    # Seasonal indices -------------------------------------------------------

    def _branch_model(self, daily: dict[date, float]) -> BranchModel:
        if not daily:
            return BranchModel({w: 1.0 for w in range(7)}, 1.0, 1.0, False, None, 0)
        overall = sum(daily.values()) / len(daily)
        by_weekday: dict[int, list[float]] = defaultdict(list)
        for day, total in daily.items():
            by_weekday[model_weekday(day)].append(total)
        dow: dict[int, float] = {}
        for weekday in range(7):
            values = by_weekday.get(weekday, [])
            if not values or overall <= 0:
                dow[weekday] = 1.0
                continue
            raw = (sum(values) / len(values)) / overall
            n = len(values)
            dow[weekday] = (n * raw + DOW_SHRINK_WEEKS) / (n + DOW_SHRINK_WEEKS)
        present = [dow[w] for w in by_weekday]
        mean_idx = sum(present) / len(present) if present else 1.0
        if mean_idx > 0:
            dow = {w: v / mean_idx for w, v in dow.items()}

        # The payday effect, on weekday-adjusted totals.
        payday, rest = [], []
        cycles: set[tuple[int, int]] = set()
        for day, total in daily.items():
            adjusted = total / (dow[model_weekday(day)] or 1.0)
            if _payday(day):
                payday.append(adjusted)
                cycles.add(_pay_cycle(day))
            else:
                rest.append(adjusted)
        measured = None
        if payday and rest and sum(rest) > 0:
            measured = (sum(payday) / len(payday)) / (sum(rest) / len(rest))
        applied = measured is not None and len(cycles) >= DOM_MIN_CYCLES
        dom_payday = dom_rest = 1.0
        if applied and measured is not None:
            n = len(cycles)
            ratio = (n * measured + DOW_SHRINK_WEEKS) / (n + DOW_SHRINK_WEEKS)
            share = len(payday) / (len(payday) + len(rest))
            # Keep the average multiplier at 1 across the month.
            dom_rest = 1.0 / (share * ratio + (1 - share))
            dom_payday = ratio * dom_rest
        return BranchModel(dow, dom_payday, dom_rest, applied, measured, len(cycles))

    def seasonal(self, branch_id: uuid.UUID, day: date) -> float:
        model = self.branch_models[branch_id]
        dom = model.dom_payday if _payday(day) else model.dom_rest
        return model.dow.get(model_weekday(day), 1.0) * dom

    # Levels -----------------------------------------------------------------

    def _level(self, branch_id: uuid.UUID, item_id: uuid.UUID) -> ItemModel:
        obs = self.observations.get((branch_id, item_id), [])
        if not obs:
            return ItemModel(0.0, 0, True, DEFAULT_DISPERSION)
        end = self.snapshot.business_date
        half_life = self.snapshot.settings.half_life_days
        adjusted = [
            (day, value / (self.seasonal(branch_id, day) or 1.0), weight)
            for day, value, weight in obs
        ]
        values = [v for _, v, _ in adjusted]
        med = _median(values)
        mad = _median([abs(v - med) for v in values]) * 1.4826
        scale = mad if mad > 0 else max(1.0, sum(values) / len(values))
        hi = med + WINSOR_SDS * scale
        num = den = 0.0
        for day, value, weight in adjusted:
            w = weight * 0.5 ** ((end - day).days / half_life)
            num += w * min(max(value, 0.0), hi)
            den += w
        level = num / den if den > 0 else 0.0
        return ItemModel(
            level, len(obs), len(obs) < COLD_START_DAYS, DEFAULT_DISPERSION
        )

    def _cold_start(self) -> None:
        """Blend a young item's level with its category's typical level here."""
        by_category: dict[tuple[uuid.UUID, uuid.UUID | None], list[float]] = (
            defaultdict(list)
        )
        for (branch_id, item_id), model in self.item_models.items():
            if not model.cold_start:
                by_category[
                    (branch_id, self.snapshot.items[item_id].category_id)
                ].append(model.level)
        for (branch_id, item_id), model in self.item_models.items():
            if not model.cold_start:
                continue
            peers = by_category.get(
                (branch_id, self.snapshot.items[item_id].category_id)
            )
            if not peers:
                continue
            prior = sum(peers) / len(peers)
            n = model.n_obs
            model.level = (n * model.level + COLD_START_DAYS * prior) / (
                n + COLD_START_DAYS
            )

    def _dispersion(self) -> None:
        """Negative-binomial dispersion per (branch, category) from residuals."""
        excess: dict[tuple[uuid.UUID, uuid.UUID | None], float] = defaultdict(float)
        scale: dict[tuple[uuid.UUID, uuid.UUID | None], float] = defaultdict(float)
        for (branch_id, item_id), obs in self.observations.items():
            model = self.item_models.get((branch_id, item_id))
            if model is None:
                continue
            key = (branch_id, self.snapshot.items[item_id].category_id)
            for day, value, _ in obs:
                mu = model.level * self.seasonal(branch_id, day)
                excess[key] += (value - mu) ** 2 - mu
                scale[key] += mu * mu
        for (branch_id, item_id), model in self.item_models.items():
            key = (branch_id, self.snapshot.items[item_id].category_id)
            if scale.get(key, 0) > 1.0:
                model.dispersion = min(
                    max(excess[key] / scale[key], 0.0), MAX_DISPERSION
                )

    # Reading ----------------------------------------------------------------

    def day_mean(self, branch_id: uuid.UUID, item_id: uuid.UUID, day: date) -> float:
        if self.snapshot.branches[branch_id].calendar.window(day) is None:
            return 0.0
        return self.item_models[(branch_id, item_id)].level * self.seasonal(
            branch_id, day
        )

    def window_demand(
        self, branch_id: uuid.UUID, item_id: uuid.UUID, start: datetime, end: datetime
    ) -> Demand:
        """Demand at a branch between two local moments, following the intraday
        profile across as many business days as the window spans."""
        mean = var = 0.0
        if end > start:
            calendar = self.snapshot.branches[branch_id].calendar
            alpha = self.item_models[(branch_id, item_id)].dispersion
            first, t0 = calendar.business_position(start)
            last, t1 = calendar.business_position(end)
            day = first
            while day <= last:
                profile = self.profiles.day(branch_id, day)
                if profile is not None:
                    lo = t0 if day == first else 0.0
                    hi = t1 if day == last else 3000.0
                    part = self.day_mean(
                        branch_id, item_id, day
                    ) * profile.share_between(lo, hi)
                    mean += part
                    var += part + alpha * part * part
                day += timedelta(days=1)
        return Demand(mean, var)


# ─── Allocation & production ──────────────────────────────────────────────────


@dataclass
class BranchLine:
    """One branch's side of an item's forecast."""

    branch_id: uuid.UUID
    kind: str  # 'transfer' (destination) | 'retain' (the pool branch)
    qty: int
    day_demand_mean: float
    window_start: datetime | None
    window_end: datetime | None
    window_mean: float
    window_quantile: int
    floor: float
    on_hand: float
    target: int
    need: int
    shortfall: int = 0
    tier: int | None = None
    #: Internal: the window's demand distribution, for the marginal allocation.
    demand: Demand | None = field(default=None, repr=False)


@dataclass
class ProductionForecast:
    units: float
    batches: int | None
    raw_units: float
    protection_mean: float
    protection_quantile: int
    floors: float
    usable_stock: float
    capped_by_shelf_life: bool
    window_start: datetime | None
    window_end: datetime | None
    #: Every branch's forecast for tomorrow, the day this batch protects.
    next_day_demand_mean: float = 0.0


@dataclass
class ItemForecast:
    item_id: uuid.UUID
    pool_on_hand: float
    lines: list[BranchLine]
    production: ProductionForecast | None
    explain: dict


def _largest_remainder(
    requests: dict[uuid.UUID, int], budget: int
) -> dict[uuid.UUID, int]:
    """Split `budget` units across `requests` in proportion, in whole units."""
    total = sum(requests.values())
    if total <= budget:
        return dict(requests)
    shares = {k: budget * v / total for k, v in requests.items()}
    out = {k: int(math.floor(v)) for k, v in shares.items()}
    left = budget - sum(out.values())
    for k in sorted(
        shares, key=lambda k: (shares[k] - out[k], requests[k]), reverse=True
    ):
        if left <= 0:
            break
        if out[k] < requests[k]:
            out[k] += 1
            left -= 1
    return out


def allocate(
    pool_units: int,
    pool: BranchLine,
    destinations: list[BranchLine],
    *,
    pool_weight: float,
) -> None:
    """Share the morning pool between the pool branch (what it keeps) and the
    destinations (what they are sent). Mutates `qty`, `tier` and `shortfall`."""
    pool.qty = 0
    for line in destinations:
        line.qty = 0
    caps = {pool.branch_id: pool.target, **{d.branch_id: d.need for d in destinations}}
    total_need = sum(caps.values())
    if pool_units >= total_need:
        for line in destinations:
            line.qty = line.need
            line.tier = 0 if line.need else None
        pool.qty = pool_units - sum(d.need for d in destinations)
        pool.tier = 0
        return

    lines = {pool.branch_id: pool, **{d.branch_id: d for d in destinations}}
    left = pool_units

    def give(branch_id: uuid.UUID, units: int, tier: int) -> None:
        nonlocal left
        if units <= 0:
            return
        line = lines[branch_id]
        line.qty += units
        line.tier = tier
        left -= units

    # Tier 1 — the pool branch's own expected demand until its production lands.
    give(pool.branch_id, min(left, _ceil(pool.window_mean), caps[pool.branch_id]), 1)

    # Tier 2 — one of each everywhere: every open branch's availability floor.
    floor_requests: dict[uuid.UUID, int] = {}
    if pool.qty < caps[pool.branch_id]:
        floor_requests[pool.branch_id] = min(
            caps[pool.branch_id] - pool.qty, _ceil(pool.floor)
        )
    for line in destinations:
        if line.window_start is None:
            continue
        floor_requests[line.branch_id] = min(
            line.need, _ceil(max(0.0, line.floor - max(line.on_hand, 0.0)))
        )
    floor_requests = {k: v for k, v in floor_requests.items() if v > 0}
    for branch_id, units in _largest_remainder(floor_requests, left).items():
        give(branch_id, units, 2)

    # Tier 3 — unit by unit to the biggest gain in expected sales.
    def value(line: BranchLine) -> float:
        if line.qty >= caps[line.branch_id] or line.demand is None:
            return -1.0
        held = line.qty if line is pool else max(line.on_hand, 0.0) + line.qty
        free = int(math.floor(held - line.floor + 1e-6))
        weight = pool_weight if line is pool else 1.0
        if free < 0:
            return weight * 2.0  # still under its floor
        return weight * line.demand.prob_at_least(free + 1)

    while left > 0:
        best = max(lines.values(), key=lambda line: (value(line), line.day_demand_mean))
        if value(best) < 0:
            break
        give(best.branch_id, 1, 3)

    for branch_id, line in lines.items():
        line.shortfall = max(0, caps[branch_id] - line.qty)


def forecast(snapshot: Snapshot) -> list[ItemForecast]:
    """The forecast for every produced good in the snapshot."""
    profiles = ProfileModel(
        snapshot.facts, snapshot.branches, snapshot.settings.bucket_hours
    )
    model = ForecastModel(snapshot, profiles)
    return [forecast_item(snapshot, model, item_id) for item_id in snapshot.items]


def _ready_moment(
    snapshot: Snapshot, pool_cal: BranchCalendar, day: date, next_day: bool
) -> datetime | None:
    """When production made on `day` becomes sellable at the pool branch."""
    if not next_day:
        return datetime.combine(day, snapshot.settings.same_day_ready_time)
    following = pool_cal.next_open_date(day)
    if following is None:
        return None
    window = pool_cal.window(following)
    return pool_cal.moment(following, window[0]) if window else None


def forecast_item(
    snapshot: Snapshot, model: ForecastModel, item_id: uuid.UUID
) -> ItemForecast:
    settings = snapshot.settings
    item = snapshot.items[item_id]
    today = snapshot.business_date
    now = snapshot.now
    pool_id = snapshot.pool_branch_id
    pool_cal = snapshot.branches[pool_id].calendar
    next_day = item.category_id in settings.next_day_category_ids
    level = settings.service_level

    def on_hand(branch_id: uuid.UUID) -> float:
        return snapshot.on_hand.get((branch_id, item_id), 0.0)

    def floor(branch_id: uuid.UUID) -> float:
        """Enough for the biggest single sale — but no more than the branch can
        sell within the item's shelf life, and never less than the smallest
        single sale. A box of 9 is not worth holding where 1 sells a month."""
        top = snapshot.floors.get((branch_id, item_id), 0.0)
        if top <= 0:
            return 0.0
        low = min(snapshot.min_floors.get((branch_id, item_id), 1.0), top)
        sells = model.item_models[(branch_id, item_id)].level * item.shelf_life_days
        return min(top, max(low, float(_ceil(sells))))

    def close_of(branch_id: uuid.UUID, day: date) -> datetime | None:
        cal = snapshot.branches[branch_id].calendar
        window = cal.window(day)
        return cal.moment(day, window[1]) if window else None

    def open_of(branch_id: uuid.UUID, day: date) -> datetime | None:
        cal = snapshot.branches[branch_id].calendar
        window = cal.window(day)
        return cal.moment(day, window[0]) if window else None

    # The pool branch keeps enough to carry it until today's production lands.
    ready_today = _ready_moment(snapshot, pool_cal, today, next_day)
    pool_close = close_of(pool_id, today)
    pool_end = pool_close if next_day else ready_today
    if pool_end is not None and pool_close is not None:
        pool_end = min(pool_end, pool_close)
    pool_start = now
    pool_demand = (
        model.window_demand(pool_id, item_id, pool_start, pool_end)
        if pool_end is not None and pool_end > pool_start
        else Demand(0, 0)
    )
    pool_floor = floor(pool_id)
    pool_q = pool_demand.quantile(level)
    pool_line = BranchLine(
        branch_id=pool_id,
        kind="retain",
        qty=0,
        day_demand_mean=model.day_mean(pool_id, item_id, today),
        window_start=pool_start if pool_end and pool_end > pool_start else None,
        window_end=pool_end if pool_end and pool_end > pool_start else None,
        window_mean=pool_demand.mean,
        window_quantile=pool_q,
        floor=pool_floor,
        on_hand=on_hand(pool_id),
        target=_ceil(pool_q + pool_floor),
        need=_ceil(pool_q + pool_floor),
        demand=pool_demand,
    )

    # Each destination: the rest of its trading day today (the next transfer
    # arrives before it opens on its next open day).
    destinations: list[BranchLine] = []
    for branch_id in snapshot.branches:
        if branch_id == pool_id:
            continue
        opens, closes = open_of(branch_id, today), close_of(branch_id, today)
        held = on_hand(branch_id)
        if opens is None or closes is None or closes <= now:
            destinations.append(
                BranchLine(
                    branch_id,
                    "transfer",
                    0,
                    0.0,
                    None,
                    None,
                    0.0,
                    0,
                    floor(branch_id),
                    held,
                    0,
                    0,
                )
            )
            continue
        start = max(now, opens)
        demand = model.window_demand(branch_id, item_id, start, closes)
        q = demand.quantile(level)
        target = _ceil(q + floor(branch_id))
        destinations.append(
            BranchLine(
                branch_id=branch_id,
                kind="transfer",
                qty=0,
                day_demand_mean=model.day_mean(branch_id, item_id, today),
                window_start=start,
                window_end=closes,
                window_mean=demand.mean,
                window_quantile=q,
                floor=floor(branch_id),
                on_hand=held,
                target=target,
                need=max(0, _ceil(target - max(held, 0.0))),
                demand=demand,
            )
        )

    pool_units = int(math.floor(max(on_hand(pool_id), 0.0) + 1e-6))
    allocate(pool_units, pool_line, destinations, pool_weight=settings.pool_weight)

    production = None
    if item.producible and snapshot.pool_produces and ready_today is not None:
        production = _production(
            snapshot, model, item, pool_line, destinations, ready_today, next_day
        )

    item_model = model.item_models
    explain = {
        "next_day_category": next_day,
        "levels": {
            str(branch_id): {
                "level": round(item_model[(branch_id, item_id)].level, 3),
                "n_obs": item_model[(branch_id, item_id)].n_obs,
                "cold_start": item_model[(branch_id, item_id)].cold_start,
                "dispersion": round(item_model[(branch_id, item_id)].dispersion, 3),
                "seasonal_today": round(model.seasonal(branch_id, today), 3),
            }
            for branch_id in snapshot.branches
        },
    }
    return ItemForecast(
        item_id=item_id,
        pool_on_hand=on_hand(pool_id),
        lines=[pool_line, *destinations],
        production=production,
        explain=explain,
    )


def _production(
    snapshot: Snapshot,
    model: ForecastModel,
    item: ItemInfo,
    pool: BranchLine,
    destinations: list[BranchLine],
    ready_today: datetime,
    next_day: bool,
) -> ProductionForecast:
    today = snapshot.business_date
    pool_id = snapshot.pool_branch_id
    pool_cal = snapshot.branches[pool_id].calendar
    level = snapshot.settings.service_level
    item_id = item.id

    # Production happens every day; tomorrow's batch lands at its ready moment.
    ready_next = _ready_moment(snapshot, pool_cal, today + timedelta(days=1), next_day)
    if ready_next is None or ready_next <= ready_today:
        ready_next = ready_today + timedelta(days=1)

    mean = var = 0.0
    floors = pool.floor
    protect = model.window_demand(pool_id, item_id, ready_today, ready_next)
    mean += protect.mean
    var += protect.var

    # What the pool branch will still hold when this batch lands: what it keeps
    # this morning, less what it sells until then.
    until_ready = model.window_demand(pool_id, item_id, snapshot.now, ready_today)
    usable = max(0.0, pool.qty - until_ready.mean)

    # Tomorrow's transfers come out of this batch, for every destination open
    # tomorrow; what a destination still holds tonight counts toward its own
    # tomorrow only — surplus at a small branch cannot serve anyone else.
    tomorrow = today + timedelta(days=1)
    for line in destinations:
        cal = snapshot.branches[line.branch_id].calendar
        window = cal.window(tomorrow)
        if window is None:
            continue
        demand = model.window_demand(
            line.branch_id,
            item_id,
            cal.moment(tomorrow, window[0]),
            cal.moment(tomorrow, window[1]),
        )
        mean += demand.mean
        var += demand.var
        floors += line.floor
        leftover = max(0.0, max(line.on_hand, 0.0) + line.qty - line.window_mean)
        usable += min(leftover, demand.mean + line.floor)

    total = Demand(mean, var)
    q = total.quantile(level)
    raw = max(0.0, q + floors - usable)

    # Never plan more than the item can sell before it expires.
    capped = False
    daily = sum(
        model.day_mean(branch_id, item_id, tomorrow) for branch_id in snapshot.branches
    )
    if daily > 0:
        limit = item.shelf_life_days * daily - usable
        if raw > limit:
            raw = max(0.0, limit)
            capped = True

    batches = None
    if item.basis == "batch" and item.batch_yield:
        batches = _ceil(raw / item.batch_yield)
        units = batches * item.batch_yield
    else:
        units = float(_ceil(raw))
    return ProductionForecast(
        units=units,
        batches=batches,
        raw_units=raw,
        protection_mean=mean,
        protection_quantile=q,
        floors=floors,
        usable_stock=usable,
        capped_by_shelf_life=capped,
        window_start=ready_today,
        window_end=ready_next,
        next_day_demand_mean=daily,
    )
