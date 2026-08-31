"""Unit tests for Keeta structured modifiers and finance parse/ingest.

Tests are pure-Python (no DB, no httpx, no browser) so they run as part of the
fast unit suite. Three concern areas:

1. `expand_modifiers` handles Keeta's various modifier/attribute shapes and
   correctly propagates qty from quantity/qty/count keys.
2. `keeta_provider.parse_orders` populates `StandardOrderItem.modifiers` with
   `StandardModifier` entries (not just `modifiers_text`).
3. `keeta_provider.parse_finance` parses statement + payout rows from a
   fixture payload and returns `FinanceResult`; returns a truncation_note when
   the payload carries no settled rows.
4. `ingest.ingest_keeta_finance_payloads` calls `_upsert_statement` and
   `_upsert_payout` the right number of times (DB mocked).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.aggregators.modifiers import expand_modifiers
from app.services.providers.keeta_provider import provider as keeta

# ── 1. expand_modifiers with Keeta shapes ────────────────────────────────────


def test_expand_modifiers_list_of_option_dicts_with_quantity():
    raw = [
        {"name": "Extra Sauce", "quantity": 2, "price": 50},
        {"name": "No Onion", "qty": 1},
    ]
    mods = expand_modifiers(raw)
    assert len(mods) == 2
    assert mods[0].name == "Extra Sauce"
    assert mods[0].quantity == Decimal("2")
    assert mods[0].unit_price == Decimal("50")
    assert mods[1].name == "No Onion"
    assert mods[1].quantity == Decimal("1")


def test_expand_modifiers_count_key_used_as_qty():
    raw = [{"name": "Extra Cheese", "count": 3, "unitPrice": 25}]
    mods = expand_modifiers(raw)
    assert mods[0].quantity == Decimal("3")


def test_expand_modifiers_attribute_list_no_qty_defaults_to_1():
    raw = [{"name": "Gluten Free Base"}, {"name": "Vegan Option"}]
    mods = expand_modifiers(raw)
    assert len(mods) == 2
    assert all(m.quantity == Decimal("1") for m in mods)


def test_expand_modifiers_empty_returns_empty():
    assert expand_modifiers(None) == []
    assert expand_modifiers([]) == []
    assert expand_modifiers("") == []


def test_expand_modifiers_bare_string_names():
    raw = ["Sugar", "Extra Shot"]
    mods = expand_modifiers(raw)
    assert [m.name for m in mods] == ["Sugar", "Extra Shot"]
    assert all(m.quantity == Decimal("1") for m in mods)


def test_expand_modifiers_external_ref_populated_from_id():
    raw = [{"name": "Spicy Sauce", "id": "mod-99", "qty": 2}]
    mods = expand_modifiers(raw)
    assert mods[0].external_ref == "mod-99"
    assert mods[0].quantity == Decimal("2")


# ── 2. parse_orders populates StandardOrderItem.modifiers ────────────────────

_ORDER_WITH_MODIFIERS = {
    "code": 0,
    "data": {
        "totalCount": 1,
        "list": [
            {
                "baseOrder": {
                    "orderViewId": "ORD-001",
                    "status": "completed",
                    "orderCreateTime": 1_723_363_200_000,
                },
                "merchantOrder": {"shopId": "shop-1", "orderAmount": 5500},
                "products": [
                    {
                        "name": "Burger",
                        "count": 1,
                        "price": 5500,
                        "skuId": "sku-burger",
                        "modifiers": [
                            {"name": "Extra Pickles", "quantity": 2, "price": 0},
                            {"name": "No Lettuce", "qty": 1},
                        ],
                    }
                ],
                "feeDtl": {"merchantFee": {"commission": 825, "total": 4675}},
            }
        ],
    },
}

_ORDER_WITH_ATTRIBUTES = {
    "code": 0,
    "data": {
        "totalCount": 1,
        "list": [
            {
                "baseOrder": {
                    "orderViewId": "ORD-002",
                    "status": "completed",
                    "orderCreateTime": 1_723_363_200_000,
                },
                "merchantOrder": {"shopId": "shop-1", "orderAmount": 3000},
                "products": [
                    {
                        "name": "Pizza",
                        "count": 1,
                        "price": 3000,
                        "skuId": "sku-pizza",
                        "attributes": [
                            {"name": "Thin Crust", "count": 1},
                            {"name": "Extra Cheese", "count": 2, "price": 500},
                        ],
                    }
                ],
                "feeDtl": {"merchantFee": {"commission": 450, "total": 2550}},
            }
        ],
    },
}


def test_parse_orders_item_has_structured_modifiers():
    orders = keeta.parse_orders(_ORDER_WITH_MODIFIERS)
    assert orders, "should parse one order"
    item = orders[0].items[0]
    assert item.item_name == "Burger"
    assert len(item.modifiers) == 2
    names = [m.name for m in item.modifiers]
    assert "Extra Pickles" in names
    assert "No Lettuce" in names
    pickles = next(m for m in item.modifiers if m.name == "Extra Pickles")
    assert pickles.quantity == Decimal("2")
    # modifiers_text still present for debug
    assert item.modifiers_text is not None
    assert "Extra Pickles" in item.modifiers_text


def test_parse_orders_attributes_become_modifiers():
    orders = keeta.parse_orders(_ORDER_WITH_ATTRIBUTES)
    item = orders[0].items[0]
    assert len(item.modifiers) == 2
    cheese = next(m for m in item.modifiers if m.name == "Extra Cheese")
    assert cheese.quantity == Decimal("2")


def test_parse_orders_item_no_modifiers_gives_empty_list():
    payload = {
        "code": 0,
        "data": {
            "totalCount": 1,
            "list": [
                {
                    "baseOrder": {
                        "orderViewId": "ORD-003",
                        "status": "completed",
                        "orderCreateTime": 1_723_363_200_000,
                    },
                    "merchantOrder": {"shopId": "shop-1", "orderAmount": 2000},
                    "products": [
                        {"name": "Water", "count": 1, "price": 2000, "skuId": "sku-w"}
                    ],
                    "feeDtl": {"merchantFee": {"commission": 300, "total": 1700}},
                }
            ],
        },
    }
    orders = keeta.parse_orders(payload)
    item = orders[0].items[0]
    assert item.modifiers == []
    assert item.modifiers_text is None


# ── 2b. real getOrders envelope: customer + numeric status decode ────────────
# Trimmed from apps/aggregator-bootstrap/.aggregator-sessions/keeta-audit/
# orders_sample.json (order data.list[0]) — real field spellings: baseOrder with
# numeric `status`/`openPrivacyNumber`, envelope-level `recipientInfo`/`userInfo`,
# and a `products[]` line carrying empty `groups`/`spuPvList` (as every real line
# in the sample does).
_REAL_ORDER_ENVELOPE = {
    "code": 0,
    "message": "success",
    "data": {
        "pageNum": 1,
        "totalCount": 1,
        "list": [
            {
                "baseOrder": {
                    "orderViewId": 5047843723786410,
                    "orderViewIdStr": "5047843723786410",
                    "status": 30,
                    "ctime": 1787821408351,
                    "currency": "AED",
                    "openPrivacyNumber": 0,
                },
                "merchantOrder": {
                    "orderViewIdStr": "5047843723786410",
                    "shopId": 1644336388,
                    "shopName": "Melting Moments",
                    "status": 30,
                },
                "products": [
                    {
                        "spuId": 99583665,
                        "skuId": 113520023,
                        "count": 1,
                        "price": 4000,
                        "name": "Nutella Cookie Melt (250 grams)",
                        "currency": "AED",
                        "spuPvList": [],
                        "groups": [],
                        "priceWithGroup": {"amount": 4000, "unitPrice": 4000},
                    }
                ],
                "recipientInfo": {
                    "name": "J4P773781744",
                    "phone": "521461759",
                    "interCode": "971",
                    "privacyPhone": "",
                },
                "userInfo": {
                    "userName": "***",
                    "userPhone": "52*****59",
                    "phone": "971******1759",
                    "interCode": "971",
                },
                "feeDtl": {"merchantFee": {"commission": 900, "total": 2620}},
            }
        ],
    },
}


def test_parse_orders_real_envelope_decodes_numeric_status():
    orders = keeta.parse_orders(_REAL_ORDER_ENVELOPE)
    assert len(orders) == 1
    # baseOrder.status 30 → confirmed (lifecycle 30 per merchantOrderTraces).
    assert orders[0].status == "confirmed"


def test_parse_orders_real_envelope_populates_customer():
    order = keeta.parse_orders(_REAL_ORDER_ENVELOPE)[0]
    # recipientInfo.name is the recipient identity in the real payload.
    assert order.customer_name == "J4P773781744"
    # recipient phone prefixed with its interCode when unmasked.
    assert order.customer_phone == "+971521461759"


def test_parse_orders_real_envelope_empty_option_arrays_no_modifiers():
    item = keeta.parse_orders(_REAL_ORDER_ENVELOPE)[0].items[0]
    assert item.item_name == "Nutella Cookie Melt (250 grams)"
    # groups[]/spuPvList[] are empty in the sample → no invented modifiers.
    assert item.modifiers == []
    assert item.modifiers_text is None


def test_parse_orders_maps_merchant_funded_promotion_to_marketing_fee():
    """Keeta's feeDtl.merchantFee.activityFee ('Promotion funded by merchant') is a
    merchant cost — mapped to marketing_fee, kept distinct from commission. Shape and
    values taken from a real order (5087841884884367): item 40, commission 9,
    promotion 4, payment fee 0.80, earnings 26.20."""
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "baseOrder": {"orderViewIdStr": "5087841884884367", "status": 40},
                    "merchantOrder": {"shopId": "1644189187"},
                    "products": [
                        {"name": "Kinder Cookie Melt (250 grams)", "count": 1}
                    ],
                    "feeDtl": {
                        "merchantFee": {
                            "productPrice": 4000,
                            "commission": 900,
                            "activityFee": 400,  # merchant-funded promotion
                            "bankTransactionFee": 80,
                            "earnings": 2620,
                            "total": 2620,
                        }
                    },
                }
            ]
        },
    }
    order = keeta.parse_orders(payload)[0]
    assert order.commission_amount == Decimal("9.00")
    assert order.payment_fee == Decimal("0.80")
    assert order.marketing_fee == Decimal("4.00")  # was dropped before this fix
    assert order.net_payable == Decimal("26.20")
    # Commission + payment fee + marketing now reconcile to gross − net (13.80),
    # where before marketing (4.00) was silent and the buckets under-reported by it.
    assert (
        order.commission_amount + order.payment_fee + order.marketing_fee
    ) == Decimal("13.80")


def test_status_code_40_decodes_to_completed():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "baseOrder": {"orderViewIdStr": "ORD-40", "status": 40},
                    "merchantOrder": {"shopId": "shop-1", "orderAmount": 4000},
                    "products": [{"name": "Cookie", "count": 1, "price": 4000}],
                }
            ]
        },
    }
    assert keeta.parse_orders(payload)[0].status == "completed"


def test_unknown_numeric_status_falls_back_to_raw():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "baseOrder": {"orderViewIdStr": "ORD-99", "status": 99},
                    "merchantOrder": {"shopId": "shop-1", "orderAmount": 4000},
                    "products": [{"name": "Cookie", "count": 1, "price": 4000}],
                }
            ]
        },
    }
    # 99 is not evidenced in the sample → kept as the raw normalized string.
    assert keeta.parse_orders(payload)[0].status == "99"


def test_parse_orders_masked_recipient_phone_not_intercode_prefixed():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "baseOrder": {"orderViewIdStr": "ORD-M", "status": 40},
                    "merchantOrder": {"shopId": "shop-1", "orderAmount": 4000},
                    "products": [{"name": "Cookie", "count": 1, "price": 4000}],
                    "recipientInfo": {
                        "name": "***",
                        "phone": "52*****75",
                        "interCode": "971",
                    },
                }
            ]
        },
    }
    order = keeta.parse_orders(payload)[0]
    # A masked number is stored verbatim, never fused with the interCode.
    assert order.customer_phone == "52*****75"


def test_parse_orders_structured_modifiers_from_groups():
    """Real Keeta options live under products[].groups[] (empty in the sample);
    exercise the group→leaf-option flattening with a Meituan-shaped group."""
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "baseOrder": {"orderViewIdStr": "ORD-G", "status": 40},
                    "merchantOrder": {"shopId": "shop-1", "orderAmount": 6000},
                    "products": [
                        {
                            "name": "Build-a-Box",
                            "count": 1,
                            "price": 6000,
                            "skuId": "sku-box",
                            "spuPvList": [],
                            "groups": [
                                {
                                    "name": "Add-ons",
                                    "foods": [
                                        {
                                            "name": "Extra Cookie",
                                            "count": 2,
                                            "price": 500,
                                        },
                                        {"name": "Gift Wrap", "count": 1},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    }
    item = keeta.parse_orders(payload)[0].items[0]
    names = {m.name for m in item.modifiers}
    assert names == {"Extra Cookie", "Gift Wrap"}
    extra = next(m for m in item.modifiers if m.name == "Extra Cookie")
    assert extra.quantity == Decimal("2")
    assert item.modifiers_text is not None


# ── 2c. status history (merchantOrderTraces) + customer address ──────────────
# The trace list and recipientInfo below are transcribed from the real
# orders_sample.json order data.list[0]: three lifecycle steps (10 submitted →
# 20 pending → 30 confirmed) at real epoch-ms opTimes, deliberately given
# newest-first (as the portal returns them) so the ascending re-order is tested;
# and the real recipientInfo with the full Business Bay address string, an empty
# buildingNumber (dropped) and a trailing-space unitNumber (trimmed).
_ORDER_WITH_TRACES = {
    "code": 0,
    "data": {
        "list": [
            {
                "baseOrder": {
                    "orderViewIdStr": "5047843723786410",
                    "status": 30,
                    "ctime": 1787821408351,
                    "currency": "AED",
                },
                "merchantOrder": {
                    "shopId": 1644336388,
                    "status": 30,
                    "unconfirmedStatusTime": 1787821419753,
                    "confirmedStatusTime": 1787821524298,
                    "merchantOrderTraces": [
                        {"merchantOrderStatus": 30, "opTime": 1787821524298},
                        {"merchantOrderStatus": 20, "opTime": 1787821419761},
                        {"merchantOrderStatus": 10, "opTime": 1787821408348},
                    ],
                },
                "products": [
                    {"name": "Nutella Cookie Melt", "count": 1, "price": 4000}
                ],
                "recipientInfo": {
                    "name": "J4P773781744",
                    "phone": "521461759",
                    "interCode": "971",
                    "addressName": (
                        "U Bora Towers - Commercial Tower, Marasi Drive, "
                        "Business Bay, Dubai, United Arab Emirates"
                    ),
                    "addressLocation": (
                        "U Bora Towers - Commercial Tower, Marasi Drive, "
                        "Business Bay, Dubai, United Arab Emirates"
                    ),
                    "houseNumber": "36 , Multibank",
                    "unitNumber": "36 ",
                    "buildingNumber": "",
                },
            }
        ]
    },
}


def test_status_events_built_from_traces_in_optime_order():
    order = keeta.parse_orders(_ORDER_WITH_TRACES)[0]
    events = order.status_events
    # Three trace steps, one per status, re-ordered oldest → newest by opTime.
    assert [e.status for e in events] == ["submitted", "pending", "confirmed"]
    assert [e.sequence for e in events] == [1, 2, 3]
    # opTimes are epoch ms resolved to NAIVE Dubai wall-clock, strictly ascending.
    ats = [e.at for e in events]
    assert all(isinstance(at, _dt) for at in ats)
    assert all(at.tzinfo is None for at in ats)
    assert ats[0] < ats[1] < ats[2]
    # 1787821408348 ms → 2026-08-08 in Dubai (UTC+4).
    assert ats[0].year == 2026
    # The source trace dict is retained for audit.
    assert events[0].raw["merchantOrderStatus"] == 10


def test_customer_address_from_recipient_info():
    order = keeta.parse_orders(_ORDER_WITH_TRACES)[0]
    addr = order.customer_address
    assert addr is not None
    assert "Business Bay" in addr["address"]
    assert addr["house"] == "36 , Multibank"
    # Trailing space trimmed; empty buildingNumber dropped entirely.
    assert addr["unit"] == "36"
    assert "building" not in addr
    # Existing customer fields are untouched.
    assert order.customer_name == "J4P773781744"
    assert order.customer_phone == "+971521461759"


def test_status_events_absent_traces_gives_empty_list():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "baseOrder": {"orderViewIdStr": "ORD-NT", "status": 40},
                    "merchantOrder": {"shopId": "shop-1", "orderAmount": 4000},
                    "products": [{"name": "Cookie", "count": 1, "price": 4000}],
                }
            ]
        },
    }
    order = keeta.parse_orders(payload)[0]
    assert order.status_events == []


def test_customer_address_none_when_recipient_absent():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "baseOrder": {"orderViewIdStr": "ORD-NA", "status": 40},
                    "merchantOrder": {"shopId": "shop-1", "orderAmount": 4000},
                    "products": [{"name": "Cookie", "count": 1, "price": 4000}],
                }
            ]
        },
    }
    assert keeta.parse_orders(payload)[0].customer_address is None


import pathlib as _pathlib_sample  # noqa: E402

_REAL_SAMPLE_PATH = _pathlib_sample.Path(
    "/Users/hussainabbasi/Documents/GitHub/mm-apps/mm-ecommerce/"
    "apps/aggregator-bootstrap/.aggregator-sessions/keeta-audit/orders_sample.json"
)


@pytest.mark.skipif(
    not _REAL_SAMPLE_PATH.exists(), reason="real orders_sample.json not present"
)
def test_real_sample_status_events_and_address():
    import json as _json

    payload = _json.loads(_REAL_SAMPLE_PATH.read_text())
    orders = keeta.parse_orders(payload)
    assert orders, "sample should parse at least one order"
    target = orders[0]
    statuses = [e.status for e in target.status_events]
    # The sample's live order records 10 → 20 → 30 (submitted/pending/confirmed).
    assert "submitted" in statuses
    assert "pending" in statuses
    assert "confirmed" in statuses
    # Strictly ascending, sequenced 1..n.
    assert [e.sequence for e in target.status_events] == list(
        range(1, len(target.status_events) + 1)
    )
    assert target.customer_address is not None
    assert "address" in target.customer_address


# ── 3. parse_finance ─────────────────────────────────────────────────────────

_FINANCE_PAYLOAD_WITH_ROWS = {
    "code": 0,
    "data": {
        "list": [
            {
                "statementId": "STMT-2024-08",
                "settleAmount": 12500,
                "startDate": "2024-08-01",
                "endDate": "2024-08-31",
                "orderAmount": 15000,
                "feeAmount": 2250,
                "currency": "AED",
                "transferId": "PAY-001",
                "payDate": "2024-09-05",
                "paymentStatus": "paid",
            }
        ]
    },
}

_FINANCE_PAYLOAD_TASK_ONLY = {
    "code": 0,
    "data": {
        "taskList": [
            {"taskId": "TASK-001", "status": "generated", "downloadUrl": "/pdf/001.pdf"}
        ]
    },
}


def test_parse_finance_extracts_statement_and_payout():
    result = keeta.parse_finance(_FINANCE_PAYLOAD_WITH_ROWS)
    assert len(result.statements) == 1
    stmt = result.statements[0]
    assert stmt.statement_id == "STMT-2024-08"
    assert stmt.period_start == "2024-08-01"
    assert stmt.period_end == "2024-08-31"
    # payout on the same row
    assert len(result.payouts) == 1
    payout = result.payouts[0]
    assert payout.transfer_id == "PAY-001"
    assert result.truncation_note is None


def test_parse_finance_empty_payload_sets_truncation_note():
    result = keeta.parse_finance(_FINANCE_PAYLOAD_TASK_ONLY)
    assert result.statements == []
    assert result.payouts == []
    assert result.truncation_note is not None
    assert (
        "PDF" in result.truncation_note or "invoice" in result.truncation_note.lower()
    )


def test_parse_finance_empty_dict_sets_truncation_note():
    result = keeta.parse_finance({})
    assert result.statements == []
    assert result.payouts == []
    assert result.truncation_note


def test_parse_finance_deduplicates_on_statement_id():
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "statementId": "STMT-DUP",
                    "settleAmount": 5000,
                    "startDate": "2024-07-01",
                    "endDate": "2024-07-31",
                },
                {
                    "statementId": "STMT-DUP",
                    "settleAmount": 5000,
                    "startDate": "2024-07-01",
                    "endDate": "2024-07-31",
                },
            ]
        },
    }
    result = keeta.parse_finance(payload)
    assert len(result.statements) == 1


# ── 4. ingest_keeta_finance_payloads (DB mocked) ─────────────────────────────


@pytest.mark.asyncio
async def test_ingest_keeta_finance_payloads_calls_upserts():
    """ingest_keeta_finance_payloads calls _upsert_statement and _upsert_payout
    for each parsed row, and returns correct (statements, payouts) counts."""
    from app.services.aggregators import ingest

    mock_db = MagicMock()

    with (
        patch.object(ingest, "_upsert_statement", new_callable=AsyncMock) as mock_stmt,
        patch.object(ingest, "_upsert_payout", new_callable=AsyncMock) as mock_payout,
    ):
        stmts, pays = await ingest.ingest_keeta_finance_payloads(
            mock_db, [_FINANCE_PAYLOAD_WITH_ROWS]
        )

    assert stmts == 1
    assert pays == 1
    assert mock_stmt.call_count == 1
    assert mock_payout.call_count == 1


@pytest.mark.asyncio
async def test_ingest_keeta_finance_payloads_skips_bad_payload():
    """A payload that raises during parse does not abort the batch."""
    from app.services.aggregators import ingest

    mock_db = MagicMock()
    bad_payload = "not a dict"  # type: ignore[assignment]

    with (
        patch.object(ingest, "_upsert_statement", new_callable=AsyncMock),
        patch.object(ingest, "_upsert_payout", new_callable=AsyncMock),
    ):
        stmts, pays = await ingest.ingest_keeta_finance_payloads(
            mock_db,
            [bad_payload, _FINANCE_PAYLOAD_WITH_ROWS],  # type: ignore[list-item]
        )

    # bad payload skipped; good payload processed
    assert stmts == 1
    assert pays == 1


@pytest.mark.asyncio
async def test_ingest_keeta_finance_payloads_truncation_only_returns_zeros():
    """Task-only payloads (no settled rows) return (0, 0) — not an error."""
    from app.services.aggregators import ingest

    mock_db = MagicMock()

    with (
        patch.object(ingest, "_upsert_statement", new_callable=AsyncMock) as mock_stmt,
        patch.object(ingest, "_upsert_payout", new_callable=AsyncMock) as mock_payout,
    ):
        stmts, pays = await ingest.ingest_keeta_finance_payloads(
            mock_db, [_FINANCE_PAYLOAD_TASK_ONLY]
        )

    assert stmts == 0
    assert pays == 0
    assert mock_stmt.call_count == 0
    assert mock_payout.call_count == 0


# ── 5. finance FILE payloads (real bill XLSX + commission ZIP) ────────────────
# These exercise the endpoint→file path the bootstrap worker feeds: the weekly
# billing-report XLSX becomes per-order statement lines, and the monthly
# commission-invoice ZIP is archived onto the statement. The XLSX is built with
# the REAL "Order Summary" column layout and real sample values (rows 4–5 of the
# downloaded bill.xlsx), so the column mapping is asserted against genuine data.
import base64 as _base64  # noqa: E402
import io as _io  # noqa: E402
import zipfile as _zipfile  # noqa: E402
from datetime import datetime as _dt  # noqa: E402
from datetime import timezone as _tz  # noqa: E402

# 1-indexed column → value, transcribed from the real bill.xlsx "Order Summary".
_REAL_BILL_ROWS = [
    {
        4: "1644189187",
        6: "15 Aug 2026",
        7: "2026.08.15~2026.08.21",
        9: "4927840114700030",
        10: "Completed",
        16: 40.0,  # Original item price (gross, AED major units)
        21: -9.0,  # Subtotal of commission fee
        22: -0.8,  # Bank fee
        33: 26.2,  # Payable to Restaurant (net)
        35: -9.0,  # Total Commission
        36: "25%",
    },
    {
        4: "1644189187",
        6: "16 Aug 2026",
        9: "4927840540851974",
        10: "Completed",
        16: 40.0,
        22: -0.64,
        33: 26.36,
        # col 35 (Total Commission) intentionally left blank to assert that a
        # missing money cell yields no fabricated commission line.
    },
]


def _build_bill_xlsx_b64() -> str:
    """A minimal 'Order Summary' workbook with header on row 3, data from row 4."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Order Summary"
    # Rows 1–3 are titles/headers in the real file; only their presence matters.
    ws.append(["Order information"])
    ws.append([])
    ws.append(["Brand Name"])  # header row 3
    for spec in _REAL_BILL_ROWS:
        values = [None] * 40
        for col, value in spec.items():
            values[col - 1] = value
        ws.append(values)
    buffer = _io.BytesIO()
    wb.save(buffer)
    return _base64.b64encode(buffer.getvalue()).decode("ascii")


