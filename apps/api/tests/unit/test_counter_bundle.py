"""
The local-first counter's config bundle, without a database.

The DB-backed behaviour — ETag/304, persistence, ticket prefixes, the build
gate over HTTP — is in `tests/integration/test_counter_bundle.py`. These pin
the pure parts the register relies on: the canonical hash, the mode matrix, the
bundle body → pricing context round trip, and the ticket number grammar.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.pos_builds import COUNTER_LOCAL_FIRST_MIN_BUILD
from app.schemas.pos_counter import (
    BundleBranch,
    BundleEntity,
    BundleProduct,
    BundlePromotion,
    BundleResolvedTax,
    BundleTax,
    BundleTaxGroup,
    CounterBundleBody,
)
from app.services.pos import counter_bundle_service as svc
from app.services.pos import counter_pricing as cp

BRANCH = uuid.uuid4()
GROUP = uuid.uuid4()
TAX = uuid.uuid4()
COOKIES = uuid.uuid4()
BROWNIE = uuid.uuid4()
COOKIE = uuid.uuid4()
PROMO = uuid.uuid4()


def _body(*, vat_registered: bool = True) -> CounterBundleBody:
    return CounterBundleBody(
        engine_version=cp.ENGINE_VERSION,
        currency_code="AED",
        currency_symbol="AED",
        timezone="Asia/Dubai",
        rounding_step="0.250",
        branch=BundleBranch(
            id=BRANCH,
            name="Sharjah",
            reference="K001",
            business_day_start="04:00",
            cash_enabled=True,
        ),
        entity=BundleEntity(id=uuid.uuid4(), vat_registered=vat_registered),
        tax_groups=[
            BundleTaxGroup(
                id=GROUP,
                name="VAT",
                taxes=[
                    BundleTax(
                        id=TAX,
                        name="VAT 5%",
                        rate="0.0500",
                        type="inclusive",
                        is_active=True,
                    )
                ],
                resolved=BundleResolvedTax(
                    rate="0.0500", name="VAT 5%", tax_id=str(TAX), inclusive=True
                ),
            )
        ],
        products=[
            BundleProduct(
                id=BROWNIE,
                name="Brownie",
                base_price="21.00",
                pricing_method="fixed",
                is_sold_by_weight=False,
                is_non_revenue=False,
                tax_group_id=GROUP,
            ),
            BundleProduct(
                id=COOKIE,
                name="Cookie",
                base_price="12.50",
                pricing_method="fixed",
                is_sold_by_weight=False,
                is_non_revenue=False,
                tax_group_id=GROUP,
                category_id=COOKIES,
            ),
        ],
        modifiers=[],
        menu_tree=[],
        payment_methods=[],
        promotions=[
            BundlePromotion(
                id=PROMO,
                name="Cookies 15%",
                reward="percentage_off_order",
                reward_value="15.0000",
                trigger="spend",
                trigger_value="0.00",
                category_ids=[COOKIES],
                sources=["cashier"],
                mode="coupon",
                rank=0,
            )
        ],
        void_reasons=[],
        kitchen_flows=[],
    )


def test_the_hash_is_over_canonical_json_and_ignores_key_order():
    payload = svc.body_payload(_body())
    shuffled = dict(reversed(list(payload.items())))
    assert svc.sha256_hex(payload) == svc.sha256_hex(shuffled)
    assert len(svc.sha256_hex(payload)) == 64
    assert svc.canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'


def test_money_is_serialised_as_strings():
    payload = svc.body_payload(_body())
    assert payload["products"][0]["base_price"] == "21.00"
    assert payload["tax_groups"][0]["resolved"]["rate"] == "0.0500"
    assert payload["rounding_step"] == "0.250"


@pytest.mark.parametrize(
    ("flag", "build", "mode"),
    [
        ("on", str(COUNTER_LOCAL_FIRST_MIN_BUILD), "on"),
        ("shadow", str(COUNTER_LOCAL_FIRST_MIN_BUILD + 5), "shadow"),
        ("off", str(COUNTER_LOCAL_FIRST_MIN_BUILD + 5), "off"),
        ("on", str(COUNTER_LOCAL_FIRST_MIN_BUILD - 1), "off"),
        ("on", None, "off"),
        ("on", "not-a-number", "off"),
        ("bogus", str(COUNTER_LOCAL_FIRST_MIN_BUILD), "off"),
    ],
)
def test_the_mode_a_terminal_runs_is_the_flag_gated_by_its_build(flag, build, mode):
    assert svc.effective_mode(flag, build) == mode


def test_a_bundle_body_round_trips_into_the_pricing_context():
    ctx = svc.context_from_payload(svc.body_payload(_body()))
    assert ctx.branch_id == BRANCH
    assert ctx.rounding_step == Decimal("0.250")
    assert ctx.tax_for(BROWNIE) == cp.TaxTuple(
        Decimal("0.0500"), "VAT 5%", str(TAX), True
    )
    assert ctx.facts_for(COOKIE).category_id == COOKIES
    assert ctx.promotions[0].coupon_branch_ids == frozenset({BRANCH})

    lines = [
        cp.CheckLine(
            id="a", product_id=COOKIE, quantity=2, unit_price=Decimal("12.50")
        ),
        cp.CheckLine(
            id="b", product_id=BROWNIE, quantity=1, unit_price=Decimal("21.00")
        ),
    ]
    at = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    plain = cp.price_check(ctx, lines, at)
    assert plain.promotion is None and plain.total == Decimal("46.00")
    couponed = cp.price_check(ctx, lines, at, coupon_id=PROMO)
    assert couponed.promotion.mode == "coupon"
    assert couponed.promotion.line_ids == ("a",)
    # 25.00 of cookies less 15%, the brownie untouched; rounded to 0.25.
    assert couponed.total == Decimal("42.25")


def test_an_unregistered_entity_prices_no_vat_and_the_same_total():
    ctx = svc.context_from_payload(svc.body_payload(_body(vat_registered=False)))
    lines = [
        cp.CheckLine(
            id="a", product_id=BROWNIE, quantity=1, unit_price=Decimal("21.00")
        )
    ]
    pricing = cp.price_check(ctx, lines, datetime.now(timezone.utc))
    assert pricing.tax_total == Decimal("0.00")
    assert pricing.taxes == []
    assert pricing.total == Decimal("21.00")


@pytest.mark.parametrize(
    ("value", "parsed"),
    [
        ("T1-0042", ("T1", 42)),
        ("T12-1234567", ("T12", 1234567)),
        ("T1", None),
        ("T1-", None),
        ("T1-00a2", None),
        (None, None),
    ],
)
def test_ticket_numbers_parse(value, parsed):
    assert svc.parse_display_number(value) == parsed
