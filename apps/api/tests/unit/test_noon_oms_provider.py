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
