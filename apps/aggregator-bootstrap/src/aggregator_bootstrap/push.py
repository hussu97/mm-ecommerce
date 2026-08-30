"""Hand a captured session (or Keeta's in-page orders) to the mm-ecommerce API.

The same HTTPS-with-bearer push the standalone scraper used for bulk ingest: the
worker holds the shared `AGGREGATOR_SESSION_PUSH_TOKEN`, the API checks it in
constant time. The session blobs are sealed on arrival, so the token is the only
secret in flight.

`pull_sessions` is the deploy/restart counterpart: the API is the source of
truth, and a new worker with an empty volume hydrates local `storage_state`
files from the encrypted row rather than asking for a login.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from .config import settings


def _headers() -> dict[str, str]:
    token = (settings.AGGREGATOR_SESSION_PUSH_TOKEN or "").strip()
    if not token:
        raise RuntimeError(
            "AGGREGATOR_SESSION_PUSH_TOKEN is empty. Set it in apps/api/.env "
            "(or the worker env). GitHub secrets only reach production after a deploy."
        )
    return {"Authorization": f"Bearer {token}"}


async def push_session(payload: dict[str, Any]) -> dict[str, Any]:
    """POST one captured session to /aggregators/session."""
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/session"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def report_reauth_backoff(channel: str, backoff_until: float | None) -> None:
    """Tell the API when this channel's login will next be re-driven, so the ingest
    can skip a reauth wait the heal daemon will not honour in time. `backoff_until`
    is a unix timestamp (seconds) or None to clear. Best-effort — a reporting blip
    must never fail the heal loop."""
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/worker/reauth-backoff"
    iso = (
        datetime.fromtimestamp(backoff_until, tz=UTC).isoformat()
        if backoff_until is not None
        else None
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url, json={"channel": channel, "backoff_until": iso}, headers=_headers()
        )
        resp.raise_for_status()


async def pull_sessions() -> list[dict[str, Any]]:
    """GET every live session blob the worker is allowed to hydrate from.

    Empty list if the API has nothing yet (first login still to happen), not
    an error. A 401/5xx still raises — those are config/availability faults.
    """
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/worker/sessions"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        body = resp.json()
    if isinstance(body, list):
        return body
    return body.get("sessions") or []


async def push_keeta_orders(payloads: list[dict]) -> dict[str, Any]:
    """POST in-page-fetched Keeta order payloads to /aggregators/keeta/orders.

    Chunks by a few getOrders responses at a time — a full-month multi-shop
    dump can be tens of MB and a single POST times out at the edge before the
    API finishes the Decimal-safe upserts.
    """
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/keeta/orders"
    chunk_size = 2
    ingested = 0
    async with httpx.AsyncClient(timeout=180) as client:
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i : i + chunk_size]
            resp = await client.post(url, json={"payloads": chunk}, headers=_headers())
            resp.raise_for_status()
            body = resp.json()
            ingested += int(body.get("ingested") or 0)
    return {"ingested": ingested}


async def push_keeta_finance(payloads: list[dict]) -> dict[str, Any]:
    """POST in-page-fetched Keeta finance payloads to /aggregators/keeta/finance.

    Mirrors the orders push: chunked to avoid edge timeouts on a large dump.
    Returns accumulated `{"statements": int, "payouts": int}` across all chunks.
    When the worker obtained only download-task metadata (no settled figures yet),
    the API still responds 200 with zero counts; the caller should log that case.
    """
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/keeta/finance"
    chunk_size = 2
    statements = 0
    payouts = 0
    async with httpx.AsyncClient(timeout=180) as client:
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i : i + chunk_size]
            resp = await client.post(url, json={"payloads": chunk}, headers=_headers())
            resp.raise_for_status()
            body = resp.json()
            statements += int(body.get("statements") or 0)
            payouts += int(body.get("payouts") or 0)
    return {"statements": statements, "payouts": payouts}


async def push_deliveroo_finance(payloads: list[dict]) -> dict[str, Any]:
    """POST in-page-fetched Deliveroo invoice payloads to /aggregators/deliveroo/finance.

    Mirrors the Keeta finance push: chunked to avoid edge timeouts on a large
    dump. Each payload carries one invoice's raw dict plus its statement CSV
    text and (when Cloudflare let the download through) its statement PDF as
    base64. Returns accumulated `{"statements": int, "lines": int}`.
    """
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/deliveroo/finance"
    chunk_size = 2
    statements = 0
    lines = 0
    async with httpx.AsyncClient(timeout=180) as client:
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i : i + chunk_size]
            resp = await client.post(url, json={"payloads": chunk}, headers=_headers())
            resp.raise_for_status()
            body = resp.json()
            statements += int(body.get("statements") or 0)
            lines += int(body.get("lines") or 0)
    return {"statements": statements, "lines": lines}