def test_parse_finance_bill_xlsx_yields_statement_with_outlet_and_period():
    payload = {
        "statement_id": "DT2091796450566606888",
        "taskViewId": "DT2091796450566606888",
        "shopId": "1644189187",
        "displayTimeText": "15 Aug 2026 ~ 22 Aug 2026",
        "fileScene": "Billing report - Restaurant[1644189187]",
        "bill_xlsx_b64": _build_bill_xlsx_b64(),
    }
    result = keeta.parse_finance(payload)

    assert result.truncation_note is None
    assert len(result.statements) == 1
    stmt = result.statements[0]
    assert stmt.statement_id == "DT2091796450566606888"
    assert stmt.external_outlet_id == "1644189187"
    assert stmt.period_start == "2026-08-15"
    assert stmt.period_end == "2026-08-22"
    assert stmt.currency == "AED"
    # The bytes are stripped out of the archived raw JSONB.
    assert "bill_xlsx_b64" not in (stmt.raw or {})


def test_parse_finance_bill_xlsx_lines_map_real_columns():
    payload = {
        "statement_id": "DT2091796450566606888",
        "shopId": "1644189187",
        "displayTimeText": "15 Aug 2026 ~ 22 Aug 2026",
        "bill_xlsx_b64": _build_bill_xlsx_b64(),
    }
    stmt = keeta.parse_finance(payload).statements[0]

    lines = {(ln.external_order_id, ln.fee_category): ln for ln in stmt.lines}

    # First order: all four money columns present → four distinct lines.
    order1 = "4927840114700030"
    assert lines[(order1, "gross_sales")].amount == Decimal("40.0")
    assert lines[(order1, "commission")].amount == Decimal("-9.0")
    assert lines[(order1, "bank_fee")].amount == Decimal("-0.8")
    assert lines[(order1, "net_payable")].amount == Decimal("26.2")
    # external_order_id is the join key to sales; line_date from Transaction date.
    assert lines[(order1, "gross_sales")].external_order_id == order1
    assert lines[(order1, "gross_sales")].line_date == "2026-08-15"
    assert lines[(order1, "gross_sales")].currency == "AED"

    # Second order carries no commission column → no commission line fabricated.
    order2 = "4927840540851974"
    assert (order2, "gross_sales") in lines
    assert lines[(order2, "bank_fee")].amount == Decimal("-0.64")
    assert lines[(order2, "net_payable")].amount == Decimal("26.36")
    assert (order2, "commission") not in lines


