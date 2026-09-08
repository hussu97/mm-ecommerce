"""The APNs transport: one reused client, and all registers pushed at once.

F-POS-12 — `ApnsClient.send` rebuilt a fresh `httpx.AsyncClient` (a new TLS
handshake and connection) on every token, and `_send_to_branch` awaited them one
at a time. The client is now pooled and reused, and the per-token sends run under
one `asyncio.gather`; dead-token revocation still happens on the caller's session
afterwards, so nothing about the write ordering changes.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import push_service
from app.services.providers.apns_provider import ApnsClient, ApnsResult


@pytest.mark.asyncio
async def test_apns_reuses_one_pooled_client():
    client = ApnsClient()
    try:
        first = client._http()
        assert client._http() is first, "the client must be reused, not rebuilt"
        await client.aclose()
        assert client._http() is not first, "a closed client is rebuilt"
    finally:
        await client.aclose()


def _token(tok: str) -> SimpleNamespace:
    return SimpleNamespace(
        token=tok,
        bundle_id="com.mm.pos",
        is_sandbox=False,
        revoked_at=None,
        revoked_reason=None,
    )


@pytest.mark.asyncio
async def test_send_to_branch_gathers_all_and_retires_dead_tokens(monkeypatch):
    rows = [_token("good" + "a" * 16), _token("dead" + "b" * 16)]

    async def fake_send(*, token, **_kw):
        if token.startswith("dead"):
            return ApnsResult(
                token=token, delivered=False, dead=True, reason="Unregistered"
            )
        return ApnsResult(token=token, delivered=True)

    monkeypatch.setattr(push_service, "is_enabled", lambda: True)
    monkeypatch.setattr(
        push_service, "tokens_for_branch", AsyncMock(return_value=rows)
    )
    send = AsyncMock(side_effect=fake_send)
    monkeypatch.setattr(push_service.provider, "send", send)

    db = AsyncMock()
    delivered = await push_service._send_to_branch(
        db, uuid.uuid4(), payload={"a": 1}, collapse_id="order-1"
    )

    assert delivered == 1
    assert send.await_count == 2, "every token is attempted, not stopped at the first"
    assert rows[0].revoked_at is None, "a live token is left alone"
    assert rows[1].revoked_at is not None, "a dead token is retired"
    assert rows[1].revoked_reason == "Unregistered"


@pytest.mark.asyncio
async def test_send_to_branch_is_a_noop_with_no_tokens(monkeypatch):
    monkeypatch.setattr(push_service, "is_enabled", lambda: True)
    monkeypatch.setattr(push_service, "tokens_for_branch", AsyncMock(return_value=[]))
    send = AsyncMock()
    monkeypatch.setattr(push_service.provider, "send", send)
    assert (
        await push_service._send_to_branch(
            AsyncMock(), uuid.uuid4(), payload={}, collapse_id="c"
        )
        == 0
    )
    send.assert_not_awaited()
