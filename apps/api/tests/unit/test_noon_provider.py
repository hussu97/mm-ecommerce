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


# A statement's Tax Invoice overview: per-order fees plus the STATEMENT-level
# "Platform fee" (noon's monthly cost of being on the platform), shape captured
# live 2026-09-17.
_OVERVIEW = {
    "status": "success",
    "data": {
        "statementNr": "NOON_R_R1_AED_20260915",
        "periodStart": "2026-09-08",
        "periodEnd": "2026-09-15",
        "currencyCode": "AED",
        "lines": [
            {
                "feeName": "Order Value",
                "priceExclVat": 7700.0,
                "vatAmount": 0.0,
                "priceInclVat": 7700.0,
            },
            {
                "feeName": "Lead generation fee",
                "priceExclVat": -1952.5,
                "vatAmount": 97.63,
                "priceInclVat": -2050.13,
            },
            {
                "feeName": "Payment fee",
                "priceExclVat": -156.2,
                "vatAmount": 7.81,
                "priceInclVat": -164.01,
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


@pytest.mark.asyncio
async def test_overview_summary_lines_extracts_merchant_fees_not_commission(
    monkeypatch,
):
    """The merchant-charged invoice lines (payment fee, platform fee, …) become
    summary lines; the commission ("Lead generation fee", captured per order) and
    gross ("Order Value") do NOT. Amounts are VAT-INCLUSIVE, with no order id."""

    async def fake_request_json(session, method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/finance/statement/overview/NOON_R_R1_AED_20260915")
        return _OVERVIEW

    monkeypatch.setattr(provider, "request_json", fake_request_json)
    monkeypatch.setattr(provider, "_rms_headers", lambda session: {})

    lines = await provider._overview_summary_lines(object(), "NOON_R_R1_AED_20260915")

    # Payment fee is captured (a real merchant charge); commission/gross are not.
    by_cat = {ln.fee_category: ln for ln in lines}
    assert set(by_cat) == {"payment_fee", "platform_fee"}
    assert "commission" not in by_cat and "gross_sales" not in by_cat

    pay = by_cat["payment_fee"]
    assert pay.line_type == "fee"
    assert pay.external_order_id is None
    assert pay.grain == STATEMENT_GRAIN_SUMMARY
    assert pay.amount == Decimal("-164.01")  # priceInclVat, signed as booked
    assert pay.source_key == "noon:NOON_R_R1_AED_20260915:payment_fee"

    plat = by_cat["platform_fee"]
    assert plat.amount == Decimal("-156.45")
    assert plat.line_date == "2026-09-15"


@pytest.mark.asyncio
async def test_overview_summary_lines_empty_when_no_platform_fee(monkeypatch):
    """A statement with only per-order fees yields no summary lines."""
    payload = {
        "data": {
            "periodEnd": "2026-09-15",
            "lines": [
                {"feeName": "Order Value", "priceInclVat": 100.0},
                {"feeName": "Lead generation fee", "priceInclVat": -25.0},
            ],
        }
    }

    async def fake_request_json(session, method, url, **kwargs):
        return payload

    monkeypatch.setattr(provider, "request_json", fake_request_json)
    monkeypatch.setattr(provider, "_rms_headers", lambda session: {})

    assert await provider._overview_summary_lines(object(), "S1") == []


def test_statement_lines_omit_sale_vat():
    """noon's `total_vat` is the customer SALE VAT (output VAT the merchant remits),
    NOT a fee — so it is not booked as a statement 'vat' line. Only net, gross and
    the (VAT-inclusive) commission are emitted; the fee VAT is derived downstream."""
    row = {
        "order_nr": "5197840000000001",
        "order_date": "2026-09-10",
        "net_payable": "26.20",
        "order_value": "40.00",
        "fees_exc_vat": "9.0",
        "total_vat": "1.90",  # sale VAT — must NOT become a fee line
    }
    lines = provider._statement_lines_from_order_row("STMT", row)
    assert all(ln.line_type != "vat" for ln in lines)
    assert all(ln.fee_category != "vat" for ln in lines)
    assert Decimal("1.90") not in {abs(ln.amount) for ln in lines}
    # commission (VAT-inclusive) is still emitted.
    assert any(ln.fee_category == "commission" for ln in lines)
