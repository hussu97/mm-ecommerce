"""
Golden vectors for the counter pricing engine — the contract the Swift port
(`mm-pos/MMPos/Features/Register/Counter/CounterPricing.swift`) is tested
against.

Usage (from `apps/api`):
    python -m scripts.export_counter_pricing_vectors          # write the fixture
    python -m scripts.export_counter_pricing_vectors --check  # exit 1 if stale

Writes `tests/fixtures/counter_pricing_vectors.json`. Every vector is priced by
`app.services.pos.counter_pricing.price_lines` — the function
`counter_pricing.price_check` wraps and `pos_order_service.recalculate(ctx=…)`
agrees with — so the Swift port that reproduces every `expected` block
reproduces the server. `tests/unit/test_counter_pricing_vectors.py` regenerates
the file in memory and diffs it against the committed copy, so a change to the
engine that is not re-exported (and re-copied into mm-pos) fails CI. mm-pos pins
the fixture's sha256 in its own CI.

The generation is deterministic: a fixed seed, fixed ids, no clock.

═══ File format ═══════════════════════════════════════════════════════════════

Top level:

    {
      "engine_version": 1,                 # counter_pricing.ENGINE_VERSION
      "seed": 20260923,
      "generator": "scripts/export_counter_pricing_vectors.py",
      "count": <int>,
      "vectors": [<vector>, …]
    }

All money is a decimal STRING with exactly 2 dp ("12.50"); rates/fractions are
decimal STRINGS with 4 dp ("0.0500"); input prices may carry up to 2 dp,
weights up to 3 dp. Parse every one as `Decimal`, never `Double`. Compare the
expected outputs as exact strings (they are canonical).

<vector>:

    {
      "name": "hand/half-cent/…" | "random/0042",
      "input": {
        "at": "2026-09-23T10:15:00Z",       # the instant the check is priced at
        "timezone": "Asia/Dubai",           # promotion clock: at → local wall time
        "local_datetime": "2026-09-23T14:15:00+04:00",   # informative only
        "branch_id": "<uuid>",
        "source": "cashier",                # always cashier for the counter
        "order_type": "pickup",
        "rounding_step": "0.25",            # cash rounding of the total; "0" = none
        "vat_registered": true,             # false ⇒ every line's tax rate → 0
        "coupon_id": "<uuid>" | null,       # the selected coupon chip
        "promotions": [<promotion>, …],     # bundle order (best first)
        "lines": [<line>, …]
      },
      "expected": <pricing>
    }

<promotion> — `BundlePromotion` (app/schemas/pos_counter.py), with omitted keys
taking these defaults: trigger "spend", trigger_value "0.00", category_ids [],
branch_ids [], order_types [], is_active true, from_date null, to_date null,
from_time 0, to_time 1439, weekdays [true ×7] (Monday first). Always present:
id, name, reward, reward_value, sources, mode ("auto"|"coupon"), rank.

<line> — omitted keys default: weight null, options_price "0.00",
is_non_revenue false, category_id null, returned_quantity 0, voided false, tax
= NO_TAX {"rate": "0.0000", "name": "No tax", "tax_id": null, "inclusive": true}.

    {
      "id": "L1",
      "quantity": 2,
      "unit_price": "21.00",               # per kilo when weight is set
      "weight": "0.350",
      "options_price": "4.00",             # per unit, already net of free options
      "tax": {"rate": "0.0500", "name": "VAT 5%", "tax_id": "<id>", "inclusive": true},
      "is_non_revenue": false,
      "category_id": "<uuid>",
      "returned_quantity": 0,
      "voided": false
    }

<pricing> — `counter_pricing.pricing_to_wire`:

    {
      "promotion": null | {
        "id", "name", "mode": "auto"|"coupon",
        "scope": "order"|"lines",
        "is_percentage": bool,
        "value": "0.1500" (fraction, 4 dp) | "5.00" (AED),
        "amount": "3.75",                  # what it took off in total
        "line_ids": ["L1", …]              # scope "lines" only
      },
      "lines": [                           # NON-VOIDED lines, input order
        {"id", "base_price", "options_price", "unit_price", "gross", "discount",
         "total_price", "tax_amount", "tax_exclusive_total", "tax_exclusive_unit"}
      ],
      "subtotal", "line_discounts", "order_discount", "discount_total",
      "charges" ("0.00" — the counter has none), "tax_total", "total_excl_tax",
      "rounding", "total",
      "vat_rate",                          # see note (4) below
      "taxes": [{"tax_id", "name", "rate", "taxable_amount", "amount"}]
                                           # sorted by (name, rate)
    }

═══ The algorithm, in the order the Swift port must follow ════════════════════

Every quantisation is to 2 dp ROUND_HALF_UP ("money") unless stated.

1. base_price = money(unit_price × weight) when weight is set, else
   money(unit_price). unit_price_out = base_price + money(options_price).
2. Drop voided lines. billable = max(quantity − returned_quantity, 0).
   spend = Σ (base_price + options_price) × billable over lines with billable>0
   (exact, unrounded).
3. Choose ONE promotion (`promotion_rules.choose`): eligible(p, mode) means
   is_active ∧ reward ∈ {percentage_off_order, fixed_off_order} ∧ trigger ==
   "spend" ∧ sources ≠ [] ∧ source ∈ sources ∧ (branch_ids == [] ∨ branch ∈
   branch_ids) ∧ (order_types == [] ∨ order_type ∈ order_types) ∧ p.mode ==
   mode ∧ in_window(local) ∧ spend ≥ trigger_value. local = at in `timezone`.
   in_window: from_date ≤ local.date ≤ to_date (nulls open); weekdays[local
   weekday, Monday=0]; m = hour×60+minute; from_time ≤ to_time ? from ≤ m ≤ to :
   m ≥ from ∨ m ≤ to. If coupon_id names a promotion eligible as "coupon" it
   wins; else the eligible "auto" promotion with the lowest rank; else none.
4. value = reward_value/100 rounded HALF_UP to 4 dp for percentage_off_order,
   reward_value (AED) for fixed_off_order.
   • category_ids non-empty → targets = billable lines whose category_id is
     listed; if none match there is NO promotion (null). Each target gets a line
     discount: amount_on(gross) where gross = money(unit_price_out × billable).
   • else one order-level discount.
   amount_on(base): base ≤ 0 → 0; raw = base × value (percentage) or
   money(value) (fixed); result = money(min(money(raw), base)).
5. Per line (`pos_pricing._line_totals`): gross = money(unit_price_out ×
   billable); discount = its line discount (0 or the promotion); net =
   money(gross − discount). Non-revenue: tax 0, tax_exclusive_total = net,
   tax_exclusive_unit = 0. Else, with rate = 0 when !vat_registered:
   inclusive → excl = money(net / (1 + rate)) (rate 0 → excl = net), tax =
   money(net − excl); exclusive → excl = net, tax = money(net × rate).
   tax_exclusive_unit = money(excl / billable) (0 when billable is 0).
   These are the per-line outputs (`total_price` = net, `tax_amount` = tax) —
   BEFORE any order-level discount share.
6. Order level (`pos_pricing.calculate_order`): subtotal = Σ gross;
   line_discounts = Σ discount; net_after = Σ net. order_discount =
   amount_on(net_after) when an order-level promotion applies and net_after > 0.
   Spread it over the TAXABLE (non-revenue excluded) lines pro rata to their
   net: share = money(order_discount × net_i / taxable_base) for each, except
   the LAST taxable line (or the last line overall) takes the remainder
   (order_discount − Σ earlier shares). For each line: n = money(net − share);
   non-revenue → adds n to total_excl_tax, no tax; else split n as in (5) and
   add excl to total_excl_tax, tax to tax_total and to the (tax_id, name, rate)
   bucket (a bucket with rate ≤ 0 and amount 0 is not emitted). Running sums
   are money-rounded at each addition.
7. total = money(total_excl_tax + tax_total); rounded = total when step ≤ 0,
   else money(round_half_up(total / step) × step); rounding = rounded − total.
   discount_total = line_discounts + order_discount.
8. vat_rate: no buckets → 0; one bucket → its rate; several →
   (tax_total / total_excl_tax) quantized to 4 dp **HALF_EVEN** (0 when the
   base ≤ 0). Informational.
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

OUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "counter_pricing_vectors.json"
)

SEED = 20260923
RANDOM_COUNT = 1150

TZ = "Asia/Dubai"
BRANCH = "7b1d6a9e-0000-4000-8000-000000000001"
OTHER_BRANCH = "7b1d6a9e-0000-4000-8000-000000000002"
CAT_COOKIES = "c0ffee00-0000-4000-8000-000000000001"
CAT_BROWNIES = "c0ffee00-0000-4000-8000-000000000002"
CAT_DRINKS = "c0ffee00-0000-4000-8000-000000000003"
CATEGORIES = [CAT_COOKIES, CAT_BROWNIES, CAT_DRINKS]

VAT5 = {"rate": "0.0500", "name": "VAT 5%", "tax_id": "tax-vat5", "inclusive": True}
ZERO_RATED = {"rate": "0.0000", "name": "Zero", "tax_id": "tax-zero", "inclusive": True}
EXCL5 = {"rate": "0.0500", "name": "VAT 5% ex", "tax_id": "tax-ex5", "inclusive": False}
FEE7 = {"rate": "0.0700", "name": "Fee 7%", "tax_id": "tax-fee7", "inclusive": True}
COMBO = {"rate": "0.1200", "name": "VAT 5%", "tax_id": "tax-vat5", "inclusive": True}
TAXES = [VAT5, VAT5, VAT5, ZERO_RATED, EXCL5, FEE7, COMBO, None]


def _pid(n: int) -> str:
    return str(uuid.UUID(int=(0xFEED << 112) | n))


def _at(local: str, tz: str = TZ) -> str:
    """A local wall time → the UTC instant string."""
    dt = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _promo(n: int, **fields) -> dict:
    data = {
        "id": _pid(n),
        "name": fields.pop("name", f"Promo {n}"),
        "reward": fields.pop("reward", "percentage_off_order"),
        "reward_value": fields.pop("reward_value", "15.0000"),
        "sources": fields.pop("sources", ["cashier"]),
        "mode": fields.pop("mode", "auto"),
        "rank": fields.pop("rank", 0),
    }
    data.update(fields)
    return data


def _line(n: int, price: str, qty: int = 1, **fields) -> dict:
    data: dict[str, Any] = {"id": f"L{n}", "quantity": qty, "unit_price": price}
    if "tax" not in fields:
        fields["tax"] = VAT5
    if fields["tax"] is None:
        fields.pop("tax")
    data.update(fields)
    return data


def _case(
    name: str,
    lines: list[dict],
    *,
    promotions: list[dict] | None = None,
    at: str = "2026-09-23T12:00:00",
    tz: str = TZ,
    rounding: str = "0",
    vat_registered: bool = True,
    coupon: str | None = None,
) -> dict:
    return {
        "name": name,
        "input": {
            "at": _at(at, tz),
            "timezone": tz,
            "branch_id": BRANCH,
            "source": "cashier",
            "order_type": "pickup",
            "rounding_step": rounding,
            "vat_registered": vat_registered,
            "coupon_id": coupon,
            "promotions": promotions or [],
            "lines": lines,
        },
    }


# ─── Hand-written edge cases ──────────────────────────────────────────────────


def hand_cases() -> list[dict]:
    cases: list[dict] = []
    add = cases.append

    # Half-cent splits: order-level percentages whose pro-rata shares land on
    # .005, and inclusive splits that round both ways.
    price_sets = [
        ["10.01", "10.01", "10.01"],
        ["0.01", "0.01", "0.01"],
        ["3.33", "3.33", "3.34"],
        ["1.05", "2.10", "3.15"],
        ["99.99", "0.01"],
        ["7.77", "7.77", "7.77", "7.77", "7.77", "7.77", "7.77"],
        ["12.34", "0.05", "0.15"],
        ["21.00", "21.00"],
        ["0.10", "0.10", "0.10", "0.10", "0.10", "0.10", "0.10", "0.10", "0.10"],
        ["33.33", "33.33", "33.34"],
    ]
    for i, prices in enumerate(price_sets):
        for pct in ("10.0000", "15.0000", "33.3300", "50.0000", "12.5000"):
            add(
                _case(
                    f"hand/half-cent/{i}-{pct}",
                    [_line(n + 1, p) for n, p in enumerate(prices)],
                    promotions=[_promo(1, reward_value=pct)],
                )
            )

    # Mixed rates: 5% / zero-rated / exclusive / 7% fee, with and without an
    # order-level promotion.
    mixes = [
        [VAT5, ZERO_RATED],
        [VAT5, EXCL5],
        [VAT5, FEE7, ZERO_RATED],
        [EXCL5, EXCL5, ZERO_RATED],
        [COMBO, VAT5],
        [None, VAT5],
    ]
    for i, taxes in enumerate(mixes):
        for promo in (None, "20.0000", "7.5000"):
            add(
                _case(
                    f"hand/mixed-rates/{i}-{promo or 'none'}",
                    [
                        _line(n + 1, p, 1 + n % 2, tax=t)
                        for n, (p, t) in enumerate(
                            zip(["18.90", "7.35", "4.20"], taxes)
                        )
                    ],
                    promotions=[_promo(1, reward_value=promo)] if promo else [],
                )
            )

    # Non-revenue lines are excluded from the discount spread and carry no tax.
    for i, order in enumerate([(0,), (1,), (0, 2), (2,)]):
        lines = []
        for n in range(3):
            lines.append(
                _line(n + 1, ["15.00", "9.50", "0.00"][n], is_non_revenue=n in order)
            )
        for promo in ("10.0000", None):
            add(
                _case(
                    f"hand/non-revenue/{i}-{promo or 'none'}",
                    lines,
                    promotions=[_promo(1, reward_value=promo)] if promo else [],
                )
            )
    add(
        _case(
            "hand/non-revenue/last-line-non-revenue",
            [
                _line(1, "10.00"),
                _line(2, "10.01"),
                _line(3, "5.00", is_non_revenue=True),
            ],
            promotions=[_promo(1, reward_value="33.3300")],
        )
    )

    # Unregistered entity: the same baskets with vat_registered false.
    for i, taxes in enumerate(mixes):
        add(
            _case(
                f"hand/unregistered/{i}",
                [
                    _line(n + 1, p, tax=t)
                    for n, (p, t) in enumerate(zip(["18.90", "7.35", "4.20"], taxes))
                ],
                promotions=[_promo(1, reward_value="15.0000")],
                vat_registered=False,
            )
        )

    # Cash rounding across every step, including totals exactly on a half-step.
    for step in ("0.25", "0.05", "0.50", "1.00", "0.01", "0.10", "0"):
        for price in ("10.12", "10.13", "10.37", "10.38", "10.62", "10.63", "0.12"):
            add(
                _case(
                    f"hand/cash-rounding/{step}-{price}",
                    [_line(1, price), _line(2, "0.01")],
                    rounding=step,
                )
            )

    # Capped: a fixed reward larger than the check / than the line.
    for value in ("5.00", "50.00", "500.00", "0.01"):
        add(
            _case(
                f"hand/capped/order-{value}",
                [_line(1, "12.00"), _line(2, "8.50")],
                promotions=[_promo(1, reward="fixed_off_order", reward_value=value)],
            )
        )
        add(
            _case(
                f"hand/capped/lines-{value}",
                [
                    _line(1, "12.00", 2, category_id=CAT_COOKIES),
                    _line(2, "3.00", category_id=CAT_COOKIES),
                    _line(3, "8.50", category_id=CAT_DRINKS),
                ],
                promotions=[
                    _promo(
                        1,
                        reward="fixed_off_order",
                        reward_value=value,
                        category_ids=[CAT_COOKIES],
                    )
                ],
            )
        )
    add(
        _case(
            "hand/capped/100-percent",
            [_line(1, "12.00"), _line(2, "8.50", tax=EXCL5)],
            promotions=[_promo(1, reward_value="100.0000")],
        )
    )

    # A window crossing midnight, 22:00 → 02:00, at its edges; and a weekday
    # that only covers the calendar day of the sale.
    night = dict(from_time=22 * 60, to_time=2 * 60)
    for local in (
        "2026-09-23T21:59:00",
        "2026-09-23T22:00:00",
        "2026-09-23T23:59:59",
        "2026-09-24T00:00:00",
        "2026-09-24T02:00:00",
        "2026-09-24T02:00:59",
        "2026-09-24T02:01:00",
        "2026-09-24T12:00:00",
    ):
        add(
            _case(
                f"hand/midnight-window/{local}",
                [_line(1, "20.00")],
                promotions=[_promo(1, **night)],
                at=local,
            )
        )
        # Wednesday only (2026-09-23 is a Wednesday).
        add(
            _case(
                f"hand/midnight-window/wed-only/{local}",
                [_line(1, "20.00")],
                promotions=[
                    _promo(
                        1,
                        weekdays=[False, False, True, False, False, False, False],
                        **night,
                    )
                ],
                at=local,
            )
        )

    # The trigger exactly at, and just under, the spend.
    for spend, trigger in (
        ("50.00", "50.00"),
        ("49.99", "50.00"),
        ("50.01", "50.00"),
        ("0.00", "0.00"),
        ("25.00", "50.00"),
    ):
        add(
            _case(
                f"hand/trigger/{spend}-{trigger}",
                [_line(1, spend)],
                promotions=[_promo(1, trigger_value=trigger)],
            )
        )
        # Spend counts options and quantity, not discounts.
        add(
            _case(
                f"hand/trigger/options-qty/{spend}-{trigger}",
                [_line(1, "10.00", 2, options_price="5.00"), _line(2, "0.00")],
                promotions=[_promo(1, trigger_value=trigger)],
            )
        )

    # Coupons: replaces auto; ineligible coupon falls back to auto; an auto id
    # passed as a coupon is ignored; an unknown coupon is ignored.
    auto = _promo(1, name="Counter 15%", reward_value="15.0000", mode="auto", rank=1)
    coupon = _promo(
        2,
        name="Cookies 25%",
        reward_value="25.0000",
        mode="coupon",
        rank=0,
        category_ids=[CAT_COOKIES],
    )
    big_coupon = _promo(
        3,
        name="Spend 100",
        reward="fixed_off_order",
        reward_value="10.00",
        mode="coupon",
        rank=2,
        trigger_value="100.00",
    )
    basket = [
        _line(1, "12.50", 2, category_id=CAT_COOKIES),
        _line(2, "21.00", category_id=CAT_BROWNIES),
    ]
    for label, cid in (
        ("none", None),
        ("coupon", _pid(2)),
        ("ineligible-coupon", _pid(3)),
        ("auto-as-coupon", _pid(1)),
        ("unknown", _pid(99)),
    ):
        add(
            _case(
                f"hand/coupon/{label}",
                basket,
                promotions=[coupon, auto, big_coupon],
                coupon=cid,
            )
        )
    add(
        _case(
            "hand/coupon/eligible-big",
            basket + [_line(3, "80.00")],
            promotions=[coupon, auto, big_coupon],
            coupon=_pid(3),
        )
    )
    add(
        _case(
            "hand/coupon/only-coupons-no-selection",
            basket,
            promotions=[coupon, big_coupon],
        )
    )

    # Rank: lowest wins among eligible autos; an ineligible better one yields.
    add(
        _case(
            "hand/rank/lowest-wins",
            basket,
            promotions=[
                _promo(1, reward_value="10.0000", rank=0),
                _promo(2, reward_value="30.0000", rank=1),
            ],
        )
    )
    add(
        _case(
            "hand/rank/best-ineligible",
            basket,
            promotions=[
                _promo(1, reward_value="30.0000", rank=0, trigger_value="500.00"),
                _promo(2, reward_value="10.0000", rank=1),
            ],
        )
    )

    # Scope: another channel, another branch, delivery only, inactive, an
    # unscoped channel list, a product-level reward.
    for label, fields in (
        ("online-only", {"sources": ["online"]}),
        ("other-branch", {"branch_ids": [OTHER_BRANCH]}),
        ("this-branch", {"branch_ids": [BRANCH]}),
        ("delivery-only", {"order_types": ["delivery"]}),
        ("pickup-only", {"order_types": ["pickup"]}),
        ("inactive", {"is_active": False}),
        ("unscoped", {"sources": []}),
        ("product-reward", {"reward": "percentage_off_products"}),
        ("quantity-trigger", {"trigger": "quantity"}),
    ):
        add(_case(f"hand/scope/{label}", basket, promotions=[_promo(1, **fields)]))

    # Dates in the shop's zone: 21:00Z on the 22nd is 01:00 on the 23rd locally.
    for local, from_date, to_date in (
        ("2026-09-23T01:00:00", "2026-09-23", None),
        ("2026-09-22T23:59:00", "2026-09-23", None),
        ("2026-09-30T23:59:00", None, "2026-09-30"),
        ("2026-10-01T00:00:00", None, "2026-09-30"),
        ("2026-09-23T12:00:00", "2026-09-23", "2026-09-23"),
    ):
        add(
            _case(
                f"hand/dates/{local}-{from_date}-{to_date}",
                [_line(1, "40.00")],
                promotions=[_promo(1, from_date=from_date, to_date=to_date)],
                at=local,
            )
        )
    # A different zone: same instant, different local day.
    add(
        _case(
            "hand/dates/london",
            [_line(1, "40.00")],
            promotions=[_promo(1, from_date="2026-09-23")],
            at="2026-09-22T23:30:00",
            tz="Europe/London",
        )
    )

    # Every weekday flag on its own.
    for day in range(7):
        flags = [d == day for d in range(7)]
        add(
            _case(
                f"hand/weekday/{day}",
                [_line(1, "30.00")],
                promotions=[_promo(1, weekdays=flags)],
                at=f"2026-09-{21 + day:02d}T12:00:00",
            )
        )

    # Weighed products.
    for weight, price in (
        ("0.333", "45.00"),
        ("1.000", "12.99"),
        ("0.005", "10.00"),
        ("2.750", "33.33"),
        ("0.125", "0.04"),
    ):
        add(
            _case(
                f"hand/weight/{weight}-{price}",
                [_line(1, price, weight=weight), _line(2, "5.00")],
                promotions=[_promo(1, reward_value="10.0000")],
            )
        )

    # Returned units and voided lines.
    add(
        _case(
            "hand/returns/partial",
            [_line(1, "10.00", 3, returned_quantity=1), _line(2, "5.00")],
            promotions=[_promo(1)],
        )
    )
    add(
        _case(
            "hand/returns/all-returned",
            [_line(1, "10.00", 2, returned_quantity=2), _line(2, "5.00")],
            promotions=[_promo(1)],
        )
    )
    add(
        _case(
            "hand/voided/one-voided",
            [_line(1, "10.00", voided=True), _line(2, "5.00")],
            promotions=[_promo(1, trigger_value="10.00")],
        )
    )
    add(
        _case(
            "hand/voided/all-voided",
            [_line(1, "10.00", voided=True)],
            promotions=[_promo(1)],
        )
    )

    # Exclusive tax with per-line rounding.
    for price in ("0.10", "0.30", "9.99", "10.01", "123.45"):
        add(
            _case(
                f"hand/exclusive/{price}",
                [_line(1, price, 3, tax=EXCL5)],
                promotions=[_promo(1, reward_value="12.5000")],
                rounding="0.25",
            )
        )

    # A fractional percentage quantized to 4 dp (12.345% → 0.1235).
    for pct in ("12.3450", "0.0050", "33.3333", "66.6667"):
        add(
            _case(
                f"hand/fraction/{pct}",
                [_line(1, "100.00"), _line(2, "0.33")],
                promotions=[_promo(1, reward_value=pct)],
            )
        )
        add(
            _case(
                f"hand/fraction/lines/{pct}",
                [_line(1, "100.00", category_id=CAT_COOKIES), _line(2, "0.33")],
                promotions=[_promo(1, reward_value=pct, category_ids=[CAT_COOKIES])],
            )
        )

    # Category scope with no matching line: no promotion at all.
    add(
        _case(
            "hand/category/no-match",
            [_line(1, "10.00", category_id=CAT_DRINKS)],
            promotions=[_promo(1, category_ids=[CAT_COOKIES])],
        )
    )
    add(
        _case(
            "hand/category/matching-line-zero-priced",
            [_line(1, "0.00", category_id=CAT_COOKIES), _line(2, "10.00")],
            promotions=[_promo(1, category_ids=[CAT_COOKIES])],
        )
    )
    add(
        _case(
            "hand/category/two-categories",
            [
                _line(1, "10.00", category_id=CAT_COOKIES),
                _line(2, "10.00", category_id=CAT_BROWNIES),
                _line(3, "10.00", category_id=CAT_DRINKS),
            ],
            promotions=[_promo(1, category_ids=[CAT_COOKIES, CAT_BROWNIES])],
        )
    )

    # Zero-priced and empty checks.
    add(
        _case(
            "hand/zero/all-zero",
            [_line(1, "0.00"), _line(2, "0.00")],
            promotions=[_promo(1)],
        )
    )
    add(_case("hand/zero/no-lines", [], promotions=[_promo(1)]))
    add(_case("hand/zero/no-tax-line", [_line(1, "10.00", tax=None)]))

    # Large baskets and quantities.
    add(
        _case(
            "hand/large/many-lines",
            [_line(n, f"{(n * 7) % 50 + 0.99:.2f}", n % 4 + 1) for n in range(1, 41)],
            promotions=[_promo(1, reward_value="15.0000")],
            rounding="0.25",
        )
    )
    add(
        _case(
            "hand/large/big-quantity",
            [_line(1, "0.35", 999), _line(2, "12345.67", 3)],
            promotions=[_promo(1, reward="fixed_off_order", reward_value="123.45")],
        )
    )
    return cases


# ─── Seeded random cases ──────────────────────────────────────────────────────


def _money_str(value: float) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"


def random_cases(rng: random.Random, count: int) -> list[dict]:
    cases = []
    base_day = date(2026, 9, 1)
    for index in range(count):
        n_lines = rng.choice([1, 1, 2, 2, 2, 3, 3, 4, 5, 7])
        lines = []
        for n in range(1, n_lines + 1):
            price = rng.choice(
                [
                    rng.randint(0, 5000) / 100,
                    rng.randint(0, 30000) / 100,
                    rng.choice([0.01, 0.05, 0.99, 1.0, 10.0, 21.0, 12.5, 7.35]),
                ]
            )
            fields: dict[str, Any] = {"tax": rng.choice(TAXES)}
            if rng.random() < 0.08:
                fields["weight"] = f"{rng.randint(1, 3000) / 1000:.3f}"
            if rng.random() < 0.25:
                fields["options_price"] = _money_str(rng.randint(0, 1500) / 100)
            if rng.random() < 0.06:
                fields["is_non_revenue"] = True
            if rng.random() < 0.6:
                fields["category_id"] = rng.choice(CATEGORIES)
            qty = rng.choice([1, 1, 1, 2, 2, 3, 4, 6, 12])
            if rng.random() < 0.05:
                fields["returned_quantity"] = rng.randint(0, qty)
            if rng.random() < 0.05:
                fields["voided"] = True
            lines.append(_line(n, _money_str(price), qty, **fields))

        promotions = []
        for p in range(rng.choice([0, 1, 1, 1, 2, 2, 3])):
            reward = rng.choice(["percentage_off_order"] * 3 + ["fixed_off_order"])
            value = (
                f"{rng.choice([5, 10, 12.5, 15, 20, 25, 33.33, 50, 100, rng.randint(1, 9999) / 100]):.4f}"
                if reward == "percentage_off_order"
                else f"{rng.randint(1, 5000) / 100:.2f}"
            )
            fields = {
                "reward": reward,
                "reward_value": value,
                "mode": rng.choice(["auto", "auto", "coupon"]),
                "rank": p,
            }
            if rng.random() < 0.4:
                fields["trigger_value"] = _money_str(rng.randint(0, 20000) / 100)
            if rng.random() < 0.4:
                fields["category_ids"] = rng.sample(CATEGORIES, rng.randint(1, 2))
            if rng.random() < 0.3:
                start = rng.randint(0, 1439)
                end = rng.randint(0, 1439)
                fields["from_time"], fields["to_time"] = start, end
            if rng.random() < 0.2:
                fields["weekdays"] = [rng.random() < 0.7 for _ in range(7)]
            if rng.random() < 0.2:
                fields["from_date"] = (
                    base_day + timedelta(days=rng.randint(0, 40))
                ).isoformat()
            if rng.random() < 0.2:
                fields["to_date"] = (
                    base_day + timedelta(days=rng.randint(0, 40))
                ).isoformat()
            if rng.random() < 0.08:
                fields["sources"] = rng.choice([["online"], ["cashier", "online"], []])
            if rng.random() < 0.06:
                fields["branch_ids"] = [rng.choice([BRANCH, OTHER_BRANCH])]
            if rng.random() < 0.05:
                fields["order_types"] = [rng.choice(["pickup", "delivery"])]
            if rng.random() < 0.04:
                fields["is_active"] = False
            promotions.append(_promo(100 + index * 10 + p, **fields))

        coupon = None
        roll = rng.random()
        coupons = [pr["id"] for pr in promotions if pr["mode"] == "coupon"]
        if coupons and roll < 0.6:
            coupon = rng.choice(coupons)
        elif promotions and roll < 0.7:
            coupon = rng.choice(promotions)["id"]
        elif roll < 0.72:
            coupon = _pid(999_999)

        local = datetime(2026, 9, 1) + timedelta(
            days=rng.randint(0, 40),
            minutes=rng.randint(0, 1439),
            seconds=rng.randint(0, 59),
        )
        tz = (
            TZ
            if rng.random() < 0.95
            else rng.choice(["Europe/London", "UTC", "Asia/Kolkata"])
        )
        cases.append(
            _case(
                f"random/{index:04d}",
                lines,
                promotions=promotions,
                at=local.isoformat(),
                tz=tz,
                rounding=rng.choice(
                    ["0", "0", "0.25", "0.25", "0.05", "0.50", "1.00", "0.01"]
                ),
                vat_registered=rng.random() < 0.85,
                coupon=coupon,
            )
        )
    return cases


# ─── Pricing each vector ──────────────────────────────────────────────────────


def price_vector(data: dict) -> dict:
    """The engine's answer for one vector's `input`."""
    from app.services.pos import counter_pricing as cp

    inp = data["input"]
    branch = uuid.UUID(inp["branch_id"])
    at = datetime.fromisoformat(inp["at"].replace("Z", "+00:00"))
    local = at.astimezone(ZoneInfo(inp["timezone"]))
    pricing = cp.price_lines(
        [cp.priced_line_from_wire(line) for line in inp["lines"]],
        promotions=[cp.promo_rule_from_wire(p, branch) for p in inp["promotions"]],
        local_dt=local,
        coupon_id=uuid.UUID(inp["coupon_id"]) if inp.get("coupon_id") else None,
        branch_id=branch,
        rounding_step=Decimal(inp["rounding_step"]),
        vat_registered=bool(inp["vat_registered"]),
        source=inp.get("source", "cashier"),
        order_type=inp.get("order_type", "pickup"),
    )
    return cp.pricing_to_wire(pricing)


def build() -> dict:
    from app.services.pos import counter_pricing as cp

    rng = random.Random(SEED)
    vectors = hand_cases() + random_cases(rng, RANDOM_COUNT)
    for vector in vectors:
        inp = vector["input"]
        at = datetime.fromisoformat(inp["at"].replace("Z", "+00:00"))
        inp["local_datetime"] = at.astimezone(ZoneInfo(inp["timezone"])).isoformat()
        vector["expected"] = price_vector(vector)
    return {
        "engine_version": cp.ENGINE_VERSION,
        "seed": SEED,
        "generator": "scripts/export_counter_pricing_vectors.py",
        "count": len(vectors),
        "vectors": vectors,
    }


def render(document: dict) -> str:
    """One vector per line, keys sorted: diffable and under 3 MB."""
    head = {k: v for k, v in document.items() if k != "vectors"}
    lines = [
        json.dumps(v, sort_keys=True, separators=(",", ":"))
        for v in document["vectors"]
    ]
    body = ",\n".join(lines)
    header = json.dumps(head, sort_keys=True, separators=(",", ":"))[:-1]
    return f'{header},"vectors":[\n{body}\n]}}\n'


def main() -> int:
    text = render(build())
    if "--check" in sys.argv:
        current = OUT_PATH.read_text() if OUT_PATH.exists() else ""
        if current != text:
            print(
                f"{OUT_PATH} is stale. Run: cd apps/api && "
                "python -m scripts.export_counter_pricing_vectors",
                file=sys.stderr,
            )
            return 1
        print("counter_pricing_vectors.json is current.")
        return 0
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text)
    print(f"wrote {OUT_PATH} ({len(text) / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
