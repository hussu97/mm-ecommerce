"""Browser audit: where each aggregator exposes statement *invoice* PDFs.

Run after a live login (local or prod-hydrated session):

  STORAGE_STATE_DIR=.aggregator-sessions \\
    .venv/bin/python scripts/statement_invoice_audit.py

Writes JSON under STORAGE_STATE_DIR/statement-invoice-audit/ — no secrets, no uploads.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(os.environ.get("STORAGE_STATE_DIR", ".aggregator-sessions")) / "statement-invoice-audit"
STATE_ROOT = Path(os.environ.get("STORAGE_STATE_DIR", ".aggregator-sessions"))


def _magic(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:2] == b"PK":
        return "application/zip"
    if data.lstrip()[:1] == b"<":
        return "text/html"
    return "unknown"


async def _audit_talabat(page) -> dict:
    from aggregator_bootstrap.channels.login import login_talabat

    await login_talabat(page)
    await page.goto(
        "https://partner-app.talabat.com/finance",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    await page.wait_for_timeout(5_000)
    tab = page.get_by_role("tab", name="Additional statements", exact=True)
    if await tab.count():
        await tab.click()
        await page.wait_for_timeout(3_000)
    downloads: list[dict] = []
    buttons = page.get_by_role("button", name="Download", exact=True)
    count = min(await buttons.count(), 3)
    for i in range(count):
        try:
            async with page.expect_download(timeout=15_000) as dl_info:
                await buttons.nth(i).click(timeout=5_000)
            dl = await dl_info.value
            path = OUT / f"talabat-sample-{i + 1}{Path(dl.suggested_filename).suffix or '.bin'}"
            await dl.save_as(str(path))
            body = path.read_bytes()
            downloads.append(
                {
                    "suggested_filename": dl.suggested_filename,
                    "saved_as": path.name,
                    "bytes": len(body),
                    "magic": _magic(body),
                }
            )
        except Exception as exc:  # noqa: BLE001
            downloads.append({"index": i, "error": str(exc)[:200]})
    return {"finance_url": page.url, "per_row_downloads": downloads}


async def _audit_noon(page) -> dict:
    from aggregator_bootstrap.channels.login import login_noon

    await login_noon(page)
    await page.goto(
        "https://restaurant.noon.partners/_food-restaurant/finance/wallet",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    await page.wait_for_timeout(5_000)
    body_text = (await page.locator("body").inner_text())[:2000]
    pdf_links = await page.locator("a[href*='.pdf'], a[href*='pdf']").evaluate_all(
        "els => els.map(e => ({href: e.href, text: (e.innerText||'').trim()}))"
    )
    export_buttons = await page.get_by_text("Export", exact=False).evaluate_all(
        "els => els.map(e => (e.innerText||'').trim()).filter(Boolean)"
    )
    return {
        "url": page.url,
        "export_labels": export_buttons[:10],
        "pdf_link_count": len(pdf_links),
        "pdf_links_sample": pdf_links[:5],
        "body_snippet": body_text[:500],
    }


async def _audit_keeta(context) -> dict:
    from aggregator_bootstrap.keeta_pull import fetch_keeta_finance

    payloads, note = await fetch_keeta_finance(context, months_back=2)
    samples = []
    for p in payloads[:3]:
        if not isinstance(p, dict):
            continue
        samples.append(
            {
                "keys": list(p.keys())[:20],
                "downloadUrl_present": bool(p.get("downloadUrl")),
                "fileScene": p.get("fileScene") or p.get("taskName"),
            }
        )
    return {"payload_count": len(payloads), "truncation_note": note, "samples": samples}


async def _audit_deliveroo(page) -> dict:
    from aggregator_bootstrap.channels.login import login_deliveroo

    await login_deliveroo(page)
    await page.goto(
        "https://partner-hub.deliveroo.com/reports/invoices?orgId=497912",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    await page.wait_for_timeout(5_000)
    links = await page.locator("a[href*='download'], a[href*='file_type']").evaluate_all(
        "els => els.map(e => ({href: e.href, text: (e.innerText||'').trim()}))"
    )
    return {
        "url": page.url,
        "download_links": links[:15],
        "pdf_links": [link for link in links if "pdf" in link.get("href", "").lower()],
        "csv_links": [
            link for link in links if "statement_csv" in link.get("href", "").lower()
        ],
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for channel, state_name in (
            ("deliveroo", "deliveroo.session.json"),
            ("talabat", "talabat.session.json"),
            ("noon", "noon.session.json"),
            ("keeta", "keeta.session.json"),
        ):
            state_path = STATE_ROOT / state_name
            if not state_path.exists():
                report[channel] = {"skipped": True, "reason": f"missing {state_path}"}
                continue
            context = await browser.new_context(storage_state=str(state_path))
            page = await context.new_page()
            try:
                if channel == "keeta":
                    report[channel] = await _audit_keeta(context)
                elif channel == "talabat":
                    report[channel] = await _audit_talabat(page)
                elif channel == "noon":
                    report[channel] = await _audit_noon(page)
                else:
                    report[channel] = await _audit_deliveroo(page)
            except Exception as exc:  # noqa: BLE001
                report[channel] = {"error": str(exc)[:400]}
            finally:
                await context.close()
        await browser.close()
    out_path = OUT / f"audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
