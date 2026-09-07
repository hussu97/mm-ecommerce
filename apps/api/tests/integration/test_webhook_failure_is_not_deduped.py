"""
A courier webhook that raises mid-apply must not lose the update (F-COU-4),
and must not commit half-applied work under the 200 (F-ORD-17).

The dedup `webhook_events` row is inserted on the **same request session** as
the transition it guards, *before* the work. The request-scoped `get_db`
dependency commits on a clean return. So the original routes had a hole: an
apply that raised was caught, the route still returned 200, and `get_db` then
committed the dedup row while the transition that failed had been rolled back
with the exception. The provider's retry of the identical event was then
recognised as a duplicate and discarded — the status change lost for good — and
any work that *had* landed before the raise was committed half-done.

The fix rolls the request session back inside each courier route's exception
handler before returning 200, which discards the dedup row together with the
half-applied work so the retry re-applies, and reports the swallow through
`capture_issue` so it is no longer silent.

Two layers are asserted here:

* `TestTheRouteRollsBackAndReports` drives the real routes through the test
  client with a mocked session and proves the contract every courier route now
  keeps — still 200, the session rolled back, the failure reported. It runs
  everywhere.
* `TestTheDedupRowDoesNotSurviveAFailedApply` proves the end-to-end behaviour
  against a **real** Postgres — the dedup row genuinely does not survive a
  failed call, and an identical retry (apply now succeeding) is applied. It
  needs the on-conflict insert and a real transaction, so it SKIPs unless
  `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

LALAMOVE_URL = "/api/v1/webhooks/lalamove"
NOON_STATUS_URL = "/api/v1/webhooks/noon-send"
NOON_TRACKING_URL = "/api/v1/webhooks/noon-send/tracking"
SLIDER_URL = "/api/v1/webhooks/slider"

NOON_KEY = "webhook-shared-secret"
SLIDER_TOKEN = "slider-production-token"
SLIDER_HEADER = "X-Slider-Token"


class _Boom(RuntimeError):
    """A bug inside the apply step — not a provider validation error."""


# ── Layer 1: the route contract, everywhere ─────────────────────────────────


@pytest.fixture(autouse=True)
def courier_secrets(monkeypatch):
    """The keys/tokens the routes check before they reach the apply step."""
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "NOON_SEND_WEBHOOK_API_KEY", NOON_KEY)
    monkeypatch.setattr(cfg.settings, "NOON_SEND_ENFORCE_WEBHOOK_KEY", True)
    monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_TOKEN", SLIDER_TOKEN)
    monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_HEADER", SLIDER_HEADER)


@pytest.fixture
def captured_issues(monkeypatch):
    """Spy on the alerting funnel the routes now call on a swallowed failure."""
    calls: list[tuple[str, dict]] = []
    import app.api.v1.webhooks as webhooks

    monkeypatch.setattr(
        webhooks,
        "capture_issue",
        lambda message, **kw: calls.append((message, kw)),
    )
    return calls


def _make_apply_raise(monkeypatch):
    """
    Make every courier's apply step raise, so each route falls into the generic
    ``except Exception`` handler under test rather than a provider-specific one.

    Patched at the service seam the route calls, which is exactly what the fix's
    exception handler wraps.
    """
    from app.services.couriers import (
        lalamove_service,
        noon_send_service,
        slider_service,
    )

    async def boom(*_args, **_kwargs):
        raise _Boom("apply blew up")

    monkeypatch.setattr(lalamove_service, "handle_webhook", boom)
    monkeypatch.setattr(noon_send_service, "handle_webhook", boom)
    monkeypatch.setattr(noon_send_service, "handle_tracking_webhook", boom)
    monkeypatch.setattr(slider_service, "handle_webhook", boom)


CASES = [
    pytest.param(
        LALAMOVE_URL,
        {},
        {"eventId": "E-1", "eventType": "DELIVERY_COMPLETED"},
        "lalamove",
        "status",
        id="lalamove-status",
    ),
    pytest.param(
        NOON_STATUS_URL,
        {"X-API-Key": NOON_KEY},
        {"order_nr": "TASK-1", "status_code": "picked_up", "order_reference": "MM-1"},
        "noon_send",
        "status",
        id="noon-send-status",
    ),
    pytest.param(
        NOON_TRACKING_URL,
        {"X-API-Key": NOON_KEY},
        {"order_nr": "TASK-1", "da_details": {"location": "x"}},
        "noon_send",
        "tracking",
        id="noon-send-tracking",
    ),
    pytest.param(
        SLIDER_URL,
        {SLIDER_HEADER: SLIDER_TOKEN},
        {"order_number": 4820193, "order_id": "MM-1", "status": "delivered"},
        "slider",
        "status",
        id="slider-status",
    ),
]


class TestTheRouteRollsBackAndReports:
    """The contract every courier route now keeps when its apply step raises."""

    @pytest.mark.parametrize("url, headers, body, provider, endpoint", CASES)
    async def test_a_failed_apply_is_rolled_back_but_still_answered_200(
        self,
        client,
        mock_db,
        captured_issues,
        monkeypatch,
        url,
        headers,
        body,
        provider,
        endpoint,
    ):
        _make_apply_raise(monkeypatch)

        response = await client.post(url, json=body, headers=headers)

        # (a) The provider still gets a 200 — a fail-loud response is retried
        # then has the URL disabled, losing every later order's status.
        assert response.status_code == 200
        assert response.json()["received"] is True

        # The request session was rolled back, so nothing half-applied — and the
        # dedup `webhook_events` row inserted before the work — can be committed
        # by `get_db`. Without the fix this is never called.
        mock_db.rollback.assert_awaited()

        # The swallow is no longer silent: one fingerprinted, tagged report.
        assert captured_issues, "a swallowed apply failure must be reported"
        message, kw = captured_issues[-1]
        assert provider in message
        assert kw["fingerprint"] == ["courier-webhook-apply-failed", provider]
        assert kw["tags"] == {"provider": provider, "endpoint": endpoint}


# ── Layer 2: the dedup row genuinely does not survive (real Postgres) ────────

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@db_required
class TestTheDedupRowDoesNotSurviveAFailedApply:
    """
    End-to-end, against a real transaction and the real on-conflict insert.

    The apply *step* is stubbed — that is the "patch the apply to raise" the
    fix's handler is built around — but the dedup insert, the request session,
    and `get_db`'s commit-on-success are all real, which is the whole point: a
    mock cannot tell you whether Postgres kept the row.
    """

    @pytest.fixture
    async def maker(self):
        engine = create_async_engine(DATABASE_URL)
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    @pytest.fixture
    async def real_db_client(self, maker):
        """A client whose `get_db` behaves exactly as production's does."""
        from httpx import ASGITransport, AsyncClient

        from app.core.deps import get_db
        from app.main import app

        async def override_get_db():
            async with maker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_db] = override_get_db
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            yield ac
        app.dependency_overrides.clear()

    async def _event_count(self, maker, event_id: str) -> int:
        from app.models.webhook_event import WebhookEvent

        async with maker() as db:
            return int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(WebhookEvent)
                        .where(WebhookEvent.event_id == event_id)
                    )
                ).scalar()
                or 0
            )

    async def test_failure_leaves_no_row_and_the_retry_is_applied(
        self, real_db_client, maker, monkeypatch
    ):
        import uuid

        from app.models.webhook_event import WebhookEvent
        from app.services.couriers import noon_send_service

        # A unique task so the dedup event_id belongs to this test alone.
        #
        # A *terminal* status on purpose. Since F-COU-8 the dedup key is
        # deterministic — `(task, status)` — only for terminal statuses; a
        # non-terminal push is given a per-push nonce so a repeated `assigned`
        # (a rider swap) is never collapsed. This test is about the *other*
        # property: that a dedup row is rolled back with a failed apply so the
        # retry is not blocked. That only means anything where a surviving row
        # *would* block the retry — i.e. where the retry reuses the key — which
        # is the terminal case. So `delivered`, whose key both pushes share.
        task = f"TASK-{uuid.uuid4().hex[:12]}"
        push = {
            "order_nr": task,
            "status_code": "delivered",
            "order_reference": "MM-DEDUP-TEST",
            "timestamp": "2026-09-07 08:55:14",
        }
        event_id = noon_send_service._event_id(push)

        # A matching delivery is found, so the route reaches the apply step. We
        # only need it to be non-None; the apply itself is what we control.
        monkeypatch.setattr(
            noon_send_service,
            "_delivery_for",
            lambda db, payload, **_kw: _async(object()),
        )

        try:
            # First push: the apply raises mid-transition.
            async def boom(*_a, **_k):
                raise _Boom("apply blew up")

            monkeypatch.setattr(noon_send_service, "apply_webhook", boom)

            response = await real_db_client.post(
                NOON_STATUS_URL, json=push, headers={"X-API-Key": NOON_KEY}
            )
            assert response.status_code == 200  # (a)

            # (b) The dedup row was rolled back with the failed work — it must
            # not survive to block the retry.
            assert await self._event_count(maker, event_id) == 0

            # The retry: same event, apply now succeeds.
            async def ok(db, payload, delivery):
                return delivery

            monkeypatch.setattr(noon_send_service, "apply_webhook", ok)

            retry = await real_db_client.post(
                NOON_STATUS_URL, json=push, headers={"X-API-Key": NOON_KEY}
            )
            assert retry.status_code == 200
            body = retry.json()
            # (c) It was applied, not discarded as a duplicate.
            assert body.get("matched") is True
            assert body.get("duplicate") is not True
            assert await self._event_count(maker, event_id) == 1
        finally:
            async with maker() as db:
                await db.execute(
                    delete(WebhookEvent).where(WebhookEvent.event_id == event_id)
                )
                await db.commit()


async def _async(value):
    """Wrap a plain value in an awaitable, for patching an async seam."""
    return value
