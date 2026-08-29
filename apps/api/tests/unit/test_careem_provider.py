"""Careem provider — the monthly Tax-Invoice archival.

Careem's partner API is thin: the order feed carries no items/modifiers/customer/
captain or per-order fees, so the fees live only on the monthly Tax Invoice PDF.
`fetch_statements` enumerates those invoices (`billingReports/list`), resolves each
one's pre-signed S3 URL (`billingReports/{id}/download`), fetches the PDF and
archives it. These endpoints/shapes were confirmed against the live console; this
pins the orchestration (enumerate → download → archive → stamp) with them mocked.
"""

from datetime import datetime, timezone

import pytest

from app.services.aggregators.statement_docs import StoredStatementInvoice
from app.services.providers import careem_provider
from app.services.providers.careem_provider import CareemClient

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
