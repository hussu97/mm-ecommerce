"""One declarative per-channel policy for the aggregator subsystem.

Consolidates per-channel facts that were duplicated or special-cased across
`ingest.py` and `session_store.py`, so a new channel — or a change to an
existing one's liveness/health rules — is a one-line edit here rather than a hunt
through several files. This module is behaviour-preserving: every value below
reproduces the constant it replaced.

Import-safe by construction: it depends only on `app.models.aggregator` (the
channel-name constants), never on `session_store`/`ingest`, so those import this
without a cycle. It carries policy DATA, not behaviour — the sweep/heal logic
stays in its own modules and reads its dials from here.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from app.models.aggregator import (
    CHANNEL_CAREEM,
    CHANNEL_DELIVEROO,
    CHANNEL_KEETA,
    CHANNEL_NOON,
    CHANNEL_TALABAT,
    LOGIN_EMAIL_OTP,
    LOGIN_EMAIL_PASSWORD,
    LOGIN_EMAIL_PASSWORD_OTP,
    LOGIN_MANUAL,
)

# ── Refresh strategy — HOW a dead/expiring session is renewed ────────────────
# The single fact that decides who can heal a channel, and the seam the unified
# auth work turns on: `SERVER_HTTPX` channels can be refreshed by the API itself
# (and, later, proactively before expiry); the rest can only be re-established by
# the headed worker. Today only Deliveroo is server-refreshable.
REFRESH_SERVER_HTTPX = "server_httpx"  # API mints a fresh token over httpx
REFRESH_HEADED_ONLY = "headed_only"  # only the headed worker can re-login
REFRESH_IN_PAGE_SIGNED = "in_page_signed"  # signed in-page; worker warm re-captures

# ── Token shape — WHERE the load-bearing credential lives ────────────────────
# Each shape has its own liveness rule and its own exclusion list today
# (bearer-in-profile vs JWT-in-cookie vs Akamai-cookie-only vs sessionStorage);
# naming it here is the first step to one liveness evaluator instead of five.
TOKEN_BEARER_HEADER = "bearer_header_profile"  # careem: bearer in header_profile
TOKEN_AKAMAI_COOKIE = "akamai_cookie"  # noon: Akamai bm_sv/_abck IS the gate
TOKEN_JWT_COOKIE = "jwt_cookie"  # talabat: accessToken JWT cookie
TOKEN_SESSION_STORAGE = "session_storage"  # keeta: cookies + sessionStorage, no replay
TOKEN_BEARER_AND_COOKIE = "bearer_and_cookie"  # deliveroo: token cookie + Bearer


@dataclass(frozen=True)
class ChannelPolicy:
    """One declarative per-channel auth + liveness descriptor.

    The single source of truth for the per-channel facts the ingest, the session
    store and (shipped in the worker bundle) the worker all key off. Behaviour
    today reads `cookie_expiry_advisory` and `health_stale_after`; the auth
    descriptor fields (`login_method`, `refresh_strategy`, `token_shape`,
    `anti_bot`) consolidate metadata previously duplicated across
    `models.aggregator.CHANNEL_LOGIN_METHODS`, the provider clients and the
    worker's `channels/login.py`, and are the seam the unified refresh path
    consumes. Carries DATA, not behaviour.
    """

    #: When True the stored COOKIE expiry is advisory, not authoritative — the
    #: channel's load-bearing anti-bot cookie rotates on replay and keeps working
    #: past its short nominal TTL, so treating that TTL as "dead" would proactively
    #: reject a still-usable session. Talabat's PerimeterX `_px3` is the case (its
    #: ~5-min nominal expiry once starved the channel of every intraday sweep). The
    #: TOKEN expiry is always honoured; only the rotating cookie is advisory.
    cookie_expiry_advisory: bool = False

    #: A `live` session with no success/warm within this long is reported stale by
    #: the health log. Comfortably longer than the channel's refresh cadence, so an
    #: ordinary day never trips it.
    health_stale_after: timedelta = timedelta(days=2)

    #: The login flow the worker drives (mirrors, and is drift-guarded against,
    #: `models.aggregator.CHANNEL_LOGIN_METHODS`).
    login_method: str = LOGIN_MANUAL

    #: How this channel's session is renewed — the field that decides who heals it.
    refresh_strategy: str = REFRESH_HEADED_ONLY

    #: Where the load-bearing credential lives (drives the liveness rule).
    token_shape: str = TOKEN_BEARER_AND_COOKIE

    #: The anti-bot edge in front of the portal, for operator context.
    anti_bot: str = ""

    @property
    def server_refreshable(self) -> bool:
        """True when the API can renew the session itself (no headed worker)."""
        return self.refresh_strategy == REFRESH_SERVER_HTTPX


_DEFAULT_POLICY = ChannelPolicy()

#: The single place channels are configured. Keys are the `CHANNEL_*` constants so
#: a typo is an import error, not a silent miss.
POLICIES: dict[str, ChannelPolicy] = {
    CHANNEL_NOON: ChannelPolicy(
        login_method=LOGIN_EMAIL_OTP,
        refresh_strategy=REFRESH_HEADED_ONLY,
        token_shape=TOKEN_AKAMAI_COOKIE,
        anti_bot="Akamai",
    ),
    CHANNEL_TALABAT: ChannelPolicy(
        cookie_expiry_advisory=True,
        login_method=LOGIN_EMAIL_PASSWORD_OTP,
        refresh_strategy=REFRESH_HEADED_ONLY,
        token_shape=TOKEN_JWT_COOKIE,
        anti_bot="PerimeterX",
    ),
    CHANNEL_CAREEM: ChannelPolicy(
        login_method=LOGIN_MANUAL,
        refresh_strategy=REFRESH_HEADED_ONLY,
        token_shape=TOKEN_BEARER_HEADER,
        anti_bot="reCAPTCHA-v3",
    ),
    CHANNEL_DELIVEROO: ChannelPolicy(
        login_method=LOGIN_EMAIL_PASSWORD,
        # The only self-healing channel: the API re-mints its short (<1h) token
        # over httpx (`deliveroo_provider._login`), no headed worker required.
        refresh_strategy=REFRESH_SERVER_HTTPX,
        token_shape=TOKEN_BEARER_AND_COOKIE,
        anti_bot="Cloudflare",
    ),
    # Keeta is push-only, refreshed by the worker every few hours and meant to be
    # near-realtime; nothing marks it needs_bootstrap and the httpx sweep never
    # touches it, so a 7h staleness is its only dead-warm signal — the 2-day default
    # would arrive two days late for a channel that should never be hours cold.
    CHANNEL_KEETA: ChannelPolicy(
        health_stale_after=timedelta(hours=7),
        login_method=LOGIN_EMAIL_PASSWORD,
        refresh_strategy=REFRESH_IN_PAGE_SIGNED,
        token_shape=TOKEN_SESSION_STORAGE,
        anti_bot="captcha + region",
    ),
}


def policy_for(channel: str) -> ChannelPolicy:
    """The policy/descriptor for `channel`, or a safe default for an unknown one."""
    return POLICIES.get(channel, _DEFAULT_POLICY)


def server_refreshable(channel: str) -> bool:
    """True when the API can renew `channel` itself (no headed worker heal)."""
    return policy_for(channel).server_refreshable


def next_backoff(
    attempt: int, *, base: float, cap: float, jitter: bool = True
) -> float:
    """Exponential backoff with full jitter, capped. `attempt` is 0-based.

    One backoff helper for the whole subsystem instead of the several ad-hoc
    schedules that grew up in the providers, the ingest retries and the worker.
    Full jitter (`uniform(0, exp)`) spreads retries so several channels that fail
    at once do not re-hit a marketplace in lockstep. `jitter=False` returns the
    exact capped exponential, for callers that must be deterministic (tests, a
    fixed schedule).
    """
    exp = min(cap, base * (2 ** max(0, attempt)))
    if not jitter:
        return exp
    return random.uniform(0, exp)
