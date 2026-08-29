"""Unit tests for the Talabat provider — pure Python, no DB, no httpx.

Coverage areas:
1. `_split_balanced`            — comma-split that skips commas inside (…).
2. `_extract_item_modifiers`    — parenthetical + plus-addon extraction.
3. `_parse_items_text`          — qty/name parsing with improved splitter.
4. `TalabatClient._items_from_row` — modifier extraction from CSV rows.
5. `TalabatClient._parse_bundle_bytes` — xlsx/zip bundle parsing (in-memory fixture).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openpyxl import Workbook

from app.services.providers.talabat_provider import (
    TalabatClient,
    _extract_item_modifiers,
    _parse_items_text,
    _split_balanced,
)

# ── 1. _split_balanced ────────────────────────────────────────────────────────


def test_split_balanced_plain_csv():
    assert _split_balanced("1 Burger, 2 Fries") == ["1 Burger", "2 Fries"]


def test_split_balanced_keeps_paren_commas():
    result = _split_balanced("1 Burger (No pickle, Extra cheese), 2 Fries")
    assert result == ["1 Burger (No pickle, Extra cheese)", "2 Fries"]


def test_split_balanced_no_comma():
    assert _split_balanced("1 Burger") == ["1 Burger"]


def test_split_balanced_empty():
    assert _split_balanced("") == []


def test_split_balanced_multiple_parens():
    result = _split_balanced("1 A (x, y), 2 B (p, q), 3 C")
    assert result == ["1 A (x, y)", "2 B (p, q)", "3 C"]


# ── 2. _extract_item_modifiers ────────────────────────────────────────────────


def test_extract_item_modifiers_no_modifiers():
    name, mods = _extract_item_modifiers("Chicken Burger")
    assert name == "Chicken Burger"
    assert mods == []


def test_extract_item_modifiers_parenthetical():
    name, mods = _extract_item_modifiers("Chicken Burger (No pickle, Extra cheese)")
    assert name == "Chicken Burger"
    assert mods == ["No pickle", "Extra cheese"]


def test_extract_item_modifiers_plus_addon():
    name, mods = _extract_item_modifiers("Pizza + Extra Cheese + No Olives")
    assert name == "Pizza"
    assert mods == ["Extra Cheese", "No Olives"]


def test_extract_item_modifiers_paren_plus_combo():
    # Parenthetical extracted first, then remaining + chain
    name, mods = _extract_item_modifiers("Burger + Sauce (Special)")
    # "(Special)" is at the end of the whole token
    assert "Burger" in name
    assert len(mods) >= 1


def test_extract_item_modifiers_parenthetical_single():
    name, mods = _extract_item_modifiers("Fries (Large)")
    assert name == "Fries"
    assert mods == ["Large"]


def test_extract_item_modifiers_trailing_whitespace():
    name, mods = _extract_item_modifiers("  Burger (No onion)  ")
    assert name == "Burger"
    assert mods == ["No onion"]


# ── 3. _parse_items_text ──────────────────────────────────────────────────────


def test_parse_items_text_none():
    assert _parse_items_text(None) == []


def test_parse_items_text_empty():
    assert _parse_items_text("") == []


def test_parse_items_text_single_item_with_qty():
    result = _parse_items_text("2 Chicken Burger")
    assert result == [(Decimal("2"), "Chicken Burger")]


def test_parse_items_text_no_qty_prefix():
    result = _parse_items_text("Chicken Burger")
    assert result == [(Decimal("1"), "Chicken Burger")]


def test_parse_items_text_comma_separated():
    result = _parse_items_text("1 Burger, 2 Fries")
    assert len(result) == 2
    assert result[0] == (Decimal("1"), "Burger")
    assert result[1] == (Decimal("2"), "Fries")


def test_parse_items_text_newline_separated():
    result = _parse_items_text("1 Burger\n2 Fries\n1 Soda")
    assert len(result) == 3


def test_parse_items_text_parenthetical_not_split():
    """Modifier commas inside (…) must not split the item into multiple tokens."""
    result = _parse_items_text("1 Burger (No pickle, Extra cheese), 2 Fries")
    assert len(result) == 2
    assert result[0][0] == Decimal("1")
    assert "Burger" in result[0][1]
    assert result[1] == (Decimal("2"), "Fries")


def test_parse_items_text_semicolon_separated():
    result = _parse_items_text("1 Burger; 2 Fries")
    assert len(result) == 2


# ── 4. TalabatClient._items_from_row ──────────────────────────────────────────


def test_items_from_row_single_item_no_modifiers():
    row = {"Order Items": "2 Chicken Burger", "Subtotal": "50.00"}
    items = TalabatClient._items_from_row(row, "ORD-001", Decimal("50.00"))
    assert len(items) == 1
    item = items[0]
    assert item.item_name == "Chicken Burger"
    assert item.quantity == Decimal("2")
    assert item.modifiers == []
    assert item.modifiers_text is None
    assert item.amount_is_known is True
    assert item.gross_sales == Decimal("50.00")


def test_items_from_row_single_item_with_parenthetical_modifiers():
    row = {"Order Items": "1 Burger (No pickle, Extra cheese)"}
    items = TalabatClient._items_from_row(row, "ORD-002", Decimal("30.00"))
    assert len(items) == 1
    item = items[0]
    assert item.item_name == "Burger"
    assert len(item.modifiers) == 2
    mod_names = {m.name for m in item.modifiers}
    assert "No pickle" in mod_names
    assert "Extra cheese" in mod_names
    for mod in item.modifiers:
        assert mod.quantity == Decimal("1")
    assert item.modifiers_text == "Burger (No pickle, Extra cheese)"


def test_items_from_row_single_item_with_plus_addon():
    row = {"Order Items": "1 Pizza + Extra Cheese + No Olives"}
    items = TalabatClient._items_from_row(row, "ORD-003", Decimal("45.00"))
    assert len(items) == 1
    item = items[0]
    assert item.item_name == "Pizza"
    assert len(item.modifiers) == 2
    mod_names = {m.name for m in item.modifiers}
    assert "Extra Cheese" in mod_names
    assert "No Olives" in mod_names


def test_items_from_row_multiple_items_no_money():
    row = {"Order Items": "1 Burger, 2 Fries"}
    items = TalabatClient._items_from_row(row, "ORD-004", Decimal("70.00"))
    assert len(items) == 2
    for item in items:
        assert item.amount_is_known is False
        assert item.gross_sales is None


def test_items_from_row_dedicated_modifier_column_single_item():
    """Dedicated modifier column takes priority over free-text extraction."""
    row = {
        "Order Items": "1 Chicken Burger",
        "Modifier names": "No pickle; Extra sauce",
    }
    items = TalabatClient._items_from_row(row, "ORD-005", Decimal("35.00"))
    assert len(items) == 1
    mod_names = {m.name for m in items[0].modifiers}
    assert "No pickle" in mod_names
    assert "Extra sauce" in mod_names


def test_items_from_row_dedicated_column_ignored_for_multiple_items():
    """Modifier column is not assigned when there are multiple items."""
    row = {
        "Order Items": "1 Burger, 1 Fries",
        "Modifier names": "No pickle",
    }
    items = TalabatClient._items_from_row(row, "ORD-006", Decimal("60.00"))
    assert len(items) == 2
    # Column should not be applied when multiple items (ambiguous ownership)
    for item in items:
        assert item.modifiers == []


def test_items_from_row_empty_items_field():
    row = {"Order Items": ""}
    items = TalabatClient._items_from_row(row, "ORD-007", None)
    assert items == []


def test_items_from_row_source_keys_are_sequential():
    row = {"Order Items": "1 Burger\n2 Fries\n1 Soda"}
    items = TalabatClient._items_from_row(row, "ORD-008", None)
    assert [i.source_key for i in items] == ["ORD-008:1", "ORD-008:2", "ORD-008:3"]


# ── 5. TalabatClient._parse_bundle_bytes ──────────────────────────────────────


def _make_bundle_bytes(
    *,
    period_title: str = "Brand - 01/08/2026 - 31/08/2026",
    rows: list[dict],
) -> bytes:
    """Build an in-memory zip containing a Detailed_*.xlsx matching the Talabat format."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # A1: period title
    ws["A1"] = period_title

    # Header row at row 2 (within the first 10 that the parser scans)
    headers = [
        "Order Id",
        "Branch Id",
        "Date / Time",
        "SubTotal",
        "Commission VAT Exclu.",
        "Commission VAT",
        "Payment Handling Charges",
        "Payment Handling Charges VAT",
        "Promotional Fees",
        "Sponsored Deal Fees",
        "Avoidable Wait Time Fee",
        "Avoidable Wait Time Fee VAT",
        "Cost Per Order",
        "GEM Fee",
        "Loyalty Charges",
        "Net Payment Per Order",
    ]
    ws.append(headers)

    for r in rows:
        ws.append([r.get(h) for h in headers])

    xlsx_buf = io.BytesIO()
    wb.save(xlsx_buf)
    xlsx_bytes = xlsx_buf.getvalue()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("Detailed_August_2026.xlsx", xlsx_bytes)
    return zip_buf.getvalue()


