"""Deliveroo's in-page invoice download — the one fetch httpx cannot make.

The Partner Hub *invoice list* (`GET /api/invoices?org_id=`) replays fine over
httpx from a captured session, but the invoice *download*
(`GET /api/invoices/{id}/download?file_type=statement_csv|statement_pdf`) sits
behind Cloudflare and 403s any request that does not carry the page's
IP-bound `cf_clearance`. Headed real Chrome under Xvfb passes that wall (proven
on the VM), so the download has to run *in the page* — the same shape as the
Keeta in-page pull, except Deliveroo's requests are plain GETs (no `mtgsig`),
so all this needs is the page's own `fetch` carrying its cookies and clearance.

`fetch_deliveroo_invoices` opens a page on `partner-hub.deliveroo.com` (so the
browser holds CF clearance), fetches the invoice list JSON in-page, and for each
invoice inside the `since_days` window downloads the statement CSV and PDF via
Playwright's **native download capture** (`_capture_download`). The download is
NOT an in-page `fetch`: verified on the VM, the download endpoint 302-redirects
to a cross-origin signed URL, which an in-page `fetch(credentials:"include")`
cannot follow — it fails CORS with a bare `TypeError: Failed to fetch`, even
after a fresh login clears the 401. A real navigation (`page.goto` under
`page.expect_download`) follows the redirect, carries the session cookies, and
lands the attachment; headed real Chrome under Xvfb clears Cloudflare along the
way (the block is 401 auth, no longer a 403 interstitial). The caller
(`warm.pull_deliveroo_invoices_in_page`) does an email/password re-login first
to refresh the browser token, because the hydrated web session goes stale fast.

It returns `(payloads, truncation_note)` where each payload is
`{"invoice": <raw invoice dict>, "statement_csv": <text|None>,
"statement_pdf_b64": <b64|None>}` — the exact shape the mm-ecommerce
`deliveroo_provider.parse_pushed_finance` turns into a statement (lines from the
CSV, an archived VAT PDF). A failed/gated download is a truncation note and a
`None` blob, never a crash. Total bytes are capped by `_MAX_DOWNLOAD_BYTES`.

Playwright is imported nowhere here — the function is handed an already-open
context, so this module (and its tests) import without the browser library. Only
the invoice LIST uses `evaluate_in_page` (a same-origin JSON fetch in the page's
main world, so the page's cookies and `cf_clearance` are visible to it).
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .engine import evaluate_in_page

logger = logging.getLogger(__name__)

_HUB = "https://partner-hub.deliveroo.com"
_API = f"{_HUB}/api"

#: The page to sit on so the browser holds a valid `cf_clearance` before the
#: in-page fetches resolve against the same origin.
DELIVEROO_HUB_ROUTE = f"{_HUB}/"

#: Guard the total downloaded finance bytes so one runaway window cannot balloon
#: the push. base64 inflates ~4/3, so this caps the pushed payload near ~64 MB.
_MAX_DOWNLOAD_BYTES = 48 * 1024 * 1024

# The page's own `fetch`, so its cookies and `cf_clearance` ride along. The SPA
# also sends the JWT as an `Authorization: Bearer`, so we lift the `token`
# cookie and mirror that — some routes accept only the bearer.
_GET_JSON_JS = """
async ({ url }) => {
  const token = (document.cookie.match(/(?:^|;\\s*)token=([^;]+)/) || [])[1];
  const headers = { "accept": "application/json, text/plain, */*" };
  if (token) headers["authorization"] = "Bearer " + decodeURIComponent(token);
  const response = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers,
  });
  const text = await response.text();
  try {
    return { status: response.status, json: JSON.parse(text) };
  } catch (e) {
    return { status: response.status, text };
  }
}
"""

# The statement CSV/PDF are fetched with Playwright's native download capture
# (see `_capture_download`), not an in-page `fetch`: the download 302-redirects
# to a cross-origin signed URL that an in-page fetch cannot follow. Only the
# invoice LIST (same-origin JSON) uses `_GET_JSON_JS` above.


def _download_url(invoice_id: str, file_type: str) -> str:
    """The download endpoint for one invoice file_type, as the SPA calls it."""
    return (
        f"{_API}/invoices/{invoice_id}/download"
        f"?file_type={file_type}&invoice_origin=restaurant-payments"
    )


def _invoice_rows(listing: Any) -> list[dict]:
    """The invoice rows out of the list response, whether bare or wrapped."""
    if isinstance(listing, list):
        return [row for row in listing if isinstance(row, dict)]
    if isinstance(listing, dict):
        for key in ("invoices", "data"):
            value = listing.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _first(mapping: dict, *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def _parse_end_date(value: Any) -> datetime | None:
    """A best-effort `datetime` from an invoice end/period date. None if unknown."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%a %d %b %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _within_window(invoice: dict, cutoff: datetime) -> bool:
    """Whether the invoice's end date is on/after the cutoff.

    An unparseable / absent date is kept (unknown is not "too old") — the byte
    budget bounds the pull, and dropping an undated invoice would silently lose
    a statement.
    """
    end = _parse_end_date(
        _first(invoice, "period_end", "end_date", "to", "billing_end")
    )
    if end is None:
        return True
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end >= cutoff