def test_parse_finance_commission_zip_is_archived_and_stamped():
    from app.services.aggregators.statement_docs import StoredStatementInvoice

    zip_buffer = _io.BytesIO()
    with _zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("by_client-invoice-clientId[330066]-202607.pdf", b"%PDF-1.4 fake")
    zip_b64 = _base64.b64encode(zip_buffer.getvalue()).decode("ascii")

    payload = {
        "statement_id": "KEETA_COMMISSION_202607",
        "time": 202607,
        "fileScene": "Commission invoice",
        "invoice_zip_b64": zip_b64,
    }

    stored = StoredStatementInvoice(
        object_key="aggregator-statements/keeta/KEETA_COMMISSION_202607/inv.zip",
        content_type="application/zip",
        original_filename="KEETA_COMMISSION_202607.zip",
        fetched_at=_dt(2026, 7, 1, tzinfo=_tz.utc),
        size_bytes=42,
    )
    with patch(
        "app.services.aggregators.statement_docs.store_statement_invoice",
        return_value=stored,
    ) as mock_store:
        result = keeta.parse_finance(payload)

    assert mock_store.call_count == 1
    assert len(result.statements) == 1
    stmt = result.statements[0]
    assert stmt.statement_id == "KEETA_COMMISSION_202607"
    # Period synthesised from the YYYYMM `time`.
    assert stmt.period_start == "2026-07-01"
    assert stmt.period_end == "2026-07-31"
    assert stmt.invoice_object_key == stored.object_key
    assert stmt.invoice_content_type == "application/zip"
    assert stmt.invoice_fetched_at == stored.fetched_at


