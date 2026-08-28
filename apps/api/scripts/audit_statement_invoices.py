"""Probe live aggregator sessions for statement *invoice* download surfaces.

Run on prod (recommended — sessions are live there):

  docker compose -f docker-compose.prod.yml exec api \\
    python -m scripts.audit_statement_invoices

Uses httpx replay only (Deliveroo, Talabat, Noon). Keeta PDFs need the
bootstrap browser audit (`apps/aggregator-bootstrap/scripts/statement_invoice_audit.py`).

Does not write to the DB or object storage. Prints a JSON report to stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.aggregators import session_store
from app.services.aggregators.session_store import LoadedSession
from app.services.providers import deliveroo_provider, noon_provider, talabat_provider
from app.services.providers.aggregator_base import AggregatorAuthError

# Deliveroo Partner Hub invoice download types seen in the wild / worth probing.
_DELIVEROO_FILE_TYPES = (
    "statement_csv",
    "tax_invoice",
    "tax_invoice_pdf",
    "invoice_pdf",
    "statement_pdf",
    "pdf",
)


def _magic(body: bytes) -> str:
    if body[:4] == b"%PDF":
        return "pdf"
    if body[:2] == b"PK":
        return "zip"
    if body[:5] == b"<?xml" or body[:2] == b"\x50\x4b":
        return "xml_or_zip"
    try:
        text = body[:200].decode("utf-8", errors="ignore")
    except Exception:
        return "binary"
    if text.lstrip().startswith("<!"):
        return "html"
    if "," in text and "\n" in text:
        return "csv_like"
    return "text"


async def _probe_deliveroo(db, session: LoadedSession) -> dict[str, Any]:
    client = deliveroo_provider.provider
    session = await client.prepare_session(db, session)
    org_id = client._org_id(session)  # noqa: SLF001
    invoices = await client._list_invoices(session, org_id)  # noqa: SLF001
    sample = invoices[:3]
    probes: list[dict[str, Any]] = []
    for inv in sample:
        inv_id = str(inv.get("id") or inv.get("invoice_id") or "")
        if not inv_id:
            continue
        for file_type in _DELIVEROO_FILE_TYPES:
            try:
                resp = await client.request_raw(
                    session,
                    "GET",
                    f"https://partner-hub.deliveroo.com/api/invoices/{inv_id}/download",
                    params={
                        "file_type": file_type,
                        "invoice_origin": "restaurant-payments",
                    },
                )
                body = getattr(resp, "content", b"") or b""
                probes.append(
                    {
                        "invoice_id": inv_id,
                        "file_type": file_type,
                        "status": getattr(resp, "status_code", None),
                        "content_type": str(
                            (getattr(resp, "headers", None) or {}).get(
                                "content-type", ""
                            )
                        ),
                        "bytes": len(body),
                        "magic": _magic(body),
                    }
                )
            except AggregatorAuthError as exc:
                return {
                    "error": str(exc),
                    "org_id": org_id,
                    "invoice_count": len(invoices),
                }
            except Exception as exc:  # noqa: BLE001
                probes.append(
                    {
                        "invoice_id": inv_id,
                        "file_type": file_type,
                        "error": str(exc)[:200],
                    }
                )
    return {
        "org_id": org_id,
        "invoice_count": len(invoices),
        "sample_invoice_ids": [str(i.get("id")) for i in sample],
        "download_probes": probes,
    }


async def _probe_noon(_db, session: LoadedSession) -> dict[str, Any]:
    client = noon_provider.provider
    since = datetime.now(timezone.utc) - timedelta(days=14)
    until = datetime.now(timezone.utc)
    try:
        stmt_result = await client.fetch_statements(session, since=since, until=until)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:300]}
    sample = [
        {
            "statement_id": s.statement_id,
            "raw_keys": list((s.raw or {}).keys())[:25],
        }
        for s in stmt_result.statements[:5]
    ]
    return {
        "statements_in_window": len(stmt_result.statements),
        "statement_samples": sample,
        "truncation_note": stmt_result.truncation_note,
        "note": (
            "Noon RMS wallet Export Current View is tabular CSV in automation — "
            "browser audit needed for PDF tax invoice buttons."
        ),
    }


async def _probe_talabat(db, session: LoadedSession) -> dict[str, Any]:
    client = talabat_provider.provider
    session = await client.prepare_session(db, session)
    since = datetime.now(timezone.utc) - timedelta(days=90)
    until = datetime.now(timezone.utc)
    try:
        meta = await client.fetch_statements(session, since=since, until=until)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:300]}
    # Surface attachment hints from GraphQL metadata rows if present on statements.
    attachment_hints: list[dict[str, Any]] = []
    for st in meta.statements[:5]:
        raw = st.raw or {}
        attachment_hints.append(
            {
                "statement_id": st.statement_id,
                "raw_keys": list(raw.keys())[:25],
                "attachments": raw.get("attachments"),
            }
        )
    return {
        "metadata_statements": len(meta.statements),
        "attachment_hints": attachment_hints,
        "note": (
            "Talabat Statement-of-Account PDFs usually come from UI Download buttons "
            "or GraphQL attachments — use bootstrap statement_invoice_audit.py for PDF bytes."
        ),
    }


async def main() -> None:
    from app.core.database import AsyncSessionFactory

    report: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat()}
    async with AsyncSessionFactory() as db:
        for channel, probe in (
            ("deliveroo", _probe_deliveroo),
            ("talabat", _probe_talabat),
            ("noon", _probe_noon),
        ):
            loaded = await session_store.load(db, channel)
            if loaded is None or loaded.status != "live":
                report[channel] = {
                    "skipped": True,
                    "status": getattr(loaded, "status", None),
                }
                continue
            enriched = await session_store.enrich_session(db, loaded)
            report[channel] = await probe(db, enriched)

    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    asyncio.run(main())
