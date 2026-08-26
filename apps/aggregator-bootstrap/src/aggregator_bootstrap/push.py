"""Hand a captured session (or Keeta's in-page orders) to the mm-ecommerce API.

The same HTTPS-with-bearer push the standalone scraper used for bulk ingest: the
worker holds the shared `AGGREGATOR_SESSION_PUSH_TOKEN`, the API checks it in
constant time. The session blobs are sealed on arrival, so the token is the only
secret in flight.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import settings


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.AGGREGATOR_SESSION_PUSH_TOKEN}"}


async def push_session(payload: dict[str, Any]) -> dict[str, Any]:
    """POST one captured session to /aggregators/session."""
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/session"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def push_keeta_orders(payloads: list[dict]) -> dict[str, Any]:
    """POST in-page-fetched Keeta order payloads to /aggregators/keeta/orders."""
    url = f"{settings.AGGREGATOR_API_URL}/api/v1/aggregators/keeta/orders"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json={"payloads": payloads}, headers=_headers())
        resp.raise_for_status()
        return resp.json()
