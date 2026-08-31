"""The per-channel policy is the one place channel facts live (Phase 5).

Golden tests: the resolved policy reproduces the constants it replaced exactly
(so the consolidation is behaviour-preserving), it covers every real channel, and
`next_backoff` stays within its documented bounds.
"""

from __future__ import annotations

from datetime import timedelta

from app.models.aggregator import (
    CHANNEL_CAREEM,
    CHANNEL_DELIVEROO,
    CHANNEL_KEETA,
    CHANNEL_NOON,
    CHANNEL_TALABAT,
)
from app.services.aggregators import policy

_ALL = {
    CHANNEL_NOON,
    CHANNEL_TALABAT,
    CHANNEL_CAREEM,
    CHANNEL_DELIVEROO,
    CHANNEL_KEETA,
}


def test_policies_cover_exactly_the_real_channels():
    assert set(policy.POLICIES) == _ALL


def test_cookie_expiry_advisory_only_talabat():
    """Talabat's rotating PerimeterX cookie is advisory; every other channel's
    cookie expiry is authoritative — the exact behaviour of the retired
    `_COOKIE_EXPIRY_ADVISORY_CHANNELS = {talabat}`."""
    for ch in _ALL:
        expected = ch == CHANNEL_TALABAT
        assert policy.policy_for(ch).cookie_expiry_advisory is expected, ch


def test_health_stale_after_matches_the_retired_constants():
    """Keeta 7h (push-only, near-realtime), everything else the 2-day default."""
    assert policy.policy_for(CHANNEL_KEETA).health_stale_after == timedelta(hours=7)
    for ch in _ALL - {CHANNEL_KEETA}:
        assert policy.policy_for(ch).health_stale_after == timedelta(days=2), ch


def test_policy_for_unknown_channel_is_a_safe_default():
    p = policy.policy_for("not-a-channel")
    assert p.cookie_expiry_advisory is False
    assert p.health_stale_after == timedelta(days=2)


def test_next_backoff_without_jitter_is_capped_exponential():
    # base 5, doubling, capped at 60: 5,10,20,40,60,60,...
    got = [policy.next_backoff(i, base=5, cap=60, jitter=False) for i in range(6)]
    assert got == [5, 10, 20, 40, 60, 60]


def test_next_backoff_with_jitter_stays_within_zero_and_the_cap():
    for i in range(8):
        for _ in range(50):
            v = policy.next_backoff(i, base=5, cap=60)
            assert 0.0 <= v <= 60.0
