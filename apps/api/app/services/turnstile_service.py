"""
Cloudflare Turnstile: proof that a form was filled in by a person.

Signup and password reset are the two endpoints that make us send mail to an
address somebody else chose, which is what makes them worth abusing. Between
April and August a bot used them to send a welcome and a reset — seven to eighty
seconds apart — to eighteen harvested addresses, several of them the same Gmail
inbox spelled with different dots. Every one of those accounts placed no orders.
The damage is not to us directly: it is real people receiving unsolicited mail
from our domain, and a sending reputation that eventually gets throttled.

Rate limiting already existed and did nothing, because it is the wrong shape of
defence. `5/minute` per IP stops a burst; this was one signup every few hours
from somewhere new each time. What separates that traffic from a customer is not
its rate, it is that nobody was sitting there.

**Unset means off.** With no secret configured, `verify` returns success and
says so in the log, exactly as the courier integrations do. That is deliberate:
the alternative is that deploying this before the secret exists locks every real
customer out of signing up, which is a worse outage than the abuse it prevents.
The trade is only safe because it is loud — see `is_enabled` and the warning it
drives at startup.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["VERIFY_URL", "is_enabled", "verify"]

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

#: Cloudflare's own always-passes secret. Published in their docs for exactly
#: this purpose, and recognised here so a developer machine or a CI run can
#: exercise the wired-up path without reaching the network at all.
TEST_SECRET_ALWAYS_PASSES = "1x0000000000000000000000000000000AA"
TEST_SECRET_ALWAYS_FAILS = "2x0000000000000000000000000000000AA"


def is_enabled() -> bool:
    """Whether a secret is configured. Without one, nothing is checked."""
    return bool((settings.TURNSTILE_SECRET_KEY or "").strip())


async def verify(
    token: str | None, *, remote_ip: str | None = None
) -> tuple[bool, str]:
    """
    Whether this token is a genuine, unused Turnstile solution.

    Returns `(ok, reason)`. The reason is for the log, never for the response —
    telling a caller *why* their token was rejected is telling a bot which of
    its guesses was closest.

    Never raises. Cloudflare being unreachable must not take signup down with
    it, so a transport failure is treated as a pass and recorded loudly: an hour
    of unchecked signups is recoverable, an hour of no signups at all is lost
    custom.
    """
    if not is_enabled():
        return True, "turnstile not configured"

    if not token or not token.strip():
        return False, "no token supplied"

    secret = settings.TURNSTILE_SECRET_KEY.strip()

    # Cloudflare's test secrets are answered here rather than over the wire, so
    # a test suite neither depends on the network nor learns to mock it.
    if secret == TEST_SECRET_ALWAYS_PASSES:
        return True, "test secret"
    if secret == TEST_SECRET_ALWAYS_FAILS:
        return False, "test secret"

    payload = {"secret": secret, "response": token.strip()}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(
            timeout=settings.TURNSTILE_TIMEOUT_SECONDS
        ) as client:
            result = (await client.post(VERIFY_URL, data=payload)).json()
    except Exception as exc:  # pragma: no cover — network
        logger.warning("Turnstile unreachable, letting the request through: %s", exc)
        return True, "verifier unreachable"

    if result.get("success"):
        return True, "ok"

    # `error-codes` names things like `timeout-or-duplicate`, which is what a
    # replayed token looks like — worth having in the log when someone asks why
    # a real customer was refused.
    codes = ", ".join(result.get("error-codes") or []) or "no reason given"
    return False, codes
