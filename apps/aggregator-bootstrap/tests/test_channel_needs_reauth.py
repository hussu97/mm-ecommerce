"""The heal loop takes the API's liveness verdict; it does not re-derive one.

`_channel_needs_reauth` used to compute "is this session dead" locally and drifted
from the API that owns the rule: it never learned that Talabat's PerimeterX `_px3`
cookie ROTATES on replay, so its short nominal expiry is advisory. The worker read
that expiry as death on every 2-minute heal poll and re-drove a headed Chrome the
moment the success floor allowed — 41 Talabat re-logins in 16.8h of production on
2026-09-03 against a 15/day baseline, on a session that was fine.

The fix is not a second copy of the policy here. The API publishes
`unusable_reason` on every bundle and this module obeys it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_bootstrap import reauth

_PAST = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
_FUTURE = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()


def _bundle(**over) -> dict:
    base = {"channel": "talabat", "status": "live"}
    base.update(over)
    return base


# ── the API's verdict wins ──────────────────────────────────────────────────────


def test_api_verdict_of_healthy_beats_a_locally_expired_cookie():
    """THE REGRESSION. Talabat, live, cookie expiry long past — but the API says
    healthy because that cookie rotates. The worker must not re-login."""
    b = _bundle(cookie_expires_at=_PAST, unusable_reason=None)
    assert reauth._channel_needs_reauth(b) is None


def test_api_verdict_of_dead_is_honoured_verbatim():
    b = _bundle(unusable_reason="token expired")
    assert reauth._channel_needs_reauth(b) == "token expired"


def test_api_verdict_of_dead_wins_even_when_everything_local_looks_fine():
    b = _bundle(cookie_expires_at=_FUTURE, token_expires_at=_FUTURE)
    b["unusable_reason"] = "needs_bootstrap"
    assert reauth._channel_needs_reauth(b) == "needs_bootstrap"


def test_empty_string_verdict_is_treated_as_healthy():
    """A falsy-but-present reason is not a reason."""
    assert reauth._channel_needs_reauth(_bundle(unusable_reason="")) is None


# ── fallback: an old API mid-deploy does not send the field ─────────────────────


def test_absent_field_falls_back_to_the_local_derivation():
    """Blue/green skew only. Absent key ⇒ derive; present-and-null ⇒ healthy."""
    assert reauth._channel_needs_reauth(_bundle(cookie_expires_at=_PAST)) == (
        "cookie expired"
    )
    assert reauth._channel_needs_reauth(_bundle(status="needs_bootstrap")) == (
        "needs_bootstrap"
    )
    assert reauth._channel_needs_reauth(_bundle(cookie_expires_at=_FUTURE)) is None


def test_fallback_is_the_only_local_derivation_left():
    """Guard the shape of the fix: the API verdict must be consulted FIRST, before
    any local expiry parsing, or the drift quietly comes back."""
    import inspect

    src = inspect.getsource(reauth._channel_needs_reauth)
    body = src.split('"""', 2)[-1]
    assert body.index('"unusable_reason" in bundle') < body.index("token_expires_at")
