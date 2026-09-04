"""Sentry for the worker (the browser half) — init + typed capture helpers.

Until now the daemon never called `sentry_sdk.init`, so every failure in the
scraper half reached only Cloud Logging with nothing paging anyone: a login that
needs a human, a reauth budget exhausted, a job that captured zero pages. This
module closes that. It is called once per process — the Typer callback in
`cli.py` runs it before any command, so `serve` (the daemon) and every one-shot
(`login`, `warm-sessions`, `heal-sessions`, …) are covered.

Design rules:
  * Import-safe and cheap when off: `sentry_sdk` is imported lazily inside the
    functions, so with no `SENTRY_DSN` (local/dev) nothing is imported and the
    capture helpers are pure no-ops.
  * Never raises: telemetry must not break a scraper. Every path is guarded.
  * Stable fingerprints: each capture groups by `(channel, kind)` so a channel
    that quietly stops producing pages is ONE ongoing issue, not per-tick spam —
    and so a Sentry alert rule can target a specific fingerprint (needs_human is
    the one to page on immediately).
"""

from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger("aggregator-bootstrap")

_initialised = False


def init_sentry() -> None:
    """Initialise Sentry once. No-op without a DSN, or if already initialised."""
    global _initialised
    if _initialised:
        return
    _initialised = True
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            integrations=[
                AsyncioIntegration(),
                # ERROR logs become events automatically; the explicit captures
                # below add fingerprints/tags for the sub-ERROR "expected but
                # broken" cases (a warning-level zero-capture) the log level
                # would otherwise drop.
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            # This worker serves no requests; traces would only be self-noise.
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
        sentry_sdk.set_tag("service", "aggregator-worker")
        logger.info("sentry initialised (service=aggregator-worker)")
    except Exception:  # noqa: BLE001 — a telemetry init failure must not stop the worker
        logger.warning("sentry init failed; continuing without it", exc_info=True)


def _capture_message(
    message: str,
    *,
    level: str,
    fingerprint: list[str],
    tags: dict[str, str],
) -> None:
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk

        # `new_scope` (sentry-sdk 2.x) / `push_scope` (1.x) — support both.
        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            scope.fingerprint = fingerprint
            for key, value in tags.items():
                scope.set_tag(key, value)
            sentry_sdk.capture_message(message, level=level)
    except Exception:  # noqa: BLE001 — never let telemetry break a scraper
        logger.debug("sentry capture_message failed", exc_info=True)


def capture_exception(
    exc: BaseException, *, tags: dict[str, str] | None = None
) -> None:
    """Report an unexpected exception (e.g. a job's catch-all)."""
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk

        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            for key, value in (tags or {}).items():
                scope.set_tag(key, value)
            sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 — never let telemetry break a scraper
        logger.debug("sentry capture_exception failed", exc_info=True)


# ── Typed captures for the known failure classes ────────────────────────────
# One helper per class so call-sites stay one line and fingerprints stay stable.


def note_needs_human(channel: str, *, next_attempt_seconds: int | None = None) -> None:
    """A channel automated re-login cannot recover — a human must act. Page on this."""
    _capture_message(
        f"aggregator {channel}: needs a human login (captcha/passkey/mailbox)",
        level="error",
        fingerprint=["aggregator", "needs_human", channel],
        tags={"channel": channel, "aggregator_issue": "needs_human"},
    )


def note_reauth_failure(channel: str, *, transient: bool, failures: int) -> None:
    """A reauth attempt failed and armed a backoff (transient vs human-needed)."""
    _capture_message(
        f"aggregator {channel}: reauth failed x{failures} "
        f"({'transient' if transient else 'needs-human'})",
        level="warning",
        fingerprint=["aggregator", "reauth_failed", channel],
        tags={
            "channel": channel,
            "aggregator_issue": "reauth_failed",
            "transient": str(transient).lower(),
        },
    )


def note_empty_capture(channel: str, kind: str, detail: str = "") -> None:
    """A job ran but produced nothing — "expected" yet the logic is not working."""
    msg = f"aggregator {channel}: {kind} captured nothing"
    if detail:
        msg = f"{msg} ({detail})"
    _capture_message(
        msg,
        level="warning",
        fingerprint=["aggregator", "empty_capture", channel, kind],
        tags={"channel": channel, "aggregator_issue": "empty_capture", "kind": kind},
    )


def note_job_timeout(kind: str, channel: str | None, budget_seconds: int) -> None:
    """A job exceeded its hard budget and its Chrome was SIGKILLed (a wedge)."""
    _capture_message(
        f"aggregator job {kind}"
        f"{('/' + channel) if channel else ''} timed out after {budget_seconds}s",
        level="error",
        fingerprint=["aggregator", "job_timeout", kind, channel or "-"],
        tags={
            "channel": channel or "-",
            "aggregator_issue": "job_timeout",
            "kind": kind,
        },
    )
