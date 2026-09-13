"""Careem provider — order detail (v2/admin/orders) + the monthly Tax-Invoice.

The `partner-orders-minimal` feed is id/status/date/total only, but the console's
order popup (`GET /v2/admin/orders/{id}`) carries items, modifiers, the captain,
the customer dropoff address and the delivery status timeline; `_order_from_detail`
maps it. Per-order fees are still absent (Careem bills the merchant monthly), so
`fetch_statements` enumerates the monthly Tax Invoices (`billingReports/list`),
resolves each pre-signed S3 URL (`billingReports/{id}/download`) and archives the
PDF. All endpoints/shapes were confirmed against the live console; the parsers are
pinned here against faithful payload slices.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.aggregators.normalized import StandardOrder
from app.services.aggregators.statement_docs import StoredStatementInvoice
from app.services.providers import careem_provider
from app.services.providers.careem_provider import CareemClient

# A faithful slice of a real v2/admin/orders/{id} detail payload.
_DETAIL = {
    "id": 168934434,
    "status": "delivered",
    "created_at": "2026-08-28T15:36:52+00:00",
    "accepted_at": "2026-08-28T15:37:00+00:00",
    "delivered_at": "2026-08-28T15:56:48+00:00",
    "cancelled_at": None,
    "pending_at": "2026-08-28T15:36:55+00:00",
    "merchant": {"id": 1067984, "currency": {"code": "AED"}},
    "price": {"total": 55, "sub_total": 55, "tax": 0},
    "dropoff_address": {
        "area": "Jumeirah Village Circle (JVC)",
        "building": "Oakley Square Residences",
        "number": "B-312 - Floor No 3",
        "city": "Dubai",
        "nickname": "Jordan’s Home",
        "street": None,
        "location": {"lat": 25.0594, "lng": 55.2119},
    },
    "delivery": {
        "status": "TRIP_ENDED",
        "captain": {"id": 2442962, "mobile": "+971549931225", "name": "Taimoor Akram"},
        "status_log": [
            {"status": "DRIVER_ASSIGNED", "created_at": "2026-08-28T15:37:14+00:00"},
            {"status": "DRIVER_HERE", "created_at": "2026-08-28T15:40:57+00:00"},
            {"status": "TRIP_STARTED", "created_at": "2026-08-28T15:42:18+00:00"},
            {"status": "TRIP_ENDED", "created_at": "2026-08-28T15:56:47+00:00"},
        ],
    },
    "items": [
        {
            "category_name": "Brownies",
            "count": 1,
            "price": {"total": 55, "total_with_options": 55, "original": 55},
            "menu_item": {
                "item": "Lindor Brownies",
                "item_localized": {"en": "Lindor Brownies"},
                "price": {"total": 55},
            },
            "groups": [
                {
                    "name": "Your Choice of Quantity",
                    "options": [
                        {
                            "name": "3 Pieces",
                            "count": 1,
                            "price": {"original": 55, "total": 55},
                        }
                    ],
                }
            ],
        }
    ],
}

# A box whose contents are FREE options — the price is on the box, and the
# customer was charged 57.75 (55 menu + Careem's CPlus markup). gross must be the
# 55 menu value, and the item line 55 (not the 0 sum of the free options).
_DETAIL_BOX = {
    "id": 169056257,
    "status": "confirmed",
    "created_at": "2026-08-29T17:30:00+00:00",
    "merchant": {"id": 1067984, "currency": {"code": "AED"}},
    "total_price": 57.75,
    "charge_amount": 57.75,
    "price": {"total": 55, "sub_total": 55, "original": 55, "tax": 0},
    "items": [
        {
            "category_name": "Boxes",
            "count": 1,
            "price": {"total": 55, "total_with_options": 55, "original": 55},
            "menu_item": {
                "item": "Mix Brownies and Cookies Box of 3",
                "price": {"total": 55},
            },
            "groups": [
                {
                    "name": "Box Contents",
                    "options": [
                        {"name": "Ferrero Brownie", "count": 1, "price": {"total": 0}},
                        {"name": "Snickers Brownie", "count": 1, "price": {"total": 0}},
                        {
                            "name": "Cheesecake Brownie",
                            "count": 1,
                            "price": {"total": 0},
                        },
                    ],
                }
            ],
        }
    ],
}


def test_order_gross_is_the_menu_value_not_the_charged_amount():
    """The customer was charged 57.75 (Careem's CPlus markup on top of the 55 menu
    subtotal), but the shop's sale is 55, and the box line is 55 (not the 0 sum of
    its free contents)."""
    client = CareemClient()
    minimal = StandardOrder(external_order_id="169056257", external_outlet_id="1067984")
    order = client._order_from_detail(_DETAIL_BOX, minimal)
    assert order.gross_sales == Decimal("55")  # not 57.75
    line = order.items[0]
    assert line.item_name == "Mix Brownies and Cookies Box of 3"
    assert line.gross_sales == Decimal("55")  # box price, not the 0 options sum
    assert [m.name for m in line.modifiers] == [
        "Ferrero Brownie",
        "Snickers Brownie",
        "Cheesecake Brownie",
    ]


def test_order_from_detail_extracts_items_modifiers_driver_address_timeline():
    client = CareemClient()
    minimal = StandardOrder(
        external_order_id="168934434",
        external_outlet_id="1067984",
        placed_at=None,
        status="delivered",
        currency="AED",
        gross_sales=Decimal("55"),
    )
    order = client._order_from_detail(_DETAIL, minimal)

    # order-level
    assert order.external_order_id == "168934434"
    assert order.gross_sales == Decimal("55")
    assert order.business_date == "2026-08-28"  # Dubai date of 15:36 UTC
    assert order.placed_at is not None and order.placed_at.tzinfo is not None
    assert order.accepted_at is not None and order.delivered_at is not None
    # customer address (Careem gives no name/phone, but the dropoff address)
    assert order.customer_address["area"].startswith("Jumeirah Village")
    assert order.customer_address["building"] == "Oakley Square Residences"
    assert order.customer_name is None
    # captain / delivery agent
    assert order.driver_name == "Taimoor Akram"
    assert order.driver_phone == "+971549931225"
    assert order.driver_status == "TRIP_ENDED"
    # items + modifiers
    assert len(order.items) == 1
    line = order.items[0]
    assert line.item_name == "Lindor Brownies"
    assert line.category_name == "Brownies"
    assert line.quantity == Decimal("1")
    assert line.gross_sales == Decimal("55")
    assert [m.name for m in line.modifiers] == ["3 Pieces"]
    assert line.modifiers[0].unit_price == Decimal("55")
    # status timeline: order lifecycle + captain movement, tz-aware, ordered
    words = [e.status for e in order.status_events]
    assert "pending" in words and "accepted" in words and "delivered" in words
    assert "driver_assigned" in words and "trip_ended" in words
    assert all(e.at.tzinfo is not None for e in order.status_events)
    assert [e.sequence for e in order.status_events] == list(
        range(1, len(order.status_events) + 1)
    )


def test_order_from_detail_falls_back_to_minimal_outlet_when_detail_thin():
    client = CareemClient()
    minimal = StandardOrder(
        external_order_id="999",
        external_outlet_id="1069463",
        business_date="2026-08-27",
        status="delivered",
        currency="AED",
        gross_sales=Decimal("40"),
    )
    order = client._order_from_detail({"id": 999}, minimal)
    assert order.external_outlet_id == "1069463"  # from minimal
    assert order.business_date == "2026-08-27"
    assert order.items == []
    assert order.driver_name is None


_REPORTS = {
    "reports": [
        {
            "id": 763255,
            "status": "SUCCESS",
            "tenant": "FOOD",
            "billableId": 1067984,
            "billableType": "MERCHANT",
            "startDate": "2026-07-01T00:00:00.000+00:00",
            "endDate": "2026-07-31T23:59:59.000+00:00",
            "reportType": "INVOICE",
            "referenceId": "185456184",
        },
        {
            "id": 999999,
            "status": "PENDING",  # not yet generated — must be skipped
            "billableId": 1069463,
            "billableType": "MERCHANT",
            "startDate": "2026-07-01T00:00:00.000+00:00",
            "endDate": "2026-07-31T23:59:59.000+00:00",
            "reportType": "INVOICE",
            "referenceId": "185456999",
        },
    ],
    "paginationInfo": {"pageNumber": 0, "pageSize": 50, "totalRecords": 2},
}

_FILE_URL = "https://prod-generated-tax-invoices.s3.eu-west-1.amazonaws.com/UAE/x/104101335800003-20260808-185456184.pdf?X-Amz-Signature=abc"


@pytest.fixture
def wired(monkeypatch):
    client = CareemClient()

    async def fake_discover(session):
        return [
            {
                "external_company_id": "1026653",
                "external_brand_id": "1029671",
                "external_outlet_id": "1067984",
            }
        ]

    async def fake_request_json(session, method, url, *, json_body=None, params=None):
        if url.endswith("/billingReports/list"):
            # the INVOICE list body must be well-formed
            assert json_body["reportType"] == "INVOICE"
            assert json_body["entryType"] == "FOOD_ORDER"
            assert json_body["tenant"] == "FOOD"
            return _REPORTS
        if url.endswith("/download"):
            # the download must carry the per-account query params
            assert params["billableId"] == "1067984"
            assert params["billableType"] == "MERCHANT"
            assert params["tenant"] == "FOOD"
            return {"fileUrl": _FILE_URL}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(client, "discover_outlets", fake_discover)
    monkeypatch.setattr(client, "request_json", fake_request_json)

    class _Resp:
        status_code = 200
        content = b"%PDF-1.4 fake careem invoice"
        headers = {"content-type": "application/pdf"}

    class _AsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            assert url == _FILE_URL
            return _Resp()

    monkeypatch.setattr(careem_provider.httpx, "AsyncClient", _AsyncClient)

    archived = {}

    def fake_store(
        *, channel, statement_id, filename, body, content_type, extra_files=None
    ):
        archived["call"] = dict(
            channel=channel,
            statement_id=statement_id,
            filename=filename,
            body=body,
            content_type=content_type,
        )
        return StoredStatementInvoice(
            object_key=f"invoices/{channel}/{statement_id}/{filename}",
            content_type=content_type,
            original_filename=filename,
            fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            size_bytes=len(body),
        )

    monkeypatch.setattr(careem_provider, "store_statement_invoice", fake_store)
    return client, archived


async def test_fetch_statements_archives_the_monthly_invoice(wired):
    client, archived = wired
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    until = datetime(2026, 7, 31, 23, 59, tzinfo=timezone.utc)
    result = await client.fetch_statements(since=since, until=until, session=object())

    # Only the SUCCESS report becomes a statement; the PENDING one is skipped.
    assert len(result.statements) == 1
    stmt = result.statements[0]
    assert stmt.statement_id == "185456184"  # the invoice number
    assert stmt.period_start == "2026-07-01"
    assert stmt.period_end == "2026-07-31"
    assert stmt.external_outlet_id == "1067984"
    # The PDF was fetched and archived, and stamped back onto the statement.
    assert archived["call"]["content_type"] == "application/pdf"
    assert archived["call"]["body"].startswith(b"%PDF")
    assert stmt.invoice_object_key.endswith(".pdf")
    assert stmt.invoice_content_type == "application/pdf"
    assert stmt.invoice_fetched_at is not None


async def test_fetch_statements_survives_a_download_failure(wired, monkeypatch):
    """A download that fails still yields the statement metadata (row exists)."""
    client, _ = wired

    async def boom(session, report):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(client, "_download_invoice_pdf", boom)
    result = await client.fetch_statements(
        since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        until=datetime(2026, 7, 31, tzinfo=timezone.utc),
        session=object(),
    )
    assert len(result.statements) == 1
    assert result.statements[0].invoice_object_key is None  # metadata only


async def test_fetch_statements_reports_a_list_failure(monkeypatch):
    client = CareemClient()

    async def fake_discover(session):
        return [
            {
                "external_company_id": "1",
                "external_brand_id": "2",
                "external_outlet_id": "3",
            }
        ]

    async def boom(session, method, url, *, json_body=None, params=None):
        raise RuntimeError("careem 500")

    monkeypatch.setattr(client, "discover_outlets", fake_discover)
    monkeypatch.setattr(client, "request_json", boom)
    result = await client.fetch_statements(
        since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        until=datetime(2026, 7, 31, tzinfo=timezone.utc),
        session=object(),
    )
    assert result.statements == []
    assert "careem invoice list failed" in result.truncation_note


# ── billing entries: per-order fees + VAT (billingEntries/list) ───────────────
#
# A faithful slice of a real billingEntries/list entry (captured live 2026-09-13,
# order 170599972): basket 100 → payout 68.70, fees VAT-exclusive with a matching
# _TAX line each.
_ENTRY = {
    "id": 84541462,
    "entryType": "FOOD_ORDER",
    "referenceId": "170599972",
    "billableId": 1069463,
    "billableType": "MERCHANT",
    "transactionDate": "2026-09-12T15:57:52.000+00:00",
    "status": "PROCESSED",
    "totalAmount": 100,
    "totalPayoutAmount": 68.7,
    "paymentMode": "CASHLESS",
    "transactions": [
        {"transactionType": "FOOD_GROSS_BASKET_AMOUNT", "transactionValue": 100},
        {"transactionType": "BILLING_PLATFORM_FEE", "transactionValue": -23.80952381},
        {
            "transactionType": "BILLING_PLATFORM_FEE_TAX",
            "transactionValue": -1.19047619,
        },
        {"transactionType": "BILLING_PROCESSING_FEE", "transactionValue": -2},
        {"transactionType": "BILLING_PROCESSING_FEE_TAX", "transactionValue": -0.1},
        {"transactionType": "CPLUS_FEE", "transactionValue": -4},
        {"transactionType": "CPLUS_FEE_TAX", "transactionValue": -0.2},
    ],
}


def test_billing_entry_fees_are_vat_inclusive_and_the_net_invariant_holds():
    """The order fee patch books each fee VAT-INCLUSIVE, so
    gross - commission - payment - marketing == payout, exactly."""
    client = CareemClient()
    statements, patches, note = client._finance_from_entries([_ENTRY])

    assert note is None
    assert len(patches) == 1
    p = patches[0]
    assert p.external_order_id == "170599972"
    assert p.external_outlet_id == "1069463"  # billableId -> outlet -> branch
    assert p.gross_sales == Decimal("100")
    assert p.commission_amount == Decimal("25.00")  # 23.80952381 + 1.19047619
    assert p.payment_fee == Decimal("2.10")  # 2 + 0.10
    assert p.marketing_fee == Decimal("4.20")  # 4 + 0.20 (Careem Plus)
    assert p.net_payable == Decimal("68.7")
    # The invariant every channel's economics rests on.
    assert (
        p.gross_sales - p.commission_amount - p.payment_fee - p.marketing_fee
        == p.net_payable
    )


def test_billing_entry_statement_lines_itemise_vat_distinctly():
    """Each fee and its VAT are separate lines (line_type 'vat' for the tax), so a
    VAT roll-up counts the tax as VAT, not as commission/fee."""
    client = CareemClient()
    statements, _patches, _note = client._finance_from_entries([_ENTRY])

    assert len(statements) == 1
    stmt = statements[0]
    assert stmt.statement_id == "CAREEM-ENTRIES-1069463-202609"
    assert stmt.external_outlet_id == "1069463"
    by_cat = {ln.fee_category: ln for ln in stmt.lines}
    # commission split into fee + its VAT
    assert by_cat["commission"].line_type == "fee"
    assert by_cat["commission"].amount == Decimal("-23.80952381")
    assert by_cat["commission_vat"].line_type == "vat"
    assert by_cat["commission_vat"].amount == Decimal("-1.19047619")
    # processing + cplus each split the same way
    assert by_cat["payment_handling"].amount == Decimal("-2")
    assert by_cat["payment_handling_vat"].line_type == "vat"
    assert by_cat["cplus_fee"].amount == Decimal("-4")
    assert by_cat["cplus_vat"].line_type == "vat"
    # gross and net bracket the entry
    assert by_cat["gross_sales"].amount == Decimal("100")
    assert by_cat["net_payable"].amount == Decimal("68.7")
    # Every line carries the order id and the Dubai transaction date.
    assert all(ln.external_order_id == "170599972" for ln in stmt.lines)
    assert all(ln.line_date == "2026-09-12" for ln in stmt.lines)


def test_billing_entries_skip_non_merchant_rows():
    """A company/brand-level roll-up row (no per-order fees) is not a fee patch."""
    client = CareemClient()
    company_row = {**_ENTRY, "billableType": "COMPANY", "referenceId": None}
    statements, patches, note = client._finance_from_entries([company_row])
    assert patches == []
    assert statements == []
    assert "skipped" in note


async def test_fetch_finance_combines_invoices_payouts_and_billing_entries(monkeypatch):
    """`fetch_finance` returns the monthly Tax-Invoice statement AND per-outlet
    billing-entry statements, plus the per-order fee patches, in one result."""
    client = CareemClient()

    async def fake_discover(session):
        return [
            {
                "external_company_id": "1026653",
                "external_brand_id": "1029671",
                "external_outlet_id": "1069463",
            }
        ]

    async def fake_request_json(session, method, url, *, json_body=None, params=None):
        if url.endswith("/billingReports/list"):
            return _REPORTS
        if url.endswith("/download"):
            return {"fileUrl": _FILE_URL}
        if url.endswith("/payoutRequests/list"):
            return {"payoutRequests": [], "paginationInfo": {"totalRecords": 0}}
        if url.endswith("/billingEntries/list"):
            assert json_body["entryType"] == "FOOD_ORDER"
            assert json_body["tenant"] == "FOOD"
            return {"billingEntries": [_ENTRY], "paginationInfo": {"totalRecords": 1}}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(client, "discover_outlets", fake_discover)
    monkeypatch.setattr(client, "request_json", fake_request_json)

    class _Resp:
        status_code = 200
        content = b"%PDF-1.4 fake"
        headers = {"content-type": "application/pdf"}

    class _AsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(careem_provider.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        careem_provider,
        "store_statement_invoice",
        lambda **k: StoredStatementInvoice(
            object_key="k",
            content_type="application/pdf",
            original_filename="f.pdf",
            fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            size_bytes=1,
        ),
    )

    result = await client.fetch_finance(
        session=object(),
        since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        until=datetime(2026, 9, 13, 23, 59, tzinfo=timezone.utc),
    )
    ids = {s.statement_id for s in result.statements}
    assert "185456184" in ids  # the monthly Tax-Invoice PDF
    assert "CAREEM-ENTRIES-1069463-202609" in ids  # the billing-entry statement
    # The per-order fee patch is carried for the finance persist to apply.
    assert [p.external_order_id for p in result.order_fee_updates] == ["170599972"]
    assert result.order_fee_updates[0].commission_amount == Decimal("25.00")
