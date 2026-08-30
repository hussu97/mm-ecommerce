"""Unit tests for the Deliveroo provider — pure Python, no DB, no httpx.

Coverage areas:
1. `DeliverooClient._invoice_csv`  — returns (text, bytes) or (None, None).
2. `DeliverooClient._statement_lines` — CSV parse produces correct lines.
3. `fetch_statements` — parses lines; invoice archival deferred until PDF discovery.
"""

from __future__ import annotations

import base64
import textwrap
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.aggregators.normalized import StandardOrder
from app.services.aggregators.session_store import LoadedSession
from app.services.aggregators.statement_docs import StoredStatementInvoice
from app.services.providers.deliveroo_provider import (
    DeliverooClient,
    _num,
    _parse_date,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _org_session() -> LoadedSession:
    """A resolved session carrying org_id — what `_augment_from_db` guarantees
    before any finance call, so the org-scoped URLs resolve without the removed
    hard-coded default."""
    return LoadedSession(
        channel="deliveroo",
        account_ref="",
        cookies={"token": "jwt"},
        tokens={"access_token": "jwt", "org_id": "497912"},
    )


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
    session = _org_session()

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
    session = _org_session()

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


@pytest.mark.asyncio
async def test_fetch_payouts_derives_one_per_invoice():
    """Deliveroo settles each invoice 1:1, so a payout is derived per invoice —
    keyed on the statement id so the statement↔payout back-link can close."""
    client = DeliverooClient()
    session = _org_session()
    _invoice_meta = {
        "id": "INV-99",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "due_at": "2026-09-07",
        "total": {"fractional": 4000},
        "currency": "AED",
        "reference": "REF-99",
    }
    with patch.object(client, "_list_invoices", return_value=[_invoice_meta]):
        result = await client.fetch_payouts(
            session,
            since=datetime(2026, 8, 1, tzinfo=timezone.utc),
            until=datetime(2026, 8, 31, tzinfo=timezone.utc),
        )
    assert len(result.payouts) == 1
    p = result.payouts[0]
    assert p.transfer_id == "INV-99"  # same id as the statement → links directly
    assert p.statement_id == "INV-99"
    assert p.transfer_amount == Decimal("40.00")
    assert p.transfer_date == "2026-09-07"
    assert p.transfer_status == "derived"
    assert p.payment_reference == "REF-99"


# ── 4. parse_pushed_finance (in-page worker payload) ──────────────────────────

_PUSHED_PAYLOAD = {
    "invoice": {
        "id": "INV-42",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "payment_due_date": "2026-09-07",
        "total": {"fractional": 4000},
        "currency": "AED",
    },
    "statement_csv": _SAMPLE_CSV,
    "statement_pdf_b64": base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode("ascii"),
}

_STORED = StoredStatementInvoice(
    object_key="invoices/deliveroo/INV-42/INV-42.pdf",
    content_type="application/pdf",
    original_filename="INV-42.pdf",
    fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    size_bytes=23,
    attachments=[{"object_key": "invoices/deliveroo/INV-42/INV-42.csv"}],
)


def test_parse_pushed_finance_builds_statement_with_lines():
    """A pushed invoice payload yields a statement whose CSV lines are parsed."""
    client = DeliverooClient()
    with patch(
        "app.services.providers.deliveroo_provider.store_statement_invoice",
        return_value=_STORED,
    ) as mock_store:
        stmt = client.parse_pushed_finance(_PUSHED_PAYLOAD)

    assert stmt is not None
    assert stmt.statement_id == "INV-42"
    assert stmt.period_start == "2026-08-01"
    assert stmt.period_end == "2026-08-31"
    assert stmt.payment_due_date == "2026-09-07"
    assert stmt.net_payable == Decimal("40.00")  # 4000 fils
    # Lines came off the shared CSV parser.
    fee_categories = {line.fee_category for line in stmt.lines}
    assert {"gross_sales", "commission", "commission_vat", "net_payable"} <= (
        fee_categories
    )
    # PDF archived as primary, CSV alongside as an extra file.
    mock_store.assert_called_once()
    kwargs = mock_store.call_args.kwargs
    assert kwargs["channel"] == "deliveroo"
    assert kwargs["content_type"] == "application/pdf"
    assert kwargs["extra_files"] and kwargs["extra_files"][0][2] == "text/csv"
    assert stmt.invoice_object_key == _STORED.object_key
    assert stmt.invoice_content_type == "application/pdf"
    assert stmt.invoice_fetched_at == _STORED.fetched_at


def test_parse_pushed_finance_without_pdf_archives_csv():
    """When Cloudflare blocked the PDF (null b64), the CSV is archived instead."""
    client = DeliverooClient()
    payload = {**_PUSHED_PAYLOAD, "statement_pdf_b64": None}
    with patch(
        "app.services.providers.deliveroo_provider.store_statement_invoice",
        return_value=_STORED,
    ) as mock_store:
        stmt = client.parse_pushed_finance(payload)

    assert stmt is not None
    assert mock_store.call_args.kwargs["content_type"] == "text/csv"


def test_parse_pushed_finance_no_csv_no_lines():
    """A payload whose CSV was gated (None) parses to a statement with no lines."""
    client = DeliverooClient()
    payload = {
        "invoice": {"id": "INV-9", "total": {"fractional": 0}, "currency": "AED"},
        "statement_csv": None,
        "statement_pdf_b64": None,
    }
    with patch(
        "app.services.providers.deliveroo_provider.store_statement_invoice",
        return_value=None,
    ):
        stmt = client.parse_pushed_finance(payload)

    assert stmt is not None
    assert stmt.statement_id == "INV-9"
    assert stmt.lines == []
    assert stmt.invoice_object_key is None


def test_parse_pushed_finance_no_invoice_returns_none():
    client = DeliverooClient()
    assert client.parse_pushed_finance({"statement_csv": _SAMPLE_CSV}) is None


# ── 5. customer id → pseudonymous name (repeat-customer key) ───────────────────


def _base_order() -> StandardOrder:
    return StandardOrder(external_order_id="ORD-1", external_outlet_id="rest-1")


def test_customer_id_becomes_pseudonymous_name_when_no_real_name():
    """Deliveroo exposes only a numeric consumer id — store it as the name so the
    shop can spot repeat customers even though it can never see who they are."""
    client = DeliverooClient()
    merged = client._merge_order_detail(
        _base_order(), {"customer": {"id": 8760020}}, "rest-1"
    )
    assert merged.customer_name == "Deliveroo customer 8760020"


def test_real_customer_name_wins_over_id():
    client = DeliverooClient()
    merged = client._merge_order_detail(
        _base_order(),
        {"customer": {"id": 8760020, "name": "Aisha"}},
        "rest-1",
    )
    assert merged.customer_name == "Aisha"


def test_no_customer_id_leaves_name_none():
    client = DeliverooClient()
    merged = client._merge_order_detail(_base_order(), {"customer": {}}, "rest-1")
    assert merged.customer_name is None


# ── 6. realistic order detail: items + status history + customer ──────────────

# The per-order detail shape confirmed on prod: `items` with fractional money and
# nested modifier groups, a full `timeline`, a terminal `status`, and a customer
# that is only a numeric consumer id.
_REALISTIC_DETAIL = {
    "order_number": "MM-8842",
    "status": "delivered",
    "short_drn": "DRN-77",
    "rejection_reason": None,
    "customer": {"id": 32419558},
    "timeline": {
        "placed_at": "2026-08-28T08:38:23+04:00",
        "accepted_at": "2026-08-28T08:39:10+04:00",
        "prepare_for": "2026-08-28T09:05:00+04:00",
        "confirmed_at": "2026-08-28T08:40:02+04:00",
        "delivery_picked_up_at": "2026-08-28T08:58:44+04:00",
        "delivered_at": "2026-08-28T09:12:30+04:00",
    },
    "items": [
        {
            "name": "Pistachio Croissant",
            "quantity": 2,
            "category_name": "Viennoiserie",
            "unit_price": {"fractional": 1800},
            "total_price": {"fractional": 3600},
            "modifiers": [
                {
                    "name": "Add-ons",
                    "options": [
                        {"name": "Extra pistachio", "quantity": 1, "price": "5.00"}
                    ],
                }
            ],
        },
        {
            "name": "Flat White",
            "quantity": 1,
            "category_name": "Coffee",
            "unit_price": {"fractional": 2200},
            "total_price": {"fractional": 2200},
        },
    ],
}


def test_merge_detail_parses_item_lines():
    """Items parse: name, quantity, fractional/100 unit + total, and modifiers."""
    client = DeliverooClient()
    merged = client._merge_order_detail(_base_order(), _REALISTIC_DETAIL, "rest-1")

    assert len(merged.items) == 2
    croissant, coffee = merged.items

    assert croissant.item_name == "Pistachio Croissant"
    assert croissant.category_name == "Viennoiserie"
    assert croissant.quantity == Decimal("2")
    assert croissant.unit_price == Decimal("18.00")  # 1800 fils
    assert croissant.gross_sales == Decimal("36.00")  # 3600 fils
    assert croissant.net_sales == Decimal("36.00")
    assert croissant.amount_is_known is True
    # Nested modifier group flattened to its option.
    assert [m.name for m in croissant.modifiers] == ["Extra pistachio"]
    assert croissant.modifiers[0].quantity == Decimal("1")
    assert croissant.modifiers_text  # raw dump retained

    assert coffee.item_name == "Flat White"
    assert coffee.quantity == Decimal("1")
    assert coffee.unit_price == Decimal("22.00")
    assert coffee.modifiers == []


def test_merge_detail_builds_status_events_tz_aware():
    """The timeline becomes an ordered, tz-aware status trace plus the terminal."""
    client = DeliverooClient()
    merged = client._merge_order_detail(_base_order(), _REALISTIC_DETAIL, "rest-1")

    events = merged.status_events
    # placed, accepted, confirmed, prepare_for, picked_up, delivered
    assert [e.status for e in events] == [
        "placed",
        "accepted",
        "confirmed",
        "prepare_for",
        "picked_up",
        "delivered",
    ]
    # Sequence reflects lifecycle order (not chronology of the sparse subset).
    assert [e.sequence for e in events] == [0, 1, 2, 3, 4, 5]
    # Every event is tz-aware (Deliveroo returns +04:00 ISO; kept as-is).
    for event in events:
        assert event.at is not None
        assert event.at.tzinfo is not None
    placed = events[0]
    assert placed.at == datetime(
        2026, 8, 28, 8, 38, 23, tzinfo=timezone(timedelta(hours=4))
    )


def test_merge_detail_omits_absent_timeline_steps():
    """A still-open order emits only the steps it has reached; no terminal event."""
    client = DeliverooClient()
    detail = {
        "status": "confirmed",
        "customer": {"id": 32419558},
        "timeline": {
            "placed_at": "2026-08-28T08:38:23+04:00",
            "accepted_at": "2026-08-28T08:39:10+04:00",
            "confirmed_at": "2026-08-28T08:40:02+04:00",
            # no prepare_for / picked_up / delivered yet
        },
    }
    merged = client._merge_order_detail(_base_order(), detail, "rest-1")
    assert [e.status for e in merged.status_events] == [
        "placed",
        "accepted",
        "confirmed",
    ]


def test_merge_detail_cancelled_terminal_event():
    """A cancelled order still lists items and gets a `cancelled` terminal event."""
    client = DeliverooClient()
    detail = {
        "status": "cancelled",
        "customer": {"id": 999},
        "timeline": {
            "placed_at": "2026-08-28T08:38:23+04:00",
            "cancelled_at": "2026-08-28T08:45:00+04:00",
        },
        "items": [
            {
                "name": "Cancelled Cake",
                "quantity": 1,
                "total_price": {"fractional": 5000},
            }
        ],
    }
    merged = client._merge_order_detail(_base_order(), detail, "rest-1")
    assert len(merged.items) == 1  # items present even on a cancelled order
    assert [e.status for e in merged.status_events] == ["placed", "cancelled"]
    terminal = merged.status_events[-1]
    assert terminal.at == datetime(
        2026, 8, 28, 8, 45, 0, tzinfo=timezone(timedelta(hours=4))
    )


def test_merge_detail_realistic_customer_is_pseudonymous_id():
    """The realistic detail keeps the pseudonymous consumer-id name."""
    client = DeliverooClient()
    merged = client._merge_order_detail(_base_order(), _REALISTIC_DETAIL, "rest-1")
    assert merged.customer_name == "Deliveroo customer 32419558"
    assert merged.customer_phone is None


# ── 7. display_ref = the short order number (GrubOps convergence key) ──────────
def test_parse_list_order_sets_display_ref_to_short_order_number():
    """On a GrubOps branch (Barsha/Sharjah) the Foodics order stores Deliveroo's
    SHORT order number as its external_id; our external_order_id is Deliveroo's
    UUID, which Foodics never sees. Mapping order_number -> display_ref is what
    lets promotion converge onto the GrubOps order instead of filing a standalone
    duplicate (the 29-Aug Barsha +55 discrepancy)."""
    client = DeliverooClient()
    order = client._parse_list_order(
        {
            "order_id": "b6b2c42e-1a1e-317d-b444-cdf70bc5f7f3",
            "order_number": "5254",
            "amount": {"fractional": 5500},
            "status": "delivered",
        },
        "rest-1",
    )
    assert order is not None
    assert order.external_order_id == "b6b2c42e-1a1e-317d-b444-cdf70bc5f7f3"
    assert (
        order.display_ref == "5254"
    )  # the shared key with grubops_order_map.external_id


def test_merge_order_detail_preserves_display_ref():
    """The detail merge rebuilds the order; it must not drop the short-number key."""
    client = DeliverooClient()
    base = StandardOrder(
        external_order_id="uuid-1", external_outlet_id="rest-1", display_ref="5254"
    )
    merged = client._merge_order_detail(base, {"status": "delivered"}, "rest-1")
    assert merged.display_ref == "5254"