# ── 6. bill xlsx → weekly settlement PAYOUT (the "Invoice Details" sheet) ──────
# The weekly TOTAL Keeta actually transfers lives in the bill's "Invoice Details"
# sheet (net "Payable to Restaurant" per billing cycle), which the per-order
# "Order Summary" parser never sums. These tests build the payload from the REAL
# downloaded bill.xlsx bytes so the summed total, cycle-end date, status and the
# stable transfer_id are asserted against genuine data.
import pathlib as _pathlib  # noqa: E402

_REAL_BILL_PATH = _pathlib.Path(
    "/private/tmp/claude-502/"
    "-Users-hussainabbasi-Documents-GitHub-mm-apps-mm-ecommerce/"
    "fe338c4b-c9bb-4ec6-b7d2-d92403ab3dcd/scratchpad/bill.xlsx"
)


def _real_bill_xlsx_b64() -> str:
    return _base64.b64encode(_REAL_BILL_PATH.read_bytes()).decode("ascii")


def _real_bill_payload() -> dict:
    return {
        "statement_id": "DT2091796450566606888",
        "taskViewId": "DT2091796450566606888",
        "shopId": "1644189187",
        "displayTimeText": "15 Aug 2026 ~ 22 Aug 2026",
        "fileScene": "Billing report - Restaurant[1644189187]",
        "bill_xlsx_b64": _real_bill_xlsx_b64(),
    }


