"""The Deliveroo in-page invoice pull, with `page.evaluate` mocked (no browser).

Two things are proven here: (1) `fetch_deliveroo_invoices` drives the in-page
invoice-list fetch and the per-invoice CSV/PDF downloads and returns the push
payloads, and (2) a Cloudflare interstitial on a download yields a truncation
note and a `None` blob rather than a crash. A third test asserts the pushed
payload shape satisfies `deliveroo_provider.parse_pushed_finance` when the
mm-ecommerce API deps are installed alongside (CI job), and skips otherwise.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[2] / "api"

from aggregator_bootstrap import deliveroo_pull  # noqa: E402
from aggregator_bootstrap.deliveroo_pull import fetch_deliveroo_invoices  # noqa: E402


def _patch_downloads(monkeypatch, *, csv_html: bool = False, pdf_html: bool = False):
    """Mock the browser download-capture seam (`_capture_download`).

    The CSV/PDF now come through Playwright's native download, not an in-page
    fetch — so the unit tests inject bytes at that seam. A "gate" is HTML bytes
    (`<!DOCTYPE…`), which `_download_csv`/`_download_pdf` recognise and drop.
    """

    async def fake_capture(page, statement_id, file_type):
        if file_type == "statement_csv":
            return b"<!DOCTYPE html>challenge" if csv_html else _SAMPLE_CSV.encode()
        if file_type == "statement_pdf":
            return b"<!DOCTYPE ht" if pdf_html else b"%PDF-1.4 fake pdf"
        return None

    monkeypatch.setattr(deliveroo_pull, "_capture_download", fake_capture)


_SAMPLE_CSV = (
    "Invoice Reference,55501\n"
    "Period,Aug 2026\n"
    "\n"
    "Restaurant Name,Order ID,Delivery Date & Time (UTC),Activity,Note,"
    "Order Value (د.إ),Adjustment Net (د.إ),"
    "Deliveroo Commission (د.إ),"
    "Commission / Adjustment VAT (د.إ),Total Payable\n"
    "My Restaurant,ORD-1,2026-08-10 10:00:00,,, 50.00,,  -5.00,  -0.25,  44.75\n"
)

# An invoice with no end date parses as "unknown" and is kept, so the window
# filter never drops it in these unit tests.
_INVOICE = {
    "id": "INV-500",
    "period_start": "2026-08-01",
    "total": {"fractional": 4475},
    "currency": "AED",
}

_PDF_B64 = base64.b64encode(b"%PDF-1.4 fake pdf").decode("ascii")


class _FakePage:
    """Stands in for a Playwright page. The invoice LIST is still fetched in-page
    via `page.evaluate` (JSON.parse); the per-file CSV/PDF now come through the
    native download seam, which the tests mock at `_capture_download` instead."""

    def __init__(self) -> None:
        self.evaluate_calls: list[tuple[str, object]] = []
        self.closed = False

    async def goto(self, *args, **kwargs) -> None:
        return None

    async def wait_for_timeout(self, *args, **kwargs) -> None:
        return None

    async def evaluate(self, script: str, arg: object = None):
        self.evaluate_calls.append((script, arg))
        if "JSON.parse" in script:
            return {"status": 200, "json": {"invoices": [_INVOICE]}}
        return {"status": 200}

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page


async def test_fetch_returns_csv_and_pdf_payloads(monkeypatch):
    _patch_downloads(monkeypatch)
    page = _FakePage()
    payloads, note = await fetch_deliveroo_invoices(
        _FakeContext(page), org_id="497912", since_days=45
    )

    assert len(payloads) == 1
    entry = payloads[0]
    assert entry["invoice"] == _INVOICE
    assert entry["statement_csv"] == _SAMPLE_CSV
    assert entry["statement_pdf_b64"] == _PDF_B64
    assert note is None
    # The invoice list went through the in-page JSON fetch.
    assert any(
        arg
        and isinstance(arg, dict)
        and "/api/invoices?org_id=497912" in str(arg.get("url"))
        for _, arg in page.evaluate_calls
    )
    assert page.closed


async def test_cloudflare_interstitial_on_pdf_yields_note_not_crash(monkeypatch):
    _patch_downloads(monkeypatch, pdf_html=True)
    page = _FakePage()
    payloads, note = await fetch_deliveroo_invoices(
        _FakeContext(page), org_id="497912", since_days=45
    )

    assert len(payloads) == 1
    entry = payloads[0]
    # CSV still came through; the gated PDF is a None blob, not an exception.
    assert entry["statement_csv"] == _SAMPLE_CSV
    assert entry["statement_pdf_b64"] is None
    assert note is not None and "PDF blocked" in note


async def test_cloudflare_interstitial_on_csv_yields_note(monkeypatch):
    _patch_downloads(monkeypatch, csv_html=True)
    page = _FakePage()
    payloads, note = await fetch_deliveroo_invoices(
        _FakeContext(page), org_id="497912", since_days=45
    )

    assert len(payloads) == 1
    assert payloads[0]["statement_csv"] is None
    assert note is not None and "CSV blocked" in note


# ── _capture_download (Playwright native download seam) ───────────────────────


class _FakeDownload:
    def __init__(self, path):
        self._path = path

    async def path(self):
        return self._path


class _ExpectDownloadCM:
    """Async context manager returned by page.expect_download()."""

    def __init__(self, download):
        self._download = download

    async def __aenter__(self):
        async def _value():
            return self._download

        return type("Info", (), {"value": _value()})()

    async def __aexit__(self, *exc):
        return False


class _DownloadPage:
    def __init__(self, download):
        self._download = download

    def expect_download(self, timeout=None):
        return _ExpectDownloadCM(self._download)

    async def goto(self, url, timeout=None):
        # A real navigation to a download URL aborts once it becomes a download.
        raise RuntimeError("net::ERR_ABORTED — became a download")


async def test_capture_download_reads_the_downloaded_file(tmp_path):
    """The native-download path returns the saved attachment's bytes."""
    from aggregator_bootstrap.deliveroo_pull import _capture_download

    f = tmp_path / "attachment.csv"
    f.write_bytes(b"col1,col2\n1,2\n")
    page = _DownloadPage(_FakeDownload(str(f)))
    data = await _capture_download(page, "INV-1", "statement_csv")
    assert data == b"col1,col2\n1,2\n"


