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
)


@dataclass(frozen=True)
class ChannelPolicy:
    """Per-channel liveness/health rules. See the module docstring."""

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


_DEFAULT_POLICY = ChannelPolicy()

#: The single place channels are configured. Keys are the `CHANNEL_*` constants so
#: a typo is an import error, not a silent miss.
POLICIES: dict[str, ChannelPolicy] = {
    CHANNEL_NOON: ChannelPolicy(),
    CHANNEL_TALABAT: ChannelPolicy(cookie_expiry_advisory=True),
    CHANNEL_CAREEM: ChannelPolicy(),
    CHANNEL_DELIVEROO: ChannelPolicy(),
    # Keeta is push-only, refreshed by the worker every few hours and meant to be
    # near-realtime; nothing marks it needs_bootstrap and the httpx sweep never
    # touches it, so a 7h staleness is its only dead-warm signal — the 2-day default
    # would arrive two days late for a channel that should never be hours cold.
    CHANNEL_KEETA: ChannelPolicy(health_stale_after=timedelta(hours=7)),
}


def policy_for(channel: str) -> ChannelPolicy:
    """The policy for `channel`, or a safe default for an unknown one."""
    return POLICIES.get(channel, _DEFAULT_POLICY)


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