@pytest.mark.skipif(
    not _REAL_BILL_PATH.exists(), reason="real bill.xlsx fixture not present"
)
def test_parse_finance_bill_xlsx_settled_payout_sums_payable():
    """The settled billing cycle yields one payout summing "Payable to Restaurant"."""
    result = keeta.parse_finance(_real_bill_payload())

    settled = [p for p in result.payouts if p.transfer_status == "settled"]
    assert len(settled) == 1
    payout = settled[0]
    # 247.23+74.24+79.58+78.68+154.42+79.62+127.96 over the settled cycle rows.
    assert payout.transfer_amount == Decimal("841.73")
    # Cycle end (2026.08.15~2026.08.21) drives date, due date and the id suffix.
    assert payout.transfer_date == "2026-08-21"
    assert payout.payment_due_date == "2026-08-21"
    assert payout.transfer_id == "KEETA_BILL_1644189187_2026-08-21"
    assert payout.currency == "AED"
    # taskViewId is the payment reference.
    assert payout.payment_reference == "DT2091796450566606888"


@pytest.mark.skipif(
    not _REAL_BILL_PATH.exists(), reason="real bill.xlsx fixture not present"
)
def test_parse_finance_bill_xlsx_payout_couples_to_statement():
    """Each bill payout carries the same statement_id as the weekly statement."""
    result = keeta.parse_finance(_real_bill_payload())

    assert len(result.statements) == 1
    stmt = result.statements[0]
    assert result.payouts, "expected at least one weekly payout"
    for payout in result.payouts:
        assert payout.statement_id == stmt.statement_id == "DT2091796450566606888"