def test_parse_bundle_bytes_basic_single_branch():
    bundle = _make_bundle_bytes(
        rows=[
            {
                "Order Id": 100001,
                "Branch Id": "BR001",
                "Date / Time": datetime(2026, 8, 5, 12, 30),
                "SubTotal": 50.0,
                "Commission VAT Exclu.": 5.0,
                "Commission VAT": 0.25,
                "Payment Handling Charges": 2.0,
                "Payment Handling Charges VAT": 0.1,
                "Promotional Fees": 0.0,
                "Sponsored Deal Fees": 0.0,
                "Avoidable Wait Time Fee": 0.0,
                "Avoidable Wait Time Fee VAT": 0.0,
                "Cost Per Order": 0.0,
                "GEM Fee": 0.0,
                "Loyalty Charges": 0.0,
                "Net Payment Per Order": 42.65,
            }
        ]
    )
    statements = TalabatClient._parse_bundle_bytes(bundle)
    assert len(statements) == 1
    stmt = statements[0]
    assert stmt.statement_id == "detailed-2026-08-01-2026-08-31-BR001"
    assert stmt.period_start == "2026-08-01"
    assert stmt.period_end == "2026-08-31"
    assert stmt.external_outlet_id == "BR001"
    assert stmt.currency == "AED"
    assert stmt.gross_sales == Decimal("50.00")
    # total_fees = commission + payment_handling (all non-zero fee cols)
    assert stmt.total_fees == Decimal("7.00")
    # total_vat = commission_vat + payment_handling_vat
    assert stmt.total_vat == Decimal("0.35")
    assert stmt.net_payable == Decimal("42.65")


