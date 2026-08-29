"""Unit tests for the noon OMS history sales path.

Covers:
- OMS order parsing → StandardOrder (identity, timing, money, no fee fields)
- OMS item parsing → StandardOrderItem (name/category from menuInfo, qty)
- Modifier expansion from the Noon nested-map shape {MDxxx: {Ixxx: qty}}
- Merge of OMS order into an RMS order (_merge_oms_into_rms)
- fetch_sales fallback: OMS auth failure → RMS-only result with truncation note
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services.aggregators.normalized import StandardStatusEvent
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.aggregator_base import (
    AggregatorAuthError,
    AggregatorUnavailableError,
)
from app.services.providers.noon_provider import (
    NoonClient,
    _merge_oms_into_rms,
    _rows_from_csv,
)

# ── shared fixtures ────────────────────────────────────────────────────────────

_OMS_ORDER_SINGLE_MOD = {
    "orderNr": "FG4LNN5NPGYI0JA",
    "orderRef": "2253",
    "currencyCode": "AED",
    "orderStatusCode": "delivered",
    "orderSubtotal": 45.0,
    "orderOutletSubtotal": 45.0,
    "orderRestaurantToInvoice": 45.0,
    "orderPostpaidFee": 0.0,
    "orderDeliveryFeeOutlet": 3.9,
    "createdAt": "2026-04-21T22:17:14",
    "estimatedAcceptedAt": "2026-04-21T22:17:47",
    "estimatedDeliveryAt": "2026-04-21T22:43:49",
    "outletInfo": {"outletCode": "MLTNGM1GBF"},
    "items": [
        {
            "itemCode": "I897561655A",
            "price": 45.0,
            "qty": 1,
            "totalPrice": 45.0,
            "modifiers": {"MD200624362A": {"I087324445B": 1}},
        }
    ],
    "menuInfo": {
        "categories": [{"categoryCode": "C111192295A", "nameEn": "Cookie Melt"}],
        "items": [
            {
                "itemCode": "I897561655A",
                "name": "Cookie Melt (1-2)",
                "categoryCode": "C111192295A",
            }
        ],
        "modifiers": [
            {
                "modifierCode": "MD200624362A",
                "name": "Your Choice of Quantity",
                "nameEn": "Your Choice of Quantity",
            }
        ],
    },
}

_OMS_ORDER_MULTI_MOD = {
    "orderNr": "FG4FNN2BSMILKZA",
    "currencyCode": "AED",
    "orderStatusCode": "delivered",
    "orderSubtotal": 60.0,
    "orderOutletSubtotal": 60.0,
    "orderRestaurantToInvoice": 60.0,
    "createdAt": "2026-04-20T10:00:00",
    "outletInfo": {"outletCode": "MLTNGM1GBF"},
    "items": [
        {
            "itemCode": "I111111111A",
            "price": 30.0,
            "qty": 2,
            "totalPrice": 60.0,
            "modifiers": {
                "MD101347421A": {
                    "I059380009B": 1,
                    "I097385468B": 1,
                    "I212109423B": 2,
                }
            },
        }
    ],
    "menuInfo": {
        "categories": [],
        "items": [{"itemCode": "I111111111A", "name": "Brownie", "categoryCode": "C1"}],
        "modifiers": [
            {"modifierCode": "MD101347421A", "name": "Toppings", "nameEn": "Toppings"}
        ],
    },
}

# A fully-populated OMS order: customer address, delivery agent, and the
# estimated status timeline — the enrichment the OMS panel exposes and the RMS
# statement drops.
_OMS_ORDER_ENRICHED = {
    "orderNr": "FG4LNNENRICH01A",
    "orderRef": "3120",
    "currencyCode": "AED",
    "orderStatusCode": "delivered",
    "outletStatusCode": "picked_up",
    "logisticsStatusCode": "on_the_way",
    "orderSubtotal": 72.0,
    "orderOutletSubtotal": 72.0,
    "orderRestaurantToInvoice": 72.0,
    "createdAt": "2026-04-22T18:05:00",
    "omsVisibleAt": "2026-04-22T18:05:10",
    "estimatedAcceptedAt": "2026-04-22T18:06:30",
    "estimatedDaAssignedAt": "2026-04-22T18:08:00",
    "estimatedDaReachedRestaurantAt": "2026-04-22T18:20:00",
    "estimatedReadyAt": "2026-04-22T18:22:00",
    "estimatedOutletPickedUpAt": "2026-04-22T18:25:00",
    "estimatedPickedUpAt": "2026-04-22T18:26:00",
    "estimatedDeliveryAt": "2026-04-22T18:45:00",
    "outletInfo": {"outletCode": "MLTNGM1GBF"},
    "customerInfo": {
        "name": "Layla H.",
        "phone": "+971500000000",
        "addressArea": "Al Barsha",
        "addressCity": "Dubai",
        "addressStreet": "Street 12, Villa 4",
        "addressLat": 25.1122,
        "addressLng": 55.1998,
    },
    "daName": "Rider Ahmed",
    "daPhone": "+971555555555",
    "daInfo": "Bike",
    "daPlateNo": "D-12345",
    "daVehicleType": "motorcycle",
    "items": [],
    "menuInfo": {"categories": [], "items": [], "modifiers": []},
}

# An OMS order with the rider unassigned (noon's UNKNOWN placeholder) and only a
# partial timeline.
_OMS_ORDER_UNASSIGNED = {
    "orderNr": "FG4LNNUNASSN1A",
    "currencyCode": "AED",
    "orderStatusCode": "accepted",
    "outletStatusCode": "preparing",
    "logisticsStatusCode": "UNKNOWN",
    "orderSubtotal": 20.0,
    "createdAt": "2026-04-22T12:00:00",
    "estimatedAcceptedAt": "2026-04-22T12:01:30",
    "outletInfo": {"outletCode": "MLTNGM1GBF"},
    "customerInfo": {
        "name": "Sara",
        "phone": "+971511111111",
        "addressLat": 25.2,
        "addressLng": 55.3,
    },
    "daName": "UNKNOWN",
    "daPhone": "UNKNOWN",
    "items": [],
    "menuInfo": {"categories": [], "items": [], "modifiers": []},
}

_OMS_ORDER_NO_ITEMS = {
    "orderNr": "FG4LNNVI4TB1B1A",
    "currencyCode": "AED",
    "orderStatusCode": "delivered",
    "orderSubtotal": 32.0,
    "createdAt": "2026-04-21T22:17:14",
    "outletInfo": {"outletCode": "MLTNGMG2B1"},
    "items": [],
    "menuInfo": {"categories": [], "items": [], "modifiers": []},
}

# An RMS CSV row matching _OMS_ORDER_SINGLE_MOD.
_RMS_ROW = {
    "order_nr": "FG4LNN5NPGYI0JA",
    "order_date": "2026-04-21",
    "order_status": "delivered",
    "outlet_code": "MLTNGM1GBF",
    "currency": "AED",
    "order_value": "45.0",
    "rest_invoice": "36.0",
    "fees_exc_vat": "9.0",
    "payment_fee": "0.5",
    "delivery_fee": "2.0",
    "cancellation_fee": "0.0",
    "lead_generation_fee": "0.0",
    "discount_service_fee": "0.0",
    "long_distance_fee_mp": "0.0",
    "delivery_discount_fee": "0.0",
    "total_vat": "0.63",
    "net_payable": "35.37",
    "statement_nr": "ST-2026-08-22",
}

_CLIENT = NoonClient()


# ── OMS order parsing ──────────────────────────────────────────────────────────


class TestOrderFromOms:
    def test_sets_identity_fields(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert o is not None
        assert o.external_order_id == "FG4LNN5NPGYI0JA"
        # The short customer code (GrubTech's `externalId`) is captured alongside
        # the long orderNr, so a Barsha/Sharjah order converges with its GrubOps twin.
        assert o.display_ref == "2253"
        assert o.external_outlet_id == "MLTNGM1GBF"
        assert o.status == "delivered"
        assert o.currency == "AED"

    def test_sets_placed_at_and_business_date(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert o.placed_at == datetime(2026, 4, 21, 22, 17, 14)
        assert o.business_date == "2026-04-21"

    def test_sets_accepted_at_and_delivered_at(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert o.accepted_at == datetime(2026, 4, 21, 22, 17, 47)
        assert o.delivered_at == datetime(2026, 4, 21, 22, 43, 49)

    def test_gross_sales_from_outlet_subtotal(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert o.gross_sales == Decimal("45.0")

    def test_fee_settlement_fields_are_none(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert o.commission_amount is None
        assert o.vat_amount is None
        assert o.cancellation_fee is None
        assert o.statement_id is None

    def test_delivery_fee_from_outlet_delivery_fee(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert o.delivery_fee == Decimal("3.9")

    def test_returns_none_for_missing_order_nr(self):
        assert _CLIENT._order_from_oms({"orderSubtotal": 10}) is None

    def test_order_without_items_has_empty_list(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_NO_ITEMS)
        assert o is not None
        assert o.items == []


# ── OMS enrichment: customer address, delivery agent, status timeline ─────────


class TestCustomerAddressFromOms:
    def test_builds_address_from_customer_info(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_ENRICHED)
        assert o is not None
        assert o.customer_address == {
            "area": "Al Barsha",
            "city": "Dubai",
            "street": "Street 12, Villa 4",
            "lat": 25.1122,
            "lng": 55.1998,
        }

    def test_geocode_only_address_keeps_coordinates(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_UNASSIGNED)
        assert o.customer_address == {"lat": 25.2, "lng": 55.3}

    def test_none_when_no_customer_info(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_NO_ITEMS)
        assert o.customer_address is None


class TestDeliveryAgentFromOms:
    def test_sets_driver_name_and_phone(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_ENRICHED)
        assert o.driver_name == "Rider Ahmed"
        assert o.driver_phone == "+971555555555"

    def test_driver_status_from_logistics_code(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_ENRICHED)
        assert o.driver_status == "on_the_way"

    def test_driver_status_falls_back_to_outlet_status(self):
        order = {**_OMS_ORDER_ENRICHED}
        order.pop("logisticsStatusCode")
        o = _CLIENT._order_from_oms(order)
        assert o.driver_status == "picked_up"

    def test_unknown_placeholder_is_none(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_UNASSIGNED)
        assert o.driver_name is None
        assert o.driver_phone is None
        # logisticsStatusCode is "UNKNOWN" → falls back to outletStatusCode
        assert o.driver_status == "preparing"


class TestStatusEventsFromOms:
    def test_full_timeline_in_sequence(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_ENRICHED)
        statuses = [(e.status, e.sequence) for e in o.status_events]
        assert statuses == [
            ("placed", 1),
            ("accepted", 2),
            ("driver_assigned", 3),
            ("driver_at_restaurant", 4),
            ("ready", 5),
            ("picked_up", 6),
            ("delivered", 7),
        ]

    def test_events_carry_their_estimated_datetimes(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_ENRICHED)
        by_status = {e.status: e.at for e in o.status_events}
        assert by_status["placed"] == datetime(2026, 4, 22, 18, 5, 0)
        assert by_status["accepted"] == datetime(2026, 4, 22, 18, 6, 30)
        assert by_status["delivered"] == datetime(2026, 4, 22, 18, 45, 0)

    def test_status_deduped_first_timestamp_wins(self):
        """createdAt/omsVisibleAt both mean 'placed' — only one event, from createdAt."""
        o = _CLIENT._order_from_oms(_OMS_ORDER_ENRICHED)
        placed = [e for e in o.status_events if e.status == "placed"]
        assert len(placed) == 1
        assert placed[0].at == datetime(
            2026, 4, 22, 18, 5, 0
        )  # createdAt, not omsVisibleAt

    def test_placed_falls_back_to_oms_visible_at(self):
        order = {**_OMS_ORDER_ENRICHED}
        order.pop("createdAt")
        o = _CLIENT._order_from_oms(order)
        placed = [e for e in o.status_events if e.status == "placed"]
        assert len(placed) == 1
        assert placed[0].at == datetime(2026, 4, 22, 18, 5, 10)  # omsVisibleAt

    def test_missing_steps_are_omitted_and_sequence_stays_contiguous(self):
        o = _CLIENT._order_from_oms(_OMS_ORDER_UNASSIGNED)
        assert [(e.status, e.sequence) for e in o.status_events] == [
            ("placed", 1),
            ("accepted", 2),
        ]

    def test_no_timeline_yields_empty_list(self):
        order = {"orderNr": "FG4LNNNOTIME1A", "currencyCode": "AED", "items": []}
        o = _CLIENT._order_from_oms(order)
        assert o.status_events == []


class TestEnrichmentSurvivesMerge:
    def test_merge_carries_oms_address_driver_and_events(self):
        oms = _CLIENT._order_from_oms(_OMS_ORDER_ENRICHED)
        rms = _CLIENT._order_from(_RMS_ROW)
        merged = _merge_oms_into_rms(oms, rms)
        assert merged.customer_address == oms.customer_address
        assert merged.driver_name == "Rider Ahmed"
        assert merged.driver_phone == "+971555555555"
        assert merged.driver_status == "on_the_way"
        assert merged.status_events == oms.status_events
        assert isinstance(merged.status_events[0], StandardStatusEvent)


# ── OMS item + modifier parsing ───────────────────────────────────────────────


class TestItemsFromOms:
    def test_resolves_item_name_from_menu_info(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert len(items) == 1
        assert items[0].item_name == "Cookie Melt (1-2)"

    def test_resolves_category_name_from_menu_info(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert items[0].category_name == "Cookie Melt"

    def test_parses_quantity_and_price(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert items[0].quantity == Decimal("1")
        assert items[0].unit_price == Decimal("45.0")
        assert items[0].gross_sales == Decimal("45.0")
        assert items[0].net_sales == Decimal("45.0")

    def test_amount_is_known_true(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert items[0].amount_is_known is True

    def test_business_date_from_created_at(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert items[0].business_date == "2026-04-21"

    def test_source_key_includes_order_item_and_index(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_SINGLE_MOD)
        assert items[0].source_key == "FG4LNN5NPGYI0JA:I897561655A:1"

    def test_source_key_unique_for_duplicate_item_codes(self):
        order = {
            **_OMS_ORDER_NO_ITEMS,
            "orderNr": "ORD-DUPE",
            "items": [
                {
                    "itemCode": "I1",
                    "qty": 1,
                    "price": 10,
                    "totalPrice": 10,
                    "modifiers": {},
                },
                {
                    "itemCode": "I1",
                    "qty": 1,
                    "price": 10,
                    "totalPrice": 10,
                    "modifiers": {},
                },
            ],
        }
        items = _CLIENT._items_from_oms(order)
        assert len({i.source_key for i in items}) == 2

    def test_empty_modifiers_yields_no_modifier_list(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_NO_ITEMS)
        assert items == []

    def test_item_with_empty_modifier_dict_has_empty_modifiers(self):
        order = {
            **_OMS_ORDER_SINGLE_MOD,
            "items": [
                {
                    "itemCode": "I897561655A",
                    "qty": 1,
                    "price": 45,
                    "totalPrice": 45,
                    "modifiers": {},
                }
            ],
        }
        items = _CLIENT._items_from_oms(order)
        assert items[0].modifiers == []

    def test_falls_back_to_item_code_when_no_menu_entry(self):
        order = {**_OMS_ORDER_SINGLE_MOD}
        order["menuInfo"] = {"categories": [], "items": [], "modifiers": []}
        items = _CLIENT._items_from_oms(order)
        assert items[0].item_name == "I897561655A"
        assert items[0].category_name is None


# ── Modifier quantity expansion (Noon nested-map shape) ───────────────────────


class TestNoonModifierExpansion:
    def test_single_option_single_qty(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_SINGLE_MOD)
        mods = items[0].modifiers
        assert len(mods) == 1
        mod = mods[0]
        assert mod.external_ref == "I087324445B"
        assert mod.name == "I087324445B"
        assert mod.quantity == Decimal("1")

    def test_multiple_options_correct_qty(self):
        items = _CLIENT._items_from_oms(_OMS_ORDER_MULTI_MOD)
        mods = items[0].modifiers
        assert len(mods) == 3
        qty_map = {m.external_ref: m.quantity for m in mods}
        assert qty_map["I059380009B"] == Decimal("1")
        assert qty_map["I097385468B"] == Decimal("1")
        assert qty_map["I212109423B"] == Decimal("2")

    def test_modifier_qty_2_not_duplicated_as_two_rows(self):
        """qty=2 must be a single StandardModifier(quantity=2), not two rows."""
        items = _CLIENT._items_from_oms(_OMS_ORDER_MULTI_MOD)
        mods = items[0].modifiers
        matching = [m for m in mods if m.external_ref == "I212109423B"]
        assert len(matching) == 1
        assert matching[0].quantity == Decimal("2")

    def test_bare_code_list_modifiers(self):
        order = {
            **_OMS_ORDER_NO_ITEMS,
            "orderNr": "ORD-BARE",
            "items": [
                {
                    "itemCode": "I1",
                    "qty": 1,
                    "price": 10,
                    "totalPrice": 10,
                    "modifiers": ["OPT-A", "OPT-B"],
                }
            ],
        }
        items = _CLIENT._items_from_oms(order)
        refs = {m.external_ref for m in items[0].modifiers}
        assert "OPT-A" in refs
        assert "OPT-B" in refs

    def test_list_of_dicts_modifiers(self):
        order = {
            **_OMS_ORDER_NO_ITEMS,
            "orderNr": "ORD-DICT",
            "items": [
                {
                    "itemCode": "I1",
                    "qty": 1,
                    "price": 10,
                    "totalPrice": 10,
                    "modifiers": [
                        {"modifierCode": "MC1", "name": "Extra Sauce", "qty": 2}
                    ],
                }
            ],
        }
        items = _CLIENT._items_from_oms(order)
        assert len(items[0].modifiers) == 1
        assert items[0].modifiers[0].name == "Extra Sauce"
        assert items[0].modifiers[0].quantity == Decimal("2")


# ── _merge_oms_into_rms ────────────────────────────────────────────────────────


class TestMergeOmsIntoRms:
    def _rms(self):
        return _CLIENT._order_from(_RMS_ROW)

    def _oms(self):
        return _CLIENT._order_from_oms(_OMS_ORDER_SINGLE_MOD)

    def test_merged_order_id_matches_rms(self):
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert merged.external_order_id == "FG4LNN5NPGYI0JA"

    def test_items_come_from_oms(self):
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert len(merged.items) == 1
        assert merged.items[0].modifiers[0].quantity == Decimal("1")

    def test_statement_id_comes_from_rms(self):
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert merged.statement_id == "ST-2026-08-22"

    def test_commission_comes_from_rms(self):
        """commission = fees_exc_vat (9.0) − (payment_fee 0.5 + delivery_fee 2.0) = 6.5"""
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert merged.commission_amount == Decimal("6.5")

    def test_net_payable_comes_from_rms(self):
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert merged.net_payable == Decimal("35.37")

    def test_placed_at_comes_from_oms(self):
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert merged.placed_at == datetime(2026, 4, 21, 22, 17, 14)

    def test_vat_comes_from_rms(self):
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert merged.vat_amount == Decimal("0.63")

    def test_rms_gross_sales_preferred_over_oms(self):
        """RMS settlement gross_sales wins when present (it is the ledger truth)."""
        merged = _merge_oms_into_rms(self._oms(), self._rms())
        assert merged.gross_sales == Decimal("45.0")  # RMS order_value

    def test_oms_gross_sales_used_when_rms_has_none(self):
        rms = self._rms()
        rms_no_gross = type(rms)(
            **{**rms.__dict__, "gross_sales": None}  # type: ignore[arg-type]
        )
        merged = _merge_oms_into_rms(self._oms(), rms_no_gross)
        assert merged.gross_sales == Decimal("45.0")  # falls back to OMS


# ── fetch_sales fallback on OMS failure ───────────────────────────────────────


@pytest.fixture
def session_with_tokens():
    return LoadedSession(
        channel="noon",
        account_ref="test",
        cookies={},
        tokens={
            "restaurant_code": "R5967280642376629909871448A",
            "project": "PRJ135208",
        },
        header_profile={},
    )


_WALLET_CSV_WITH_STATEMENT = (
    "date,entry_type,reference_nr,invoice_nr,amount,currency\n"
    "2026-04-20,statement,ST-001,,100.0,AED\n"
)

_OMS_PAGE_RESPONSE = {
    "status": "success",
    "data": {
        "pages": 1,
        "orders": [_OMS_ORDER_SINGLE_MOD],
    },
}

_RMS_ORDER_ROWS = [_RMS_ROW]


@pytest.mark.asyncio
async def test_fetch_sales_oms_auth_error_falls_back_to_rms(session_with_tokens):
    client = NoonClient()
    since = datetime(2026, 4, 21, 0, 0)
    until = datetime(2026, 4, 22, 0, 0)

    async def _fake_request_json(session, method, url, **kwargs):
        raise AggregatorAuthError("OMS session dead")

    async def _fake_post_tabular(session, url, json_body):
        if "wallet" in url:
            return _rows_from_csv(_WALLET_CSV_WITH_STATEMENT)
        if "statement/orders" in url:
            return _RMS_ORDER_ROWS
        return []

    with (
        patch.object(client, "request_json", side_effect=_fake_request_json),
        patch.object(client, "_post_tabular", side_effect=_fake_post_tabular),
    ):
        result = await client.fetch_sales(session_with_tokens, since=since, until=until)

    assert result.truncation_note is not None
    assert "OMS" in result.truncation_note
    # RMS orders should still be present
    assert any(o.external_order_id == "FG4LNN5NPGYI0JA" for o in result.orders)
    # RMS-only orders have no items
    rms_only = next(
        o for o in result.orders if o.external_order_id == "FG4LNN5NPGYI0JA"
    )
    assert rms_only.items == []


@pytest.mark.asyncio
async def test_fetch_sales_oms_unavailable_falls_back_to_rms(session_with_tokens):
    client = NoonClient()
    since = datetime(2026, 4, 21, 0, 0)
    until = datetime(2026, 4, 22, 0, 0)

    async def _fake_request_json(session, method, url, **kwargs):
        raise AggregatorUnavailableError("OMS 503")

    async def _fake_post_tabular(session, url, json_body):
        if "wallet" in url:
            return _rows_from_csv(_WALLET_CSV_WITH_STATEMENT)
        if "statement/orders" in url:
            return _RMS_ORDER_ROWS
        return []

    with (
        patch.object(client, "request_json", side_effect=_fake_request_json),
        patch.object(client, "_post_tabular", side_effect=_fake_post_tabular),
    ):
        result = await client.fetch_sales(session_with_tokens, since=since, until=until)

    assert result.truncation_note is not None
    assert any(o.external_order_id == "FG4LNN5NPGYI0JA" for o in result.orders)


@pytest.mark.asyncio
async def test_fetch_sales_excludes_rms_only_orders_before_the_window(
    session_with_tokens,
):
    """RMS statements are discovered over a wider (publication) window so late fees
    land, but an RMS-only order dated BEFORE the sales window is an older sale
    settling now — it must not inflate a 'yesterday' pull (the 267-vs-19 bug)."""
    client = NoonClient()
    since = datetime(2026, 4, 21, 0, 0)
    until = datetime(2026, 4, 22, 0, 0)
    old_rms = {**_RMS_ROW, "order_nr": "OLD_SETTLING_ORDER", "order_date": "2026-04-01"}

    async def _fake_request_json(session, method, url, **kwargs):
        raise AggregatorUnavailableError("OMS down")  # force the RMS-only path

    async def _fake_post_tabular(session, url, json_body):
        if "wallet" in url:
            return _rows_from_csv(_WALLET_CSV_WITH_STATEMENT)
        if "statement/orders" in url:
            return [_RMS_ROW, old_rms]  # one in-window (04-21), one old (04-01)
        return []

    with (
        patch.object(client, "request_json", side_effect=_fake_request_json),
        patch.object(client, "_post_tabular", side_effect=_fake_post_tabular),
    ):
        result = await client.fetch_sales(session_with_tokens, since=since, until=until)

    ids = {o.external_order_id for o in result.orders}
    assert "FG4LNN5NPGYI0JA" in ids  # in-window RMS-only order kept
    assert "OLD_SETTLING_ORDER" not in ids  # older settling order excluded


@pytest.mark.asyncio
async def test_fetch_sales_merges_oms_items_with_rms_fees(session_with_tokens):
    client = NoonClient()
    since = datetime(2026, 4, 21, 0, 0)
    until = datetime(2026, 4, 22, 0, 0)

    async def _fake_request_json(session, method, url, **kwargs):
        return _OMS_PAGE_RESPONSE

    async def _fake_post_tabular(session, url, json_body):
        if "wallet" in url:
            return _rows_from_csv(_WALLET_CSV_WITH_STATEMENT)
        if "statement/orders" in url:
            return _RMS_ORDER_ROWS
        return []

    with (
        patch.object(client, "request_json", side_effect=_fake_request_json),
        patch.object(client, "_post_tabular", side_effect=_fake_post_tabular),
    ):
        result = await client.fetch_sales(session_with_tokens, since=since, until=until)

    assert len(result.orders) == 1
    order = result.orders[0]
    # OMS fields
    assert order.placed_at == datetime(2026, 4, 21, 22, 17, 14)
    assert len(order.items) == 1
    assert order.items[0].modifiers[0].quantity == Decimal("1")
    # RMS fields
    assert order.statement_id == "ST-2026-08-22"
    assert order.commission_amount == Decimal("6.5")
    assert order.net_payable == Decimal("35.37")


@pytest.mark.asyncio
async def test_fetch_sales_oms_only_order_has_no_fees(session_with_tokens):
    """An OMS order with no matching RMS entry returns with fee fields as None."""
    client = NoonClient()
    since = datetime(2026, 4, 20, 0, 0)
    until = datetime(2026, 4, 21, 0, 0)

    async def _fake_request_json(session, method, url, **kwargs):
        return {
            "status": "success",
            "data": {"pages": 1, "orders": [_OMS_ORDER_MULTI_MOD]},
        }

    async def _fake_post_tabular(session, url, json_body):
        # Wallet returns one statement but order-level returns nothing matching
        if "wallet" in url:
            return _rows_from_csv(_WALLET_CSV_WITH_STATEMENT)
        return []  # no matching RMS orders

    with (
        patch.object(client, "request_json", side_effect=_fake_request_json),
        patch.object(client, "_post_tabular", side_effect=_fake_post_tabular),
    ):
        result = await client.fetch_sales(session_with_tokens, since=since, until=until)

    assert len(result.orders) == 1
    order = result.orders[0]
    assert order.external_order_id == "FG4FNN2BSMILKZA"
    assert order.commission_amount is None
    assert order.statement_id is None
    assert len(order.items) == 1
    assert len(order.items[0].modifiers) == 3


@pytest.mark.asyncio
async def test_fetch_sales_date_filter_excludes_out_of_window_oms_orders(
    session_with_tokens,
):
    """OMS orders whose createdAt is outside the since/until window are excluded."""
    client = NoonClient()
    since = datetime(2026, 4, 22, 0, 0)
    until = datetime(2026, 4, 22, 23, 59)

    # Both OMS orders have createdAt on April 20-21, outside the April 22 window.
    async def _fake_request_json(session, method, url, **kwargs):
        return {
            "status": "success",
            "data": {
                "pages": 1,
                "orders": [_OMS_ORDER_SINGLE_MOD, _OMS_ORDER_MULTI_MOD],
            },
        }

    async def _fake_post_tabular(session, url, json_body):
        return _rows_from_csv(_WALLET_CSV_WITH_STATEMENT) if "wallet" in url else []

    with (
        patch.object(client, "request_json", side_effect=_fake_request_json),
        patch.object(client, "_post_tabular", side_effect=_fake_post_tabular),
    ):
        result = await client.fetch_sales(session_with_tokens, since=since, until=until)

    oms_ids = {o.external_order_id for o in result.orders if o.items}
    assert "FG4LNN5NPGYI0JA" not in oms_ids
    assert "FG4FNN2BSMILKZA" not in oms_ids


@pytest.mark.asyncio
async def test_fetch_sales_truncation_note_when_oms_capped(session_with_tokens):
    """When OMS reports more pages than the cap, a truncation note is returned."""
    from app.services.providers.noon_provider import _OMS_MAX_PAGES

    client = NoonClient()
    since = datetime(2026, 4, 21, 0, 0)
    until = datetime(2026, 4, 22, 0, 0)

    call_count = 0

    async def _fake_request_json(session, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "status": "success",
            "data": {
                "pages": _OMS_MAX_PAGES + 5,  # more than the cap
                "orders": [_OMS_ORDER_SINGLE_MOD],
            },
        }

    async def _fake_post_tabular(session, url, json_body):
        return _rows_from_csv(_WALLET_CSV_WITH_STATEMENT) if "wallet" in url else []

    with (
        patch.object(client, "request_json", side_effect=_fake_request_json),
        patch.object(client, "_post_tabular", side_effect=_fake_post_tabular),
    ):
        result = await client.fetch_sales(session_with_tokens, since=since, until=until)

    assert result.truncation_note is not None
    assert "capped" in result.truncation_note.lower() or "OMS" in result.truncation_note
    assert call_count == _OMS_MAX_PAGES