@pytest.mark.skipif(
    not _REAL_BILL_PATH.exists(), reason="real bill.xlsx fixture not present"
)
def test_parse_finance_bill_xlsx_pending_cycle_is_pending_and_keyed_separately():
    """The pending billing cycle is its own payout, marked pending, not settled."""
    result = keeta.parse_finance(_real_bill_payload())

    pending = [p for p in result.payouts if p.transfer_status == "pending"]
    assert len(pending) == 1
    payout = pending[0]
    # The single "Settlement pending" row (cycle 2026.08.22~2026.08.31).
    assert payout.transfer_amount == Decimal("322.97")
    assert payout.transfer_date == "2026-08-31"
    assert payout.transfer_id == "KEETA_BILL_1644189187_2026-08-31"
    # Two distinct billing cycles → two distinct, stable transfer ids.
    transfer_ids = {p.transfer_id for p in result.payouts}
    assert len(transfer_ids) == len(result.payouts) == 2


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _REAL_BILL_PATH.exists(), reason="real bill.xlsx fixture not present"
)
async def test_ingest_keeta_bill_xlsx_upserts_statement_and_payouts():
    """The bill payload upserts one statement and one payout per billing cycle."""
    from app.services.aggregators import ingest

    mock_db = MagicMock()
    with (
        patch.object(ingest, "_upsert_statement", new_callable=AsyncMock),
        patch.object(ingest, "_upsert_payout", new_callable=AsyncMock) as mock_payout,
    ):
        stmts, pays = await ingest.ingest_keeta_finance_payloads(
            mock_db, [_real_bill_payload()]
        )

    assert stmts == 1
    assert pays == 2
    assert mock_payout.call_count == 2