def test_parse_bundle_bytes_lines_attached():
    bundle = _make_bundle_bytes(
        rows=[
            {
                "Order Id": 200001,
                "Branch Id": "BR001",
                "Date / Time": datetime(2026, 8, 10),
                "SubTotal": 80.0,
                "Commission VAT Exclu.": 8.0,
                "Commission VAT": 0.4,
                "Payment Handling Charges": 3.0,
                "Payment Handling Charges VAT": 0.15,
                "Promotional Fees": 0.0,
                "Sponsored Deal Fees": 0.0,
                "Avoidable Wait Time Fee": 0.0,
                "Avoidable Wait Time Fee VAT": 0.0,
                "Cost Per Order": 0.0,
                "GEM Fee": 0.0,
                "Loyalty Charges": 0.0,
                "Net Payment Per Order": 68.45,
            }
        ]
    )
    statements = TalabatClient._parse_bundle_bytes(bundle)
    assert len(statements) == 1
    stmt = statements[0]
    lines = stmt.lines
    # expect lines for: subtotal, commission, commission_vat, payment_handling,
    # payment_handling_vat, net_payable (zeros are skipped)
    fee_categories = {line.fee_category for line in lines}
    assert "subtotal" in fee_categories
    assert "commission" in fee_categories
    assert "commission_vat" in fee_categories
    assert "payment_handling" in fee_categories
    assert "net_payable" in fee_categories

    for line in lines:
        assert line.statement_id == stmt.statement_id
        assert line.external_order_id == "200001"
        assert line.currency == "AED"
        assert line.line_date == "2026-08-10"


