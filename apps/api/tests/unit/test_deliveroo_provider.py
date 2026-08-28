"""Unit tests for the Deliveroo provider — pure Python, no DB, no httpx.

Coverage areas:
1. `DeliverooClient._invoice_csv`  — returns (text, bytes) or (None, None).
2. `DeliverooClient._statement_lines` — CSV parse produces correct lines.
3. `fetch_statements` — parses lines; invoice archival deferred until PDF discovery.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.providers.deliveroo_provider import (
    DeliverooClient,
    _num,
    _parse_date,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_SAMPLE_CSV = textwrap.dedent("""\
    Invoice Reference,12345
    Period,Aug 2026

    Restaurant Name,Order ID,Delivery Date & Time (UTC),Activity,Note,Order Value (د.إ),Adjustment Net (د.إ),Deliveroo Commission (د.إ),Commission / Adjustment VAT (د.إ),Total Payable
    My Restaurant,ORD-001,2026-08-10 10:00:00,,, 50.00,,  -5.00,  -0.25,  44.75
    My Restaurant,ORD-002,2026-08-11 12:30:00,,, 80.00,,  -8.00,  -0.40,  71.60
""")

_SAMPLE_CSV_BYTES = _SAMPLE_CSV.encode()


# ── 1. _num and _parse_date (utilities) ───────────────────────────────────────


def test_num_plain_decimal():
    assert _num("50.00") == Decimal("50.00")


def test_num_negative_parentheses():
    assert _num("(1.23)") == Decimal("-1.23")


def test_num_none():
    assert _num(None) is None


def test_num_blank():
    assert _num("") is None


def test_parse_date_iso():
    assert _parse_date("2026-08-01") is not None


def test_parse_date_deliveroo_label():
    assert _parse_date("Wed 3 Sep 2025") is not None


# ── 2. _statement_lines ───────────────────────────────────────────────────────


def test_statement_lines_basic():
    client = DeliverooClient()
    lines = client._statement_lines("stmt-1", _SAMPLE_CSV)
    fee_categories = {line.fee_category for line in lines}
    assert "gross_sales" in fee_categories
    assert "commission" in fee_categories
    assert "commission_vat" in fee_categories
    assert "net_payable" in fee_categories


def test_statement_lines_order_ids_parsed():
    client = DeliverooClient()
    lines = client._statement_lines("stmt-1", _SAMPLE_CSV)
    order_ids = {line.external_order_id for line in lines if line.external_order_id}
    assert "ORD-001" in order_ids
    assert "ORD-002" in order_ids


def test_statement_lines_amounts_are_decimal():
    client = DeliverooClient()
    lines = client._statement_lines("stmt-1", _SAMPLE_CSV)
    for line in lines:
        assert isinstance(line.amount, Decimal)


def test_statement_lines_no_header_returns_empty():
    client = DeliverooClient()
    lines = client._statement_lines("stmt-1", "no,header,here\n1,2,3\n")
    assert lines == []


# ── 3. Invoice archival ───────────────────────────────────────────────────────

_MOCK_RESPONSE_OK = MagicMock(
    status_code=200,
    content=_SAMPLE_CSV_BYTES,
    text=_SAMPLE_CSV,
    headers={"content-type": "text/csv"},
)

_MOCK_RESPONSE_403 = MagicMock(
    status_code=403,
    content=b"Forbidden",
    text="Forbidden",
    headers={"content-type": "text/html"},
)


@pytest.mark.asyncio
async def test_invoice_csv_returns_text_and_bytes_on_success():
    """_invoice_csv returns (text, bytes) when the download succeeds."""
    client = DeliverooClient()
    session = MagicMock()

    with patch.object(client, "request_raw", return_value=_MOCK_RESPONSE_OK):
        text, raw = await client._invoice_csv(session, "org1", "inv-1")

    assert text is not None
    assert raw is not None
    assert isinstance(raw, bytes)
    assert "Restaurant Name" in text


@pytest.mark.asyncio
async def test_invoice_csv_returns_none_on_403():
    """_invoice_csv returns (None, None) when the server returns a 403."""
    client = DeliverooClient()
    session = MagicMock()

    with patch.object(client, "request_raw", return_value=_MOCK_RESPONSE_403):
        text, raw = await client._invoice_csv(session, "org1", "inv-err")

    assert text is None
    assert raw is None


@pytest.mark.asyncio
async def test_fetch_statements_parses_lines_without_archival():
    """CSV parse populates statement lines; invoice fields stay unset until PDF wiring."""
    client = DeliverooClient()
    session = MagicMock()

    _invoice_meta = {
        "id": "INV-99",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "payment_due_date": "2026-09-07",
        "total": {"fractional": 4000},
        "currency": "AED",
    }

    with (
        patch.object(client, "_list_invoices", return_value=[_invoice_meta]),
        patch.object(client, "request_raw", return_value=_MOCK_RESPONSE_OK),
    ):
        result = await client.fetch_statements(
            session,
            since=datetime(2026, 8, 1, tzinfo=timezone.utc),
            until=datetime(2026, 8, 31, tzinfo=timezone.utc),
        )

    assert len(result.statements) == 1
    stmt = result.statements[0]
    assert stmt.invoice_object_key is None
    assert stmt.invoice_content_type is None
    assert stmt.invoice_fetched_at is None
    assert len(stmt.lines) > 0


@pytest.mark.asyncio
async def test_fetch_statements_empty_lines_when_csv_unavailable():
    """When the CSV download fails (403), lines stay empty and no archive is attempted."""
    client = DeliverooClient()
    session = MagicMock()

    _invoice_meta = {
        "id": "INV-77",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "total": {"fractional": 0},
        "currency": "AED",
    }

    with (
        patch.object(client, "_list_invoices", return_value=[_invoice_meta]),
        patch.object(client, "request_raw", return_value=_MOCK_RESPONSE_403),
    ):
        result = await client.fetch_statements(
            session,
            since=datetime(2026, 8, 1, tzinfo=timezone.utc),
            until=datetime(2026, 8, 31, tzinfo=timezone.utc),
        )

    assert len(result.statements) == 1
    assert result.statements[0].lines == []
    assert "INV-77" in (result.truncation_note or "")
    assert result.statements[0].invoice_object_key is None
