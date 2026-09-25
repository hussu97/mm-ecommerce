"""Unit tests for the Noon RMS menu-write path (read-modify-write + publish).

The `menu/item/edit` endpoint takes the FULL item object, not a partial patch, so
`update_menu_item` must post the current item with only the changed fields overlaid
and then publish it. Endpoints/shape captured live from the RMS console 2026-09-05.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.aggregator import STATEMENT_GRAIN_SUMMARY
from app.services.providers.noon_provider import provider

# A menu/details item carries every field menu/item/edit needs.
_ITEM = {
    "itemCode": "I118035125A",
    "itemType": "main",
    "posSku": "FG0001",
    "image": "food/menu/M1/pk.png",
    "price": 0,
    "categoryCode": "C1",
    "position": 34,
    "modifiers": ["MD1"],
    "tags": [],
    "nutritionInfo": {"calories": "10"},
    "nameEn": "Pistachio Kunafa Brownies",
    "nameAr": "براونيز كنافة بالفستق",
    "descEn": "Old EN",
    "descAr": "Old AR",
    "isActive": True,
    "dietType": "egg",
    "itemIdentifier": "abc123",
    # a stray field menu/details returns that edit does not accept:
    "nextItemCode": "I2",
}


@pytest.mark.asyncio
async def test_update_menu_item_read_modify_write_and_publishes(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_request_json(session, method, url, **kwargs):
        calls.append((url, kwargs.get("json_body") or {}))
        return {"status": "success"}

    monkeypatch.setattr(provider, "request_json", fake_request_json)
    monkeypatch.setattr(provider, "_rms_headers", lambda session: {})

    await provider.update_menu_item(
        session=object(),
        menu_code="M1",
        item=_ITEM,
        name_ar="اسم جديد",
        description="New EN",
        description_ar="New AR",
        price=Decimal("35"),
    )

    # edit then publish
    assert calls[0][0].endswith("/menu/item/edit")
    assert calls[1][0].endswith("/menu/item/publish")

    edit = calls[0][1]
    # overlaid fields
    assert edit["nameAr"] == "اسم جديد"
    assert edit["descEn"] == "New EN"
    assert edit["descAr"] == "New AR"
    assert edit["price"] == Decimal("35")
    # untouched fields carried through from the current item
    assert edit["nameEn"] == "Pistachio Kunafa Brownies"
    assert edit["categoryCode"] == "C1"
    assert edit["menuCode"] == "M1"
    # a field edit does not accept is dropped
    assert "nextItemCode" not in edit

    publish = calls[1][1]
    assert publish == {"menuCode": "M1", "itemCode": "I118035125A"}


@pytest.mark.asyncio
async def test_update_menu_item_can_skip_publish(monkeypatch):
    calls: list[str] = []

    async def fake_request_json(session, method, url, **kwargs):
        calls.append(url)
        return {"status": "success"}

    monkeypatch.setattr(provider, "request_json", fake_request_json)
    monkeypatch.setattr(provider, "_rms_headers", lambda session: {})
    await provider.update_menu_item(
        session=object(), menu_code="M1", item=_ITEM, price=Decimal("40"), publish=False
    )
    assert len(calls) == 1 and calls[0].endswith("/menu/item/edit")


# A statement's Tax Invoice overview: the per-order fees summed plus the
# STATEMENT-level monthly "Platform fee" and "Long distance fee". Shape and
# figures are from the live NOON_R_R596728064_AED_20260831 invoice (2026-09-25).
_OVERVIEW = {
    "status": "success",
    "data": {
        "statementNr": "NOON_R_R1_AED_20260831",
        "periodStart": "2026-08-24",
        "periodEnd": "2026-08-31",
        "currencyCode": "AED",
        "lines": [
            {
                "feeName": "Order Value",
                "priceExclVat": 9945.0,
                "vatAmount": 0.0,
                "priceInclVat": 9945.0,
            },
            {
                "feeName": "Lead generation fee",
                "priceExclVat": -2495.0,
                "vatAmount": 124.75,
                "priceInclVat": -2619.75,
            },
            {
                "feeName": "Long distance fee",
                "priceExclVat": -104.0,
                "vatAmount": 5.2,
                "priceInclVat": -109.2,
            },
            {
                "feeName": "Payment fee",
                "priceExclVat": -199.6,
                "vatAmount": 9.98,
                "priceInclVat": -209.58,
            },
            {
                "feeName": "Platform fee",
                "priceExclVat": -149.0,
                "vatAmount": 7.45,
                "priceInclVat": -156.45,
            },
        ],
    },
}


def _serve(monkeypatch, payload):
    async def fake_request_json(session, method, url, **kwargs):
        assert method == "GET"
        return payload

    monkeypatch.setattr(provider, "request_json", fake_request_json)
    monkeypatch.setattr(provider, "_rms_headers", lambda session: {})


@pytest.mark.asyncio
async def test_overview_books_only_the_statement_level_fees(monkeypatch):
    """Only the platform and long-distance fees become summary lines. The
    commission ("Lead generation fee") and the payment fee are the order rows
    summed, and every order already carries its share, so they are not booked
    again. Booking them is what made them look like period charges. Amounts are
    VAT-INCLUSIVE, with no order id. Fee names match case-insensitively (noon has
    served "Long Distance Fee" and "Long distance fee")."""
    _serve(monkeypatch, _OVERVIEW)

    lines = await provider._overview_summary_lines(object(), "NOON_R_R1_AED_20260831")

    by_cat = {ln.fee_category: ln for ln in lines}
    assert set(by_cat) == {"platform_fee", "long_distance_fee"}

    plat = by_cat["platform_fee"]
    assert plat.line_type == "fee"
    assert plat.external_order_id is None
    assert plat.grain == STATEMENT_GRAIN_SUMMARY
    assert plat.amount == Decimal("-156.45")  # priceInclVat, signed as booked
    assert plat.line_date == "2026-08-31"
    assert plat.source_key == "noon:NOON_R_R1_AED_20260831:platform_fee"
    assert by_cat["long_distance_fee"].amount == Decimal("-109.2")


@pytest.mark.asyncio
async def test_overview_warns_when_an_order_level_fee_is_not_on_the_orders(
    monkeypatch, caplog
):
    """The order-level invoice lines are checked against the statement's order
    rows (VAT-exclusive), so a fee noon bills beyond what it itemises per order is
    logged instead of silently dropped."""
    _serve(monkeypatch, _OVERVIEW)
    rows = [
        {"lead_generation_fee": "-2495.0", "payment_fee": "-199.6"},
    ]
    with caplog.at_level("WARNING"):
        await provider._overview_summary_lines(object(), "S1", rows)
    assert "Payment fee" not in caplog.text  # ties out exactly

    rows = [{"lead_generation_fee": "-2495.0", "payment_fee": "-150.0"}]
    with caplog.at_level("WARNING"):
        await provider._overview_summary_lines(object(), "S1", rows)
    assert "'Payment fee'" in caplog.text


@pytest.mark.asyncio
async def test_overview_warns_on_an_unknown_fee_name(monkeypatch, caplog):
    payload = {
        "data": {
            "periodEnd": "2026-09-15",
            "lines": [{"feeName": "Marketing fee", "priceInclVat": -50.0}],
        }
    }
    _serve(monkeypatch, payload)
    with caplog.at_level("WARNING"):
        assert await provider._overview_summary_lines(object(), "S1") == []
    assert "unknown tax-invoice fee 'Marketing fee'" in caplog.text


@pytest.mark.asyncio
async def test_overview_summary_lines_empty_when_only_order_level_fees(monkeypatch):
    """A weekly statement carries only per-order fees, so it yields no summary
    lines."""
    payload = {
        "data": {
            "periodEnd": "2026-09-15",
            "lines": [
                {"feeName": "Order Value", "priceInclVat": 100.0},
                {"feeName": "Lead generation fee", "priceInclVat": -25.0},
                {"feeName": "Payment fee", "priceInclVat": -2.1},
                {"feeName": "Cancellation fee", "priceInclVat": -0.84},
            ],
        }
    }
    _serve(monkeypatch, payload)
    assert await provider._overview_summary_lines(object(), "S1") == []


# A live RMS `statement/orders` row (NOON_R_R596728064_AED_20260907): an outlet
# cancellation after acceptance, so noon still bills commission 10.00, payment
# 0.80 and cancellation 0.80 ex-VAT. `total_vat` 0.58 is 5% of those fees
# (11.60), not the sale's VAT.
_RMS_ROW = {
    "order_nr": "FG96NNWXZT6J5DA",
    "order_date": "2026-09-06",
    "order_status": "canceled",
    "item_value": "40.0",
    "outlet_adj": "-40.0",
    "order_value": "0.00",
    "net_payable": "-12.18",
    "payment_fee": "-0.8",
    "delivery_fee": "0.0",
    "cancellation_fee": "-0.8",
    "lead_generation_fee": "-10.0",
    "fees_exc_vat": "-11.6",
    "total_vat": "-0.58",
    "fees_inc_vat": "-12.18",
    "discount_service_fee": "0",
    "long_distance_fee_mp": "0",
    "delivery_discount_fee": "0",
}


def test_statement_lines_itemise_every_per_order_fee_vat_inclusive():
    """Each merchant fee on the order row becomes its own order-grain line,
    VAT-inclusive and signed as booked (negative). Together they equal
    `fees_inc_vat`, so the Tax Invoice's payment and cancellation fees are on the
    order. The VAT is inside the fee lines, so `total_vat` is not booked as
    well."""
    lines = provider._statement_lines_from_order_row("STMT", _RMS_ROW)
    by_cat = {ln.fee_category: ln.amount for ln in lines if ln.line_type == "fee"}
    assert by_cat == {
        "commission": Decimal("-10.50"),
        "payment_fee": Decimal("-0.84"),
        "cancellation_fee": Decimal("-0.84"),
    }
    assert sum(by_cat.values()) == Decimal("-12.18")  # == fees_inc_vat
    assert all(ln.external_order_id == "FG96NNWXZT6J5DA" for ln in lines)
    assert all(ln.line_type != "vat" for ln in lines)


def test_statement_lines_keep_a_zero_payment_and_cancellation_fee():
    """A zero payment/cancellation fee is still booked, because the settlement
    back-fill makes the statement's figure the order's and 0 has to be able to
    clear a wrong provisional value. Zero fees that are never billed per order
    (long distance, …) are not booked."""
    row = {**_RMS_ROW, "cancellation_fee": "0.0", "fees_exc_vat": "-10.8"}
    cats = {
        ln.fee_category: ln.amount
        for ln in provider._statement_lines_from_order_row("STMT", row)
    }
    assert cats["cancellation_fee"] == Decimal("0")
    assert "long_distance_fee" not in cats


def test_statement_lines_book_a_per_order_long_distance_fee():
    """If noon starts billing the long-distance fee per order, it gets its own
    line instead of vanishing inside `_commission_from`'s subtraction."""
    row = {**_RMS_ROW, "long_distance_fee_mp": "-4.0", "fees_exc_vat": "-15.6"}
    cats = {
        ln.fee_category: ln.amount
        for ln in provider._statement_lines_from_order_row("STMT", row)
    }
    assert cats["long_distance_fee"] == Decimal("-4.20")
    assert cats["commission"] == Decimal("-10.50")  # unchanged
