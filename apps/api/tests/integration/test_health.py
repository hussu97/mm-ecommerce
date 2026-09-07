"""
`/ping` and `/health`, and the WP5 (F-OPS-30) rewrite of the latter.

`/ping` stays the trivial, dependency-free liveness probe the container
healthcheck polls. `/health` used to resolve `Depends(get_db)` BEFORE the
handler ran, so under pool exhaustion it queued for the full `pool_timeout` and
then 500ed — a health probe that hangs exactly when it is most needed. It now
opens its own session inside `asyncio.timeout(2)` and reports both pools' stats
and each background loop's heartbeat age, so these drive the new shape directly:
the DB session factory is faked to make the ok/degraded outcomes deterministic
in a harness with no real database.
"""

from __future__ import annotations

import app.app_setup as app_setup
from app.core import heartbeat


class _Session:
    def __init__(self, *, fail: bool):
        self._fail = fail

    async def execute(self, *_a, **_k):
        if self._fail:
            raise RuntimeError("database unavailable")
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _factory(*, fail: bool):
    def make():
        return _Session(fail=fail)

    return make


class TestPing:
    async def test_ping_is_ok_and_names_the_service(self, client):
        r = await client.get("/ping")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["service"] == "mm-api"


class TestHealth:
    async def test_service_name(self, client):
        data = (await client.get("/health")).json()
        assert data["service"] == "mm-api"

    async def test_returns_200_and_ok_when_the_db_answers(self, client, monkeypatch):
        monkeypatch.setattr(app_setup, "AsyncSessionFactory", _factory(fail=False))
        r = await client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"

    async def test_returns_503_and_reports_the_db_when_it_does_not(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(app_setup, "AsyncSessionFactory", _factory(fail=True))
        r = await client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "error"
        assert body["db"] == "unavailable"

    async def test_reports_both_pools_with_checkout_stats(self, client, monkeypatch):
        monkeypatch.setattr(app_setup, "AsyncSessionFactory", _factory(fail=False))
        pools = (await client.get("/health")).json()["pools"]
        assert set(pools) == {"request", "scheduler"}
        for name in ("request", "scheduler"):
            # The pool exposes at least a current checkout count and a size.
            assert "checked_out" in pools[name]
            assert isinstance(pools[name]["checked_out"], int)
            assert "size" in pools[name]

    async def test_reports_a_heartbeat_age_slot_for_every_loop(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(app_setup, "AsyncSessionFactory", _factory(fail=False))
        ages = (await client.get("/health")).json()["heartbeat_ages_seconds"]
        # Every declared loop has a slot; with no Redis in the harness each reads
        # null ("unknown"), which is the honest answer, not an error.
        assert set(ages) == set(heartbeat.LOOP_NAMES)
