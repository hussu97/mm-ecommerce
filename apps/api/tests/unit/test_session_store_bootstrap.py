"""The `upsert_bootstrap` freshness guard (F-AGG-13).

Three writers reach `upsert_bootstrap` — the headed worker push,
`deliveroo_provider._persist_minted`, and the two overlapping Deliveroo sweeps
that can each mint a token. A stale login committing late used to overwrite a
fresher one, and every following call 401'd. The write is now guarded on
`minted_at`: a mint no newer than the stored `last_bootstrap_at` is dropped, and
the fresher row is left as it is. No DB — a fake session is enough to pin the
decision, since the row is fetched by `_row` and written by attribute.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.aggregator import AggregatorSession
from app.models.base import utcnow
from app.services.aggregators import session_store


class _FakeDb:
    """Hands back one row and records whether anything was written."""

    def __init__(self, row: AggregatorSession | None) -> None:
        self._row = row
        self.added: list = []
        self.flushed = 0

    async def scalar(self, _stmt):
        return self._row

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1


@pytest.fixture(autouse=True)
def _identity_crypto(monkeypatch):
    """Skip Fernet — the guard is what is under test, not the sealing."""
    monkeypatch.setattr(session_store.crypto, "encrypt_json", lambda d: d)


@pytest.mark.asyncio
async def test_a_stale_mint_does_not_clobber_a_fresher_login():
    fresh_at = utcnow()
    row = AggregatorSession(channel="deliveroo", account_ref="")
    row.cookies_encrypted = "the-fresh-blob"
    row.last_bootstrap_at = fresh_at
    db = _FakeDb(row)

    returned = await session_store.upsert_bootstrap(
        db,
        channel="deliveroo",
        cookies={"token": "stale"},
        tokens={"access_token": "stale"},
        header_profile={},
        minted_at=fresh_at - timedelta(minutes=10),  # older than what is stored
    )

    assert returned is row
    assert row.cookies_encrypted == "the-fresh-blob", "the fresher session is kept"
    assert row.last_bootstrap_at == fresh_at
    assert db.flushed == 0, "nothing written for a stale mint"


@pytest.mark.asyncio
async def test_a_newer_mint_overwrites():
    old_at = utcnow() - timedelta(hours=1)
    row = AggregatorSession(channel="deliveroo", account_ref="")
    row.cookies_encrypted = "the-old-blob"
    row.last_bootstrap_at = old_at
    db = _FakeDb(row)
    now = utcnow()

    await session_store.upsert_bootstrap(
        db,
        channel="deliveroo",
        cookies={"token": "new"},
        tokens={"access_token": "new"},
        header_profile={},
        minted_at=now,
    )

    assert row.cookies_encrypted == {"token": "new"}
    assert row.last_bootstrap_at == now
    assert row.status == session_store.SESSION_LIVE
    assert db.flushed == 1


@pytest.mark.asyncio
async def test_a_first_bootstrap_inserts():
    db = _FakeDb(None)
    now = utcnow()

    await session_store.upsert_bootstrap(
        db,
        channel="deliveroo",
        cookies={"token": "first"},
        tokens={"access_token": "first"},
        header_profile={},
        minted_at=now,
    )

    assert len(db.added) == 1
    assert db.added[0].last_bootstrap_at == now
    assert db.flushed == 1
