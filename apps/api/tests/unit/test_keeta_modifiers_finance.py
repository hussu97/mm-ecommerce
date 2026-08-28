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