async def test_capture_download_none_when_no_path(tmp_path):
    """A download whose path() is None (still in flight / failed) yields None, no crash."""
    from aggregator_bootstrap.deliveroo_pull import _capture_download

    page = _DownloadPage(_FakeDownload(None))
    assert await _capture_download(page, "INV-1", "statement_pdf") is None


def _load_deliveroo_provider():
    if str(_API_ROOT) not in sys.path:
        sys.path.insert(0, str(_API_ROOT))
    return pytest.importorskip(
        "app.services.providers.deliveroo_provider",
        reason="mm-ecommerce API deps (sqlalchemy + app package) not installed",
    )


async def test_fetched_payload_matches_provider_parse_contract(monkeypatch):
    _patch_downloads(monkeypatch)
    page = _FakePage()
    payloads, _ = await fetch_deliveroo_invoices(
        _FakeContext(page), org_id="497912", since_days=45
    )

    deliveroo_provider = _load_deliveroo_provider()
    # Archival needs no bucket configured to parse; store returns None then.
    stmt = deliveroo_provider.provider.parse_pushed_finance(payloads[0])

    assert stmt is not None
    assert stmt.statement_id == "INV-500"
    fee_categories = {line.fee_category for line in stmt.lines}
    assert "gross_sales" in fee_categories
    assert "net_payable" in fee_categories


def test_rst_id_from_webrom_menu_url():
    from aggregator_bootstrap.deliveroo_pull import rst_id_from_webrom_menu_url

    assert (
        rst_id_from_webrom_menu_url("https://webrom.deliveroo.com/rom/497912/menu")
        == "497912"
    )
    assert rst_id_from_webrom_menu_url("https://partner-hub.deliveroo.com/api/") is None


class _MenuResp:
    def __init__(self, url: str, body: dict) -> None:
        self.url = url
        self.status = 200
        self.request = type("R", (), {"method": "GET"})()
        self._body = body

    async def json(self):
        return self._body


class _MenuPage:
    def __init__(self) -> None:
        self.handlers: list = []
        self.url = "https://partner-hub.deliveroo.com/opening-hours"

    def on(self, event, handler) -> None:
        self.handlers.append(handler)

    def remove_listener(self, event, handler) -> None:
        if handler in self.handlers:
            self.handlers.remove(handler)

    async def goto(self, url, **kwargs) -> None:
        self.url = url
        if "opening-hours" in url:
            for h in list(self.handlers):
                h(
                    _MenuResp(
                        "https://webrom.deliveroo.com/rom/497912/menu",
                        {"items": []},
                    )
                )
                h(
                    _MenuResp(
                        "https://partner-hub.deliveroo.com/api/restaurants/497912/opening_hours",
                        {"hours": [{"day": "mon"}]},
                    )
                )

    async def wait_for_timeout(self, *a, **k) -> None:
        return None


class _MenuContext:
    def __init__(self, page: _MenuPage) -> None:
        self.pages = [page]


async def test_fetch_deliveroo_menu_captures_menu_not_hours(monkeypatch):
    """The headed job captures menu only — hours ride httpx on the API."""
    import asyncio

    from aggregator_bootstrap import deliveroo_pull as dp

    orig_wait = _MenuPage.wait_for_timeout

    async def wait_and_yield(self, *a, **k):
        await asyncio.sleep(0)
        return await orig_wait(self, *a, **k)

    monkeypatch.setattr(_MenuPage, "wait_for_timeout", wait_and_yield)
    payloads = await dp.fetch_deliveroo_menu(_MenuContext(_MenuPage()))
    assert len(payloads) == 1
    assert payloads[0]["rst_id"] == "497912"
    assert payloads[0]["menu"] == {"items": []}
    assert "hours" not in payloads[0]
