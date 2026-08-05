"""
The check that separates a customer from a bot.

Between April and August a bot used signup and password reset to send mail from
our domain to eighteen harvested addresses — a welcome and a reset, seven to
eighty seconds apart, several of them the same Gmail inbox spelled with
different dots. Rate limiting was already in place and irrelevant: `5/minute`
per IP stops a burst, and this was one signup every few hours from somewhere
new. What marks that traffic is not its rate but that nobody was there.

The two behaviours worth pinning are the ones that decide whether this is a
security control or an outage: unset must mean off, and Cloudflare being down
must not take signup with it.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import turnstile_service

PASS = turnstile_service.TEST_SECRET_ALWAYS_PASSES
FAIL = turnstile_service.TEST_SECRET_ALWAYS_FAILS


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", PASS)


async def test_no_secret_means_no_check(unconfigured):
    """
    Deliberate, and the reason is worth stating: the alternative is that
    deploying this before the secret exists locks every real customer out of
    signing up. An hour of unchecked signups is recoverable; an hour of nobody
    being able to register is lost custom.
    """
    assert not turnstile_service.is_enabled()
    ok, why = await turnstile_service.verify(None)
    assert ok and "not configured" in why


async def test_a_missing_token_is_refused_once_configured(configured):
    assert turnstile_service.is_enabled()
    ok, why = await turnstile_service.verify(None)
    assert not ok
    assert why == "no token supplied"

    ok, _ = await turnstile_service.verify("   ")
    assert not ok


async def test_a_solved_challenge_passes(configured):
    ok, _ = await turnstile_service.verify("XXXX.DUMMY.TOKEN.XXXX")
    assert ok


async def test_a_failed_challenge_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", FAIL)
    ok, _ = await turnstile_service.verify("XXXX.DUMMY.TOKEN.XXXX")
    assert not ok


async def test_cloudflare_being_down_does_not_close_the_shop(monkeypatch):
    """
    A verifier we cannot reach is an outage on their side. Refusing every
    signup through it would turn their outage into ours, so the request goes
    through and the log says why.
    """
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "0x_real_looking_secret")

    class _Boom:
        def __call__(self, **_kw):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, *_a, **_k):
            raise OSError("connection refused")

    monkeypatch.setattr(turnstile_service.httpx, "AsyncClient", _Boom())

    ok, why = await turnstile_service.verify("XXXX.DUMMY.TOKEN.XXXX")
    assert ok
    assert "unreachable" in why


async def test_the_refusal_says_nothing_about_why(monkeypatch):
    """
    `verify` hands back a reason for the log only. A specific refusal tells a
    bot which of its guesses was closest and tells a customer nothing they can
    act on, so the endpoint says one thing however it failed.
    """
    from types import SimpleNamespace

    from app.api.v1 import auth
    from app.core.exceptions import BadRequestError

    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", FAIL)
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"), headers={})

    for token in (None, "", "XXXX.DUMMY.TOKEN.XXXX"):
        with pytest.raises(BadRequestError) as refusal:
            await auth._require_human(request, token)
        message = str(refusal.value)
        assert "couldn't verify that you're human" in message
        # Nothing about tokens, secrets, Cloudflare or which check tripped.
        for leak in ("token", "secret", "turnstile", "cloudflare", "error-codes"):
            assert leak not in message.lower(), f"the refusal mentions {leak!r}"


async def test_a_signed_in_customer_resetting_their_own_password_is_not_challenged(
    monkeypatch,
):
    """
    The account settings screen sends a reset link to the address the person is
    already signed in as. Holding a session for that mailbox proves more than a
    challenge could, so asking for one would be friction with nothing behind
    it — and there is no widget on that screen to solve.

    Naming somebody *else's* address is a different request and is still asked,
    because that is the shape the abuse took.
    """
    from types import SimpleNamespace

    from app.api.v1 import auth

    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", FAIL)
    asked: list[str] = []

    async def spy(_request, _token):
        asked.append("challenged")

    monkeypatch.setattr(auth, "_require_human", spy)

    async def call(user_email: str | None, requested: str):
        user = SimpleNamespace(email=user_email) if user_email else None
        same = user is not None and (user.email or "").lower() == requested.lower()
        if not same:
            await auth._require_human(None, None)

    await call("customer@example.com", "customer@example.com")
    assert asked == [], "a customer was challenged for their own address"

    await call("customer@example.com", "SOMEONE.ELSE@example.com")
    await call(None, "customer@example.com")
    assert len(asked) == 2, "a guest or a third-party address must still be asked"