def test_parse_bundle_bytes_two_branches():
    bundle = _make_bundle_bytes(
        rows=[
            {
                "Order Id": 1,
                "Branch Id": "BRA",
                "Date / Time": None,
                "SubTotal": 100.0,
                "Commission VAT Exclu.": 10.0,
                "Commission VAT": 0.5,
                "Payment Handling Charges": 0.0,
                "Payment Handling Charges VAT": 0.0,
                "Promotional Fees": 0.0,
                "Sponsored Deal Fees": 0.0,
                "Avoidable Wait Time Fee": 0.0,
                "Avoidable Wait Time Fee VAT": 0.0,
                "Cost Per Order": 0.0,
                "GEM Fee": 0.0,
                "Loyalty Charges": 0.0,
                "Net Payment Per Order": 89.5,
            },
            {
                "Order Id": 2,
                "Branch Id": "BRB",
                "Date / Time": None,
                "SubTotal": 200.0,
                "Commission VAT Exclu.": 20.0,
                "Commission VAT": 1.0,
                "Payment Handling Charges": 0.0,
                "Payment Handling Charges VAT": 0.0,
                "Promotional Fees": 0.0,
                "Sponsored Deal Fees": 0.0,
                "Avoidable Wait Time Fee": 0.0,
                "Avoidable Wait Time Fee VAT": 0.0,
                "Cost Per Order": 0.0,
                "GEM Fee": 0.0,
                "Loyalty Charges": 0.0,
                "Net Payment Per Order": 179.0,
            },
        ]
    )
    statements = TalabatClient._parse_bundle_bytes(bundle)
    assert len(statements) == 2
    ids = {s.statement_id for s in statements}
    assert "detailed-2026-08-01-2026-08-31-BRA" in ids
    assert "detailed-2026-08-01-2026-08-31-BRB" in ids
    stmt_a = next(s for s in statements if s.external_outlet_id == "BRA")
    stmt_b = next(s for s in statements if s.external_outlet_id == "BRB")
    assert stmt_a.gross_sales == Decimal("100.00")
    assert stmt_b.gross_sales == Decimal("200.00")


def test_parse_bundle_bytes_skips_zero_rows():
    bundle = _make_bundle_bytes(
        rows=[
            {
                "Order Id": 999,
                "Branch Id": "BR000",
                "Date / Time": None,
                **{
                    col: 0.0
                    for col in [
                        "SubTotal",
                        "Commission VAT Exclu.",
                        "Commission VAT",
                        "Payment Handling Charges",
                        "Payment Handling Charges VAT",
                        "Promotional Fees",
                        "Sponsored Deal Fees",
                        "Avoidable Wait Time Fee",
                        "Avoidable Wait Time Fee VAT",
                        "Cost Per Order",
                        "GEM Fee",
                        "Loyalty Charges",
                        "Net Payment Per Order",
                    ]
                },
            }
        ]
    )
    statements = TalabatClient._parse_bundle_bytes(bundle)
    # Branch with zero gross and zero net is skipped
    assert statements == []


def test_parse_bundle_bytes_non_order_id_row_skipped():
    """Non-numeric rows (header fragments, totals) are skipped."""
    bundle = _make_bundle_bytes(rows=[])
    # No data rows → no statements
    statements = TalabatClient._parse_bundle_bytes(bundle)
    assert statements == []


def test_parse_bundle_bytes_invalid_zip():
    from app.services.providers.aggregator_base import AggregatorUnavailableError

    with pytest.raises(AggregatorUnavailableError, match="valid zip"):
        TalabatClient._parse_bundle_bytes(b"not a zip file")


