"""Building a GrubOps order from Foodics when GrubOps withholds the detail.

The fixtures are the real shapes read off production on 2026-10-05: Talabat
3937792428 at Barsha (Foodics #20485, Mix Brownies Box Of 3 with three brownie
options), listed by GrubOps at 15:25:44Z and never served by `getOrderInfo`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.grubops_order import GrubOpsOrderMap
from app.services.grubops import grubops_foodics_fallback as fb
from app.services.grubops import grubops_orders_service as g
from app.services.providers.foodics_provider import FoodicsError

BARSHA_FOODICS = "a0371d1d-2971-4ead-9885-e706f01da3d4"
SHARJAH_FOODICS = "a0371d1d-1d1f-40f3-834f-5956b94d7b2b"
LISTED = datetime(2026, 10, 5, 15, 25, 44, tzinfo=timezone.utc)

SUMMARY = {
    "orderId": "1424417091114426368",
    "externalId": "3937792428",
    "locationId": "692300c0323715175fede66c",
    "source": {"channel": "Talabat"},
    "customer": {"customerName": "Hana unknown", "phoneNumber": "+97144451555"},
    "paymentMethod": "PREPAID",
    "status": "OrderStarted",
    "createdAt": "2026-10-05T15:25:44.035Z",
}


def _option(option_id, name, *, unit="0", quantity=1):
    return {
        "quantity": quantity,
        "unit_price": unit,
        "total_price": unit,
        "modifier_option": {"id": option_id, "name": name},
    }


def _foodics_order(**overrides):
    order = {
        "id": "0b0cac4e-9d65-4972-ade4-38a63df5dcf1",
        "reference": 20485,
        "status": 1,
        "created_at": "2026-10-05 15:24:59",
        "branch": {"id": BARSHA_FOODICS, "name": "Barsha Heights"},
        "discount_amount": 0,
        "total_price": 55,
        "kitchen_notes": (
            "external_number : Melting Moments - Talabat: 3937792428, #2663 \n"
            " No cutlery."
        ),
        "meta": {
            "external_number": "Melting Moments - Talabat: 3937792428, #2663",
            "external_source": "Talabat",
        },
        "products": [
            {
                "quantity": 1,
                "unit_price": 55,
                "total_price": 55,
                "status": 1,
                "kitchen_notes": None,
                "product": {
                    "id": "a05b0215-965b-4acc-9a8e-bfb1af9f779c",
                    "name": "Mix Brownies Box Of 3",
                },
                "options": [
                    _option("a05ca58d-77df", "Tiramisu Brownie"),
                    _option("a05ca58d-93ef", "Lindor Brownie"),
                    _option("a05ca58d-b1e8", "Cheesecake Brownie"),
                ],
            }
        ],
    }
    order.update(overrides)
    return order


RECIPES = {"a05b0215-965b-4acc-9a8e-bfb1af9f779c": "recipe-box3"}
MODIFIERS = {
    ("recipe-box3", "a05ca58d-77df"): "mod-tiramisu",
    ("recipe-box3", "a05ca58d-93ef"): "mod-lindor",
    ("recipe-box3", "a05ca58d-b1e8"): "mod-cheesecake",
}


# ── reading Foodics' external number ─────────────────────────────────────────


def test_external_number_yields_the_id_and_talabats_short_code():
    meta = {"external_number": "Melting Moments - Talabat: 3938122835, #2665"}
    assert fb.parse_external_number(meta) == ("3938122835", "2665")


def test_external_number_without_a_short_code():
    assert fb.parse_external_number(
        {"external_number": "Melting Moments - Keeta 2.0: 5447841856256368"}
    ) == ("5447841856256368", None)
    assert fb.parse_external_number(
        {"external_number": "Melting Moments - Noon: 4158"}
    ) == ("4158", None)


def test_external_number_garbage_is_nothing():
    assert fb.parse_external_number(None) == (None, None)
    assert fb.parse_external_number({}) == (None, None)
    assert fb.parse_external_number({"external_number": "walk-in"}) == (None, None)


# ── finding the one matching Foodics order ───────────────────────────────────


def test_the_matching_order_is_found():
    order = _foodics_order()
    match, reason = fb.find_match(
        SUMMARY,
        [_foodics_order(id="other", meta={}), order],
        foodics_branch_id=BARSHA_FOODICS,
    )
    assert match is order and reason == ""


def test_another_branchs_order_never_matches():
    order = _foodics_order(branch={"id": SHARJAH_FOODICS})
    match, reason = fb.find_match(SUMMARY, [order], foodics_branch_id=BARSHA_FOODICS)
    assert match is None and reason == "no Foodics order matches"


def test_the_same_short_id_on_another_channel_never_matches():
    # Noon and Deliveroo both reuse short per-day ids like "4158".
    summary = {**SUMMARY, "externalId": "4158", "source": {"channel": "Noon"}}
    deliveroo = _foodics_order(
        meta={
            "external_number": "Melting Moments - Deliveroo: 4158",
            "external_source": "Deliveroo",
        }
    )
    match, _ = fb.find_match(summary, [deliveroo], foodics_branch_id=BARSHA_FOODICS)
    assert match is None


def test_keeta_matches_across_grubtechs_two_spellings():
    summary = {
        **SUMMARY,
        "externalId": "5447841856256368",
        "source": {"channel": "Keeta"},
    }
    keeta = _foodics_order(
        meta={
            "external_number": "Melting Moments - Keeta 2.0: 5447841856256368",
            "external_source": "Keeta 2.0",
        }
    )
    match, _ = fb.find_match(summary, [keeta], foodics_branch_id=BARSHA_FOODICS)
    assert match is keeta


def test_an_order_outside_the_window_is_another_days_order():
    stale = _foodics_order(created_at="2026-10-05 14:40:00")
    match, _ = fb.find_match(SUMMARY, [stale], foodics_branch_id=BARSHA_FOODICS)
    assert match is None


def test_two_candidates_is_a_refusal_not_a_guess():
    match, reason = fb.find_match(
        SUMMARY,
        [_foodics_order(), _foodics_order(id="dup")],
        foodics_branch_id=BARSHA_FOODICS,
    )
    assert match is None and reason == "2 Foodics orders match"


def test_a_listing_without_an_external_id_matches_nothing():
    match, _ = fb.find_match(
        {**SUMMARY, "externalId": None},
        [_foodics_order()],
        foodics_branch_id=BARSHA_FOODICS,
    )
    assert match is None


# ── money ────────────────────────────────────────────────────────────────────


def test_the_delivery_charge_foodics_adds_is_not_the_sale():
    # Talabat 3938122835: Foodics total 69.9 = lines 65 + a 4.9 delivery charge;
    # GrubOps' totalPrice was 65.
    order = _foodics_order(
        total_price=69.9,
        products=[
            {
                "quantity": 1,
                "unit_price": 30,
                "total_price": 30,
                "status": 1,
                "product": {"id": "p1", "name": "Chocolate Mousse"},
                "options": [],
            },
            {
                "quantity": 1,
                "unit_price": 35,
                "total_price": 35,
                "status": 1,
                "product": {"id": "p2", "name": "Cake Slice"},
                "options": [],
            },
        ],
    )
    assert fb.order_money(order) == (
        Decimal("65.00"),
        Decimal("0.00"),
        Decimal("65.00"),
    )


def test_a_discount_comes_off_like_grubops_reports_it():
    # Noon 8130: GrubOps gross 70, discount 20, totalPrice 50.
    order = _foodics_order(
        discount_amount=20,
        meta={"external_number": "x: 8130", "customer_paid_amount": "50.0"},
        products=[
            {
                "quantity": 1,
                "unit_price": 70,
                "total_price": 70,
                "status": 1,
                "product": {"id": "p", "name": "Brookie"},
                "options": [],
            }
        ],
    )
    assert fb.order_money(order)[2] == Decimal("50.00")
    assert fb.money_disagreement(order) is None


def test_a_paid_amount_that_disagrees_refuses():
    order = _foodics_order(
        meta={**_foodics_order()["meta"], "customer_paid_amount": "60.0"}
    )
    assert "customer paid 60.0" in fb.money_disagreement(order)


def test_voided_lines_are_not_sold():
    order = _foodics_order()
    order["products"].append(
        {
            "quantity": 1,
            "unit_price": 40,
            "total_price": 40,
            "status": 5,
            "product": {"id": "void", "name": "Voided"},
            "options": [],
        }
    )
    assert fb.order_money(order)[2] == Decimal("55.00")
    order["products"] = [order["products"][1]]
    assert fb.money_disagreement(order) == "the Foodics order has no live lines"


# ── the payload the unchanged ingest reads ───────────────────────────────────


def _info(**kwargs):
    return fb.build_info(
        SUMMARY, _foodics_order(**kwargs), recipes=RECIPES, modifiers=MODIFIERS
    )


def test_the_built_payload_reads_like_a_served_one():
    info = _info()
    header = info["orderHeader"]
    assert header["orderStatus"] == "OrderStarted"
    assert header["totalPrice"] == 55.0
    assert header["foodAggregatorName"] == "Talabat"
    assert fb.is_fallback_raw(info)

    groups = g._group_lines(info["orderLines"])
    assert len(groups) == 1
    assert groups[0]["item"]["recipeId"] == "recipe-box3"
    assert [m["modifierId"] for m in groups[0]["modifiers"]] == [
        "mod-tiramisu",
        "mod-lindor",
        "mod-cheesecake",
    ]


def test_the_foodics_id_is_where_the_ingest_caches_it_from():
    # Packed / Cancel write back through this id.
    assert g._foodics_order_id(_info()) == "0b0cac4e-9d65-4972-ade4-38a63df5dcf1"


def test_the_rider_code_and_the_note_come_out_as_for_a_served_order():
    info = _info()
    header = info["orderHeader"]
    assert g._driver_code(header, "3937792428", info, channel="Talabat") == "2663"
    assert g._customer_note(header) == "No cutlery."


def test_the_customer_and_payment_come_from_the_grubops_listing():
    info = _info()
    name, phone, *_ = g._customer_fields(info["customer"])
    assert name == "Hana unknown"
    assert phone == "+97144451555"
    assert g._payment_type(info["orderHeader"]) == "prepaid"
    cash = fb.build_info(
        {**SUMMARY, "paymentMethod": "CASH"},
        _foodics_order(),
        recipes=RECIPES,
        modifiers=MODIFIERS,
    )
    assert g._payment_type(cash["orderHeader"]) == "postpaid"


def test_money_fields_match_the_served_order_rules():
    fields = g.money_fields_from_info(_info())
    assert fields["total"] == Decimal("55.00")
    # Not itemised, so the 5% is derived from the gross like a served order.
    assert fields["vat_amount"] == Decimal("2.62")
    assert fields["total_excl_vat"] == Decimal("52.38")


def test_placed_at_is_the_listing_time():
    assert g._placed_at(_info()) == datetime(
        2026, 10, 5, 15, 25, 44, 35000, tzinfo=timezone.utc
    )


def test_an_unknown_item_is_written_unmapped_not_dropped():
    info = fb.build_info(SUMMARY, _foodics_order(), recipes={}, modifiers={})
    groups = g._group_lines(info["orderLines"])
    assert groups[0]["item"]["recipeId"] is None
    assert groups[0]["item"]["name"] == "Mix Brownies Box Of 3"
    assert all(m["modifierId"] is None for m in groups[0]["modifiers"])


def test_a_priced_option_prices_the_item():
    order = _foodics_order(
        products=[
            {
                "quantity": 2,
                "unit_price": 0,
                "total_price": 100,
                "status": 1,
                "product": {"id": "fudge", "name": "Fudge Brownies"},
                "options": [_option("three", "3 Pieces", unit="50")],
            }
        ],
    )
    info = fb.build_info(SUMMARY, order, recipes={}, modifiers={})
    item, modifier = info["orderLines"]
    assert item["quantity"] == 2.0 and modifier["unitPrice"] == 50.0
    assert info["orderHeader"]["totalPrice"] == 100.0


def test_with_status_moves_only_the_status():
    raw = _info()
    moved = fb.with_status(raw, "OrderCanceled")
    assert moved["orderHeader"]["orderStatus"] == "OrderCanceled"
    assert raw["orderHeader"]["orderStatus"] == "OrderStarted"
    assert moved["orderLines"] == raw["orderLines"]
    assert fb.is_fallback_raw(moved)


# ── the gates around a Foodics read ──────────────────────────────────────────


class _Result:
    def __init__(self, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """Answers the three reads `build_from_foodics` makes, in order."""

    def __init__(self, *, branch=BARSHA_FOODICS, history=(), approved=()):
        self.results = [
            _Result(scalar=branch),
            _Result(rows=history),
            _Result(rows=approved),
        ]

    async def execute(self, *_args, **_kwargs):
        return self.results.pop(0)


def _order_map(**kwargs):
    return GrubOpsOrderMap(
        grubops_order_id=SUMMARY["orderId"],
        external_id=SUMMARY["externalId"],
        source_channel="Talabat",
        location_id=SUMMARY["locationId"],
        **kwargs,
    )


@pytest.fixture
def enabled():
    with patch.object(fb.foodics_orders_service, "is_enabled", return_value=True):
        yield


def _foodics(pages):
    return SimpleNamespace(list_recent_orders=AsyncMock(side_effect=pages))


@pytest.mark.asyncio
async def test_nothing_is_read_while_the_write_back_is_off():
    client = _foodics([[_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(), SUMMARY, _order_map(), now=LISTED + timedelta(minutes=5)
        )
    assert info is None
    client.list_recent_orders.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "age", [timedelta(seconds=30), timedelta(hours=13)], ids=["too young", "too old"]
)
async def test_outside_the_window_grubops_or_promotion_owns_it(enabled, age):
    client = _foodics([[_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(), SUMMARY, _order_map(), now=LISTED + age
        )
    assert info is None
    client.list_recent_orders.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_order_already_made_is_never_rebuilt(enabled):
    client = _foodics([[_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(),
            SUMMARY,
            _order_map(mm_order_id=uuid.uuid4()),
            now=LISTED + timedelta(minutes=5),
        )
    assert info is None


@pytest.mark.asyncio
async def test_a_branch_without_a_foodics_map_waits(enabled):
    client = _foodics([[_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(branch=None),
            SUMMARY,
            _order_map(),
            now=LISTED + timedelta(minutes=5),
        )
    assert info is None
    client.list_recent_orders.assert_not_awaited()


@pytest.mark.asyncio
async def test_foodics_being_down_is_waiting_not_a_failure(enabled):
    client = SimpleNamespace(
        list_recent_orders=AsyncMock(side_effect=FoodicsError("unreachable"))
    )
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(), SUMMARY, _order_map(), now=LISTED + timedelta(minutes=5)
        )
    assert info is None


@pytest.mark.asyncio
async def test_the_order_is_built_with_ids_from_served_history(enabled):
    history = [
        ("a05b0215-965b-4acc-9a8e-bfb1af9f779c", "recipe-box3", None),
        ("a05ca58d-77df", "recipe-box3", "mod-tiramisu"),
        ("a05ca58d-93ef", "recipe-box3", "mod-lindor"),
    ]
    # Cheesecake was never served through GrubOps: its approved map name finds it.
    approved = [
        ("option", "recipe-box3", "mod-cheesecake", "Cheesecake Brownie"),
        ("option", "other-recipe", "mod-elsewhere", "Cheesecake Brownie"),
    ]
    client = _foodics([[_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(history=history, approved=approved),
            SUMMARY,
            _order_map(),
            now=LISTED + timedelta(minutes=5),
        )
    assert info is not None
    groups = g._group_lines(info["orderLines"])
    assert groups[0]["item"]["recipeId"] == "recipe-box3"
    assert [m["modifierId"] for m in groups[0]["modifiers"]] == [
        "mod-tiramisu",
        "mod-lindor",
        "mod-cheesecake",
    ]
    client.list_recent_orders.assert_awaited_once_with(branch_id=BARSHA_FOODICS, page=1)


@pytest.mark.asyncio
async def test_a_product_name_shared_by_two_recipes_stays_unmapped(enabled):
    approved = [
        ("product", "recipe-a", None, "Mix Brownies Box Of 3"),
        ("product", "recipe-b", None, "Mix Brownies Box Of 3"),
    ]
    client = _foodics([[_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(approved=approved),
            SUMMARY,
            _order_map(),
            now=LISTED + timedelta(minutes=5),
        )
    assert g._group_lines(info["orderLines"])[0]["item"]["recipeId"] is None


@pytest.mark.asyncio
async def test_a_second_page_is_read_only_while_it_can_still_hold_the_order(enabled):
    newer = _foodics_order(
        id="newer",
        created_at="2026-10-05 15:50:00",
        meta={"external_number": "x: 1", "external_source": "Talabat"},
    )
    client = _foodics([[newer], [_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(), SUMMARY, _order_map(), now=LISTED + timedelta(minutes=30)
        )
    assert info is not None
    assert client.list_recent_orders.await_count == 2

    older = _foodics_order(
        id="older",
        created_at="2026-10-05 13:00:00",
        meta={"external_number": "x: 1", "external_source": "Talabat"},
    )
    client = _foodics([[older], [_foodics_order()]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(), SUMMARY, _order_map(), now=LISTED + timedelta(minutes=30)
        )
    assert info is None
    assert client.list_recent_orders.await_count == 1


@pytest.mark.asyncio
async def test_money_that_disagrees_is_refused_before_anything_is_built(enabled):
    bad = _foodics_order(
        meta={**_foodics_order()["meta"], "customer_paid_amount": "99"}
    )
    client = _foodics([[bad]])
    with patch.object(fb, "foodics", client):
        info = await fb.build_from_foodics(
            _Session(), SUMMARY, _order_map(), now=LISTED + timedelta(minutes=5)
        )
    assert info is None