def _is_cf_html(text: str | None, content_type: str | None = None) -> bool:
    """Whether a download response is a Cloudflare gate page, not the file."""
    if content_type and "text/html" in content_type:
        return True
    if not text:
        return False
    return text.lstrip()[:2].startswith("<!")


async def fetch_deliveroo_invoices(
    context: Any, *, org_id: str, since_days: int = 45
) -> tuple[list[dict], str | None]:
    """Pull Deliveroo invoice list + CSV/PDF downloads in-page, as push payloads.

    Opens a page on the hub (so the browser holds `cf_clearance`), fetches the
    invoice list JSON in-page, then for each invoice inside the `since_days`
    window downloads the statement CSV (text) and statement PDF (base64) in-page.

    Returns `(payloads, truncation_note)`; each payload is
    `{"invoice": <raw dict>, "statement_csv": <text|None>,
    "statement_pdf_b64": <b64|None>}`. The note is set when a download was gated
    (Cloudflare), failed, or the byte budget tripped. Each invoice is isolated —
    one bad download is logged and skipped, never aborting the rest.
    """
    page = await context.new_page()
    payloads: list[dict] = []
    notes: list[str] = []
    downloaded_bytes = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(since_days, 0))

    def _budget_left() -> bool:
        return downloaded_bytes < _MAX_DOWNLOAD_BYTES

    try:
        await page.goto(
            DELIVEROO_HUB_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        # Let any Cloudflare JS challenge settle so `cf_clearance` is in place.
        await page.wait_for_timeout(6_000)

        try:
            listing = await evaluate_in_page(
                page,
                _GET_JSON_JS,
                {"url": f"{_API}/invoices?org_id={org_id}"},
            )
        except Exception:  # noqa: BLE001 — a broken list must not crash the warm
            logger.exception("deliveroo: invoice list fetch failed")
            return [], "Deliveroo invoice list fetch failed (see logs)."

        rows = _invoice_rows(
            listing.get("json") if isinstance(listing, dict) else listing
        )
        if not rows:
            note = "Deliveroo invoice list returned no rows"
            if isinstance(listing, dict) and _is_cf_html(listing.get("text")):
                note = "Deliveroo invoice list blocked by a Cloudflare interstitial"
            return [], note

        for invoice in rows:
            invoice_id = _first(invoice, "id", "invoice_id", "reference", "number")
            if invoice_id is None:
                continue
            if not _within_window(invoice, cutoff):
                continue
            statement_id = str(invoice_id)
            if not _budget_left():
                notes.append("byte budget reached before all invoices downloaded")
                break
            try:
                csv_text = await _download_csv(page, statement_id)
                pdf_b64, pdf_len = await _download_pdf(page, statement_id)
            except Exception:  # noqa: BLE001 — one bad invoice must not stop the rest
                logger.warning(
                    "deliveroo: download failed for invoice %s", statement_id
                )
                notes.append(f"invoice {statement_id} download failed")
                # Still push the invoice metadata so the statement summary lands.
                payloads.append(
                    {
                        "invoice": invoice,
                        "statement_csv": None,
                        "statement_pdf_b64": None,
                    }
                )
                continue

            if csv_text is None:
                notes.append(f"invoice {statement_id} CSV blocked (Cloudflare)")
            else:
                downloaded_bytes += len(csv_text.encode("utf-8", errors="ignore"))
            if pdf_b64 is None:
                notes.append(f"invoice {statement_id} PDF blocked (Cloudflare)")
            else:
                downloaded_bytes += pdf_len

            payloads.append(
                {
                    "invoice": invoice,
                    "statement_csv": csv_text,
                    "statement_pdf_b64": pdf_b64,
                }
            )

        if not payloads and not notes:
            notes.append("No Deliveroo invoices in the requested window.")
    except Exception:  # noqa: BLE001 — best-effort; a broken page must not fail the warm
        logger.exception("deliveroo invoice pull failed — skipped")
        notes.append("Deliveroo invoice pull failed (see logs).")
    finally:
        await page.close()

    return payloads, ("; ".join(notes) if notes else None)


#: How long to wait for a download to start before treating the file_type as
#: "not served as an attachment" (e.g. a PDF the portal renders inline).
_DOWNLOAD_TIMEOUT_MS = 30_000


async def _capture_download(
    page: Any, statement_id: str, file_type: str
) -> bytes | None:
    """One invoice file, via the browser's NATIVE download rather than an in-page
    fetch.

    The download endpoint 302-redirects to a cross-origin signed URL, and an
    in-page ``fetch(credentials:"include")`` cannot follow that redirect — it
    fails CORS with a bare ``TypeError: Failed to fetch`` (verified on the VM,
    even after a fresh login clears the 401). A real navigation follows the
    redirect, carries the session cookies, and lands the attachment, which
    Playwright surfaces as a ``download`` event. Returns the bytes, or None when
    no download starts (a file_type served inline, a gate, or a timeout).
    """
    url = _download_url(statement_id, file_type)
    try:
        async with page.expect_download(timeout=_DOWNLOAD_TIMEOUT_MS) as info:
            try:
                await page.goto(url, timeout=_DOWNLOAD_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 — goto aborts once it becomes a download
                pass
        download = await info.value
        path = await download.path()
        if path is None:
            return None
        with open(path, "rb") as handle:
            return handle.read()
    except Exception:  # noqa: BLE001 — no download event / timeout / gate
        return None


async def _download_csv(page: Any, statement_id: str) -> str | None:
    """The statement CSV text, or None when it did not download / is a gate page."""
    data = await _capture_download(page, statement_id, "statement_csv")
    if not data:
        return None
    text = data.decode("utf-8-sig", errors="replace")
    if _is_cf_html(text):
        return None
    return text


async def _download_pdf(page: Any, statement_id: str) -> tuple[str | None, int]:
    """The statement PDF as (base64, byte length), or (None, 0) when unavailable.

    Deliveroo does not always serve a PDF as an attachment for a given invoice
    (the CSV is the line-level source that matters); a missing PDF is a None, not
    a failure.
    """
    data = await _capture_download(page, statement_id, "statement_pdf")
    if not data or data[:16].lstrip().startswith(b"<!"):
        return None, 0
    return base64.b64encode(data).decode("ascii"), len(data)


# ── Menu + hours (catalog sync feed) ──────────────────────────────────────────
# Discovered live 2026-09-01: the Partner Hub's Opening-Hours page loads BOTH the
# webrom menu and the opening-hours in one go — the hub auto-exchanges a webrom
# token (`/api-gw/webrom/logon-pass`), so no separate menu login is needed. We sit
# on that page and capture the two responses (the menu is a cross-origin webrom
# host an in-page fetch cannot reach without the webrom bearer, so capture beats
# re-issuing it). One `{rst_id, menu, hours}` payload per restaurant the hub holds.
DELIVEROO_OPENING_HOURS_ROUTE = f"{_HUB}/opening-hours"

#: webrom menu — `.../rom/{rst}/menu`; hub hours — `.../api/restaurants/{rst}/opening_hours`.
_WEBROM_MENU_MARK = "/rom/"
_OPENING_HOURS_MARK = "/opening_hours"


async def fetch_deliveroo_menu_hours(context: Any) -> list[dict]:
    """The Deliveroo menu + opening hours for the hub's restaurant, read by sitting
    on the Opening-Hours page and capturing the two JSON responses it fires.

    Returns `[{rst_id, menu, hours}]` for the API's `parse_deliveroo_menu` /
    `parse_deliveroo_hours`. Mirrors `fetch_deliveroo_invoices`' context use; the
    menu is captured (not re-fetched) because it is cross-origin (webrom) and the
    hub's own request already carries the exchanged webrom bearer."""
    import asyncio

    grabbed: dict[str, Any] = {}

    seen_urls: list[str] = []

    async def _on_response(resp: Any) -> None:
        # Read the body AT RESPONSE TIME — Playwright evicts it after navigation, so
        # collecting the objects and reading later comes back empty (seen live).
        url = resp.url
        # Diagnostic: record request URLs that could be the menu/hours feeds so a
        # run that captures nothing shows what the page actually fired (webrom token,
        # a restaurant picker, a changed path, or a Cloudflare gate).
        low = url.lower()
        # Broad diagnostic: log the whole non-noise API surface the page fires, so a
        # run that captures nothing reveals where Deliveroo moved the menu/hours feeds
        # (the Partner Hub was restructured onto an /api-gw/ gateway — the old
        # /rom/{rst}/menu + /api/restaurants/{rst}/opening_hours no longer fire).
        _noise = (
            "sentry",
            "/track",
            "intercom",
            "sierra.chat",
            "/events",
            "segment",
            "datadog",
            "launchdarkly",
            "fullstory",
            "google",
            "gstatic",
            "hotjar",
            ".png",
            ".jpg",
            ".jpeg",
            ".css",
            ".js",
            ".woff",
            ".svg",
            ".ico",
            "cloudflareinsights",
            "doubleclick",
        )
        _keys = (
            "menu",
            "item",
            "catalog",
            "hour",
            "schedul",
            "open",
            "availab",
            "rom",
            "webrom",
            "site",
            "restaurant",
            "brand",
            "graphql",
            "query",
        )
        if not any(n in low for n in _noise) and (
            ("/api" in low and "deliveroo.com" in low) or any(k in low for k in _keys)
        ):
            entry = f"{resp.status} {getattr(resp.request, 'method', '?')} {url.split('?')[0]}"
            if entry not in seen_urls:
                seen_urls.append(entry)
        try:
            if _WEBROM_MENU_MARK in url and url.rstrip("/").endswith("menu"):
                grabbed["menu"] = await resp.json()
            elif _OPENING_HOURS_MARK in url and "restaurants/" in url:
                grabbed["rst_id"] = url.split("restaurants/")[1].split("/")[0]
                grabbed["hours"] = await resp.json()
        except Exception:  # noqa: BLE001 — a body we can't read is simply skipped
            pass

    # REUSE the context's existing page — the caller's re-login already cleared
    # Cloudflare and holds cf_clearance on it. A fresh `new_page()` faces the
    # Cloudflare challenge from scratch and its goto hangs to the timeout (seen live).
    existing = context.pages
    page = existing[0] if existing else await context.new_page()
    opened_here = not existing

    def _handler(resp: Any) -> None:
        asyncio.create_task(_on_response(resp))  # noqa: RUF006 — fire-and-forget

    page.on("response", _handler)
    try:
        await page.goto(
            DELIVEROO_HUB_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        # Let the Cloudflare challenge settle so cf_clearance is in place.
        await page.wait_for_timeout(9_000)
        await page.goto(
            DELIVEROO_OPENING_HOURS_ROUTE, wait_until="domcontentloaded", timeout=60_000
        )
        await page.wait_for_timeout(14_000)
    finally:
        page.remove_listener("response", _handler)
        if opened_here:
            await page.close()

    if not grabbed.get("menu") and not grabbed.get("hours"):
        logger.warning(
            "deliveroo: no menu/hours captured on the opening-hours page; final url=%s; "
            "candidate responses seen=%s",
            page.url,
            seen_urls[:30] or "(none — page fired no menu/hours/restaurant requests)",
        )
        return []
    return [
        {
            "rst_id": grabbed.get("rst_id"),
            "menu": grabbed.get("menu"),
            "hours": grabbed.get("hours"),
        }
    ]
