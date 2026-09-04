"""Fingerprinted Sentry captures for "expected but broken" states.

Sentry's default `LoggingIntegration` turns `logger.error`/`logger.exception`
into events, but the partial-failure signals in the scheduled aggregator loops
are logged at WARNING — a channel whose nightly run failed, a health line naming
a stale session, a same-night finance retry that gave up — and so never became
events. "Ran but did not complete fully" produced nothing in Sentry.

These helpers raise those explicitly, each with a STABLE fingerprint so a
recurring condition is one grouped, alertable issue rather than per-tick spam.

Rules, matching the worker's `observability.py`:
  * Safe no-op without a DSN (`settings.SENTRY_DSN` empty) — nothing imported at
    call time beyond `sentry_sdk`, which the API already depends on.
  * Never raises: telemetry must not break a request or a scheduler tick.
"""

from __future__ import annotations

import logging

import sentry_sdk

from app.core.config import settings

logger = logging.getLogger("mm.api")


def capture_issue(
    message: str,
    *,
    level: str = "warning",
    fingerprint: list[str],
    tags: dict[str, str] | None = None,
) -> None:
    """Raise a fingerprinted Sentry event for a known partial-failure state."""
    if not settings.SENTRY_DSN:
        return
    try:
        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            scope.fingerprint = fingerprint
            for key, value in (tags or {}).items():
                scope.set_tag(key, value)
            sentry_sdk.capture_message(message, level=level)
    except Exception:  # noqa: BLE001 — never let telemetry break the caller
        logger.debug("sentry capture_issue failed", exc_info=True)


def capture_exc(exc: BaseException, *, tags: dict[str, str] | None = None) -> None:
    """Report an unexpected exception with tags (e.g. a dead background task)."""
    if not settings.SENTRY_DSN:
        return
    try:
        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            for key, value in (tags or {}).items():
                scope.set_tag(key, value)
            sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 — never let telemetry break the caller
        logger.debug("sentry capture_exc failed", exc_info=True)
