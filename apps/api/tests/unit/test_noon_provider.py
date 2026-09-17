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
async def test_overview_summary_lines_extracts_platform_fee_only(monkeypatch):
    """Only the STATEMENT-level platform fee becomes a summary line — the per-order
    fees (commission, payment) come from the order feed and must not be duplicated.
    The amount is VAT-INCLUSIVE (what the merchant pays) and carries no order id."""

    async def fake_request_json(session, method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/finance/statement/overview/NOON_R_R1_AED_20260915")
        return _OVERVIEW

    monkeypatch.setattr(provider, "request_json", fake_request_json)
    monkeypatch.setattr(provider, "_rms_headers", lambda session: {})

    lines = await provider._overview_summary_lines(object(), "NOON_R_R1_AED_20260915")

    assert [ln.fee_category for ln in lines] == ["platform_fee"]
    fee = lines[0]
    assert fee.line_type == "fee"
    assert fee.external_order_id is None
    assert fee.grain == STATEMENT_GRAIN_SUMMARY
    assert fee.amount == Decimal("-156.45")  # priceInclVat, signed as booked
    assert fee.line_date == "2026-09-15"
    assert fee.source_key == "noon:NOON_R_R1_AED_20260915:platform_fee"


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