def test_parse_bundle_bytes_no_detailed_xlsx():
    """A zip with no Detailed_*.xlsx entries returns empty."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Summary.pdf", b"pdf content")
    statements = TalabatClient._parse_bundle_bytes(buf.getvalue())
    assert statements == []


def test_parse_bundle_bytes_line_date_from_date_string():
    """Date / Time as a plain date string (not datetime) is parsed correctly."""
    bundle = _make_bundle_bytes(
        rows=[
            {
                "Order Id": 300001,
                "Branch Id": "BR_X",
                "Date / Time": "2026-08-15 14:00",
                "SubTotal": 60.0,
                "Commission VAT Exclu.": 6.0,
                "Commission VAT": 0.3,
                "Payment Handling Charges": 0.0,
                "Payment Handling Charges VAT": 0.0,
                "Promotional Fees": 0.0,
                "Sponsored Deal Fees": 0.0,
                "Avoidable Wait Time Fee": 0.0,
                "Avoidable Wait Time Fee VAT": 0.0,
                "Cost Per Order": 0.0,
                "GEM Fee": 0.0,
                "Loyalty Charges": 0.0,
                "Net Payment Per Order": 53.7,
            }
        ]
    )
    statements = TalabatClient._parse_bundle_bytes(bundle)
    assert len(statements) == 1
    stmt = statements[0]
    line = next((item for item in stmt.lines if item.fee_category == "subtotal"), None)
    assert line is not None
    assert line.line_date == "2026-08-15"


# ── 6. Bundle statement parse (archival deferred) ─────────────────────────────


def _make_two_branch_bundle() -> bytes:
    return _make_bundle_bytes(
        rows=[
            {
                "Order Id": 1,
                "Branch Id": "BRA",
                "Date / Time": None,
                "SubTotal": 100.0,
                "Commission VAT Exclu.": 10.0,
                "Commission VAT": 0.5,
                "Payment Handling Charges": 0.0,
                "Payment Handling Charges VAT": 0.0,
                "Promotional Fees": 0.0,
                "Sponsored Deal Fees": 0.0,
                "Avoidable Wait Time Fee": 0.0,
                "Avoidable Wait Time Fee VAT": 0.0,
                "Cost Per Order": 0.0,
                "GEM Fee": 0.0,
                "Loyalty Charges": 0.0,
                "Net Payment Per Order": 89.5,
            },
            {
                "Order Id": 2,
                "Branch Id": "BRB",
                "Date / Time": None,
                "SubTotal": 200.0,
                "Commission VAT Exclu.": 20.0,
                "Commission VAT": 1.0,
                "Payment Handling Charges": 0.0,
                "Payment Handling Charges VAT": 0.0,
                "Promotional Fees": 0.0,
                "Sponsored Deal Fees": 0.0,
                "Avoidable Wait Time Fee": 0.0,
                "Avoidable Wait Time Fee VAT": 0.0,
                "Cost Per Order": 0.0,
                "GEM Fee": 0.0,
                "Loyalty Charges": 0.0,
                "Net Payment Per Order": 179.0,
            },
        ]
    )


_BUNDLE_BYTES = _make_two_branch_bundle()


@pytest.mark.asyncio
async def test_fetch_bundle_statements_parses_without_archival():
    """Bundle xlsx parse works; invoice fields unset until PDF discovery."""
    client = TalabatClient()
    session = MagicMock()

    with (
        patch.object(
            client,
            "_graphql",
            side_effect=AsyncMock(
                return_value={
                    "finances": {
                        "getBulkAdditionalStatementDownloadCounts": {
                            "fileCounts": {"totalFilesCount": 1},
                            "fileLimits": {"directDownloadLimit": 100},
                        }
                    }
                }
            ),
        ),
        patch.object(
            client,
            "_bundle_download_url",
            return_value="https://fake-url/bundle.zip",
        ),
        patch.object(
            client,
            "_download_bundle",
            return_value=_BUNDLE_BYTES,
        ),
    ):
        result = await client._fetch_bundle_statements(
            session,
            accounts=[{"grid": "g1"}],
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 31),
        )

    assert len(result) == 2
    for stmt in result:
        assert stmt.invoice_object_key is None
        assert stmt.invoice_content_type is None
        assert stmt.invoice_fetched_at is None
    ids = {s.statement_id for s in result}
    assert "detailed-2026-08-01-2026-08-31-BRA" in ids
    assert "detailed-2026-08-01-2026-08-31-BRB" in ids
    assert any(len(s.lines) > 0 for s in result)


# ── 7. Invoice archival, download auth classification, truncation note ─────────


def _counts_graphql_mock() -> AsyncMock:
    """A `_graphql` stand-in returning a within-limits bulk-count response."""
    return AsyncMock(
        return_value={
            "finances": {
                "getBulkAdditionalStatementDownloadCounts": {
                    "fileCounts": {"totalFilesCount": 1},
                    "fileLimits": {"directDownloadLimit": 100},
                }
            }
        }
    )


@pytest.mark.asyncio
async def test_fetch_bundle_statements_archives_and_sets_invoice_key():
    """A successful bundle download archives the zip and stamps invoice fields."""
    from app.services.aggregators.statement_docs import StoredStatementInvoice

    client = TalabatClient()
    session = MagicMock()

    stored = StoredStatementInvoice(
        object_key=(
            "aggregator-statements/talabat/bundle-2026-08-01-2026-08-31/"
            "additionalStatementsArchive_2026-08-01_to_2026-08-31.zip"
        ),
        content_type="application/zip",
        original_filename="additionalStatementsArchive_2026-08-01_to_2026-08-31.zip",
        fetched_at=datetime(2026, 8, 28, 12, 0),
        size_bytes=len(_BUNDLE_BYTES),
        attachments=None,
    )

    with (
        patch.object(client, "_graphql", side_effect=_counts_graphql_mock()),
        patch.object(
            client, "_bundle_download_url", return_value="https://fake-url/bundle.zip"
        ),
        patch.object(client, "_download_bundle", return_value=_BUNDLE_BYTES),
        patch(
            "app.services.aggregators.statement_docs.store_statement_invoice",
            return_value=stored,
        ) as mock_store,
    ):
        result = await client._fetch_bundle_statements(
            session,
            accounts=[{"grid": "g1"}],
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 31),
        )

    # store_statement_invoice called exactly once with the zip bytes.
    mock_store.assert_called_once()
    kwargs = mock_store.call_args.kwargs
    assert kwargs["channel"] == "talabat"
    assert kwargs["content_type"] == "application/zip"
    assert kwargs["body"] == _BUNDLE_BYTES
    assert kwargs["filename"].endswith(".zip")

    assert len(result) == 2
    for stmt in result:
        assert stmt.invoice_object_key == stored.object_key
        assert stmt.invoice_content_type == "application/zip"
        assert stmt.invoice_original_filename == stored.original_filename
        assert stmt.invoice_fetched_at == stored.fetched_at


@pytest.mark.asyncio
async def test_fetch_bundle_statements_unconfigured_r2_keeps_statements():
    """When store_statement_invoice returns None (R2 off), statements survive."""
    client = TalabatClient()
    session = MagicMock()

    with (
        patch.object(client, "_graphql", side_effect=_counts_graphql_mock()),
        patch.object(
            client, "_bundle_download_url", return_value="https://fake-url/bundle.zip"
        ),
        patch.object(client, "_download_bundle", return_value=_BUNDLE_BYTES),
        patch(
            "app.services.aggregators.statement_docs.store_statement_invoice",
            return_value=None,
        ),
    ):
        result = await client._fetch_bundle_statements(
            session,
            accounts=[{"grid": "g1"}],
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 31),
        )

    assert len(result) == 2
    for stmt in result:
        assert stmt.invoice_object_key is None
        assert len(stmt.lines) >= 0


class _FakeResponse:
    def __init__(self, status_code: int, *, text: str = "", content: bytes = b""):
        self.status_code = status_code
        self.text = text
        self.content = content


@pytest.mark.asyncio
async def test_download_bundle_auth_failure_raises_auth_error():
    """A 403 on the bundle GET is a dead session → AggregatorAuthError."""
    from app.services.providers.aggregator_base import AggregatorAuthError

    client = TalabatClient()
    session = MagicMock()
    session.cookies = {}
    session.tokens = {}

    with patch.object(
        client, "request_raw", new=AsyncMock(return_value=_FakeResponse(403))
    ):
        with pytest.raises(AggregatorAuthError):
            await client._download_bundle(session, "https://fake-url/bundle.zip")


@pytest.mark.asyncio
async def test_download_bundle_perimeterx_body_raises_auth_error():
    """A 200 whose body is a PerimeterX challenge is also a dead session."""
    from app.services.providers.aggregator_base import AggregatorAuthError

    client = TalabatClient()
    session = MagicMock()
    session.cookies = {}
    session.tokens = {}

    challenge = _FakeResponse(
        200, text="Please enable JS and disable any ad blocker — px-captcha"
    )
    with patch.object(client, "request_raw", new=AsyncMock(return_value=challenge)):
        with pytest.raises(AggregatorAuthError):
            await client._download_bundle(session, "https://fake-url/bundle.zip")


@pytest.mark.asyncio
async def test_download_bundle_server_error_stays_unavailable():
    """A genuine 5xx is transient → AggregatorUnavailableError, not auth."""
    from app.services.providers.aggregator_base import AggregatorUnavailableError

    client = TalabatClient()
    session = MagicMock()
    session.cookies = {}
    session.tokens = {}

    with patch.object(
        client, "request_raw", new=AsyncMock(return_value=_FakeResponse(503))
    ):
        with pytest.raises(AggregatorUnavailableError):
            await client._download_bundle(session, "https://fake-url/bundle.zip")


@pytest.mark.asyncio
async def test_download_csv_auth_failure_raises_auth_error():
    """The sales CSV download flips to AggregatorAuthError on a challenge too."""
    from app.services.providers.aggregator_base import AggregatorAuthError

    client = TalabatClient()
    session = MagicMock()
    session.cookies = {}
    session.tokens = {}

    with patch.object(
        client, "request_raw", new=AsyncMock(return_value=_FakeResponse(401))
    ):
        with pytest.raises(AggregatorAuthError):
            await client._download_csv(session, "https://fake-url/report.csv")


@pytest.mark.asyncio
async def test_fetch_statements_sets_truncation_note_on_bundle_failure():
    """A failed xlsx bundle leaves a truncation_note; metadata statements survive."""
    from app.services.providers.aggregator_base import AggregatorUnavailableError

    client = TalabatClient()
    session = MagicMock()

    metadata_rows = [
        {
            "statementId": "TUAE-02049097",
            "statementDate": "2026-07-31",
            "amountGross": "100.00",
            "currency": "AED",
        }
    ]

    with (
        patch.object(client, "_finance_accounts", return_value=[{"grid": "g1"}]),
        patch.object(
            client, "_paginate_finance", new=AsyncMock(return_value=metadata_rows)
        ),
        patch.object(
            client,
            "_fetch_bundle_statements",
            new=AsyncMock(
                side_effect=AggregatorUnavailableError("bundle download HTTP 503")
            ),
        ),
    ):
        result = await client.fetch_statements(
            session, since=datetime(2026, 7, 1), until=datetime(2026, 7, 31)
        )

    assert result.truncation_note is not None
    assert "xlsx" in result.truncation_note.lower()
    # The metadata statement is still returned — the failure did not drop it.
    assert len(result.statements) == 1
    assert result.statements[0].statement_id == "TUAE-02049097"


# ── global entity id from account extras (vs the TB_AE fallback) ───────────────
def _session_with_tokens(tokens: dict):
    session = MagicMock()
    session.tokens = tokens
    return session


def test_global_entity_id_falls_back_to_tb_ae_when_extras_absent():
    """An empty session yields the historical constant — behaviour unchanged."""
    from app.services.providers.talabat_provider import _GLOBAL_ENTITY_ID

    client = TalabatClient()
    assert client._global_entity_id(_session_with_tokens({})) == _GLOBAL_ENTITY_ID
    assert _GLOBAL_ENTITY_ID == "TB_AE"


def test_global_entity_id_reads_from_session_tokens():
    """A populated `global_entity_id` (injected from account extras) wins."""
    client = TalabatClient()
    session = _session_with_tokens({"global_entity_id": "TB_KW"})
    assert client._global_entity_id(session) == "TB_KW"


def test_global_entity_id_ignores_blank_token():
    """A blank/whitespace token is treated as absent, not as an empty entity."""
    from app.services.providers.talabat_provider import _GLOBAL_ENTITY_ID

    client = TalabatClient()
    session = _session_with_tokens({"global_entity_id": "   "})
    assert client._global_entity_id(session) == _GLOBAL_ENTITY_ID


# ── GraphQL auth-error classification (session-death vs transient) ─────────────
_AUTH_ERRORS = [
    {
        "message": "HTTP fetch failed from 'vp-report-builder': 401: Unauthorized",
        "path": [],
        "extensions": {
            "code": "SUBREQUEST_HTTP_ERROR",
            "service": "vp-report-builder",
            "reason": "401: Unauthorized",
            "http": {"status": 401},
        },
    },
    {
        "message": (
            "service 'vp-report-builder' response was malformed: "
            '{"code":"TOKEN_EXPIRED","message":"token expired","state":"ERROR"}'
        ),
        "path": [],
        "extensions": {
            "code": "SUBREQUEST_MALFORMED_RESPONSE",
            "service": "vp-report-builder",
        },
    },
]

_VALIDATION_ERRORS = [
    {"message": "from must be before to", "extensions": {"code": "VALIDATION_ERROR"}}
]


def test_graphql_errors_are_auth_flags_token_expiry_and_401():
    from app.services.providers.talabat_provider import _graphql_errors_are_auth

    assert _graphql_errors_are_auth(_AUTH_ERRORS) is True
    # A genuine application error must not be mistaken for a dead session.
    assert _graphql_errors_are_auth(_VALIDATION_ERRORS) is False
    # A store id that merely contains "401" must not trip the classifier.
    assert _graphql_errors_are_auth([{"message": "store 40123 not found"}]) is False


@pytest.mark.asyncio
async def test_graphql_token_expired_raises_auth_error():
    """A 200 whose errors carry an expired token is a dead session, so the sweep
    re-logs in rather than retrying — AggregatorAuthError, not Unavailable."""
    from app.services.providers.aggregator_base import AggregatorAuthError

    client = TalabatClient()
    session = MagicMock()
    with (
        patch.object(client, "_global_entity_id", return_value="TB_AE"),
        patch.object(client, "_access_token", return_value="stale-token"),
        patch.object(
            client,
            "request_json",
            new=AsyncMock(return_value={"errors": _AUTH_ERRORS}),
        ),
    ):
        with pytest.raises(AggregatorAuthError):
            await client._graphql(
                session,
                endpoint="https://fake/graphql",
                query="query EstimateExportSize {}",
                variables={},
                operation_name="EstimateExportSize",
            )


@pytest.mark.asyncio
async def test_graphql_validation_error_stays_unavailable():
    """A real application error is transient/actionable, not a session death."""
    from app.services.providers.aggregator_base import AggregatorUnavailableError

    client = TalabatClient()
    session = MagicMock()
    with (
        patch.object(client, "_global_entity_id", return_value="TB_AE"),
        patch.object(client, "_access_token", return_value="ok-token"),
        patch.object(
            client,
            "request_json",
            new=AsyncMock(return_value={"errors": _VALIDATION_ERRORS}),
        ),
    ):
        with pytest.raises(AggregatorUnavailableError):
            await client._graphql(
                session,
                endpoint="https://fake/graphql",
                query="query EstimateExportSize {}",
                variables={},
                operation_name="EstimateExportSize",
            )
