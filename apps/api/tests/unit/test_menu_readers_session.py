"""The catalog readers' session gate (F-AGG-15).

Every marketplace read now goes through `menu_readers._session_for`, mirroring
`ingest._session_for` — load, enrich, prepare, and gate on `is_session_usable`.
It **raises** rather than returning None (a read has no run to skip; a None session
handed to a provider was the AttributeError these readers stored as the snapshot
error), and it never replays a status-dead or past-expiry session at the portal.
"""

from __future__ import annotations

import pytest

from app.services.aggregators import menu_readers, session_store
from app.services.providers.aggregator_base import AggregatorUnavailableError


@pytest.fixture
def _stub_store(monkeypatch):
    async def enrich(db, s):
        return s

    monkeypatch.setattr(session_store, "enrich_session", enrich)
    monkeypatch.setattr(
        session_store, "session_unusable_reason", lambda s: "never bootstrapped"
    )


@pytest.mark.asyncio
async def test_session_for_raises_when_the_session_is_not_usable(
    monkeypatch, _stub_store
):
    async def load(db, channel):
        return None

    monkeypatch.setattr(session_store, "load", load)
    monkeypatch.setattr(session_store, "is_session_usable", lambda s: False)

    with pytest.raises(AggregatorUnavailableError, match="session not usable"):
        await menu_readers._session_for(None, "careem", object())


@pytest.mark.asyncio
async def test_session_for_returns_a_usable_session(monkeypatch, _stub_store):
    sentinel = object()

    async def load(db, channel):
        return sentinel

    monkeypatch.setattr(session_store, "load", load)
    monkeypatch.setattr(session_store, "is_session_usable", lambda s: True)

    out = await menu_readers._session_for(None, "careem", object())
    assert out is sentinel


@pytest.mark.asyncio
async def test_session_for_runs_prepare_session_when_present(monkeypatch, _stub_store):
    """Deliveroo's httpx JWT mint is `prepare_session`; a provider that has one has
    it called, and its return value (a fresh object) is what gets gated + returned."""

    async def load(db, channel):
        return object()

    prepared = object()

    class _Provider:
        async def prepare_session(self, db, session):
            return prepared

    monkeypatch.setattr(session_store, "load", load)
    monkeypatch.setattr(session_store, "is_session_usable", lambda s: True)

    out = await menu_readers._session_for(None, "deliveroo", _Provider())
    assert out is prepared
