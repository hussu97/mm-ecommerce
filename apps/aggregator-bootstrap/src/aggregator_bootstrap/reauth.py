"""Session re-auth: decide what is dead, drive the stored login, back off.

These helpers were extracted verbatim from `cli.py` so both the one-shot
`heal-sessions` command and the always-on `serve` daemon (`daemon.py`) can share
them. Nothing here opens a browser directly — `_try_auto_relogin` reuses
`browser.login_with_account` and `warm.push_probe`; the rest is the pure
"is this session dead / when may we retry it" logic that keeps a headed Chrome
from being re-driven on every tick. `cli.py` re-imports the names it exposes as
commands so its interface is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import typer

from . import push
from .accounts import PortalAccount, from_env, pull_account
from .browser import ChromeLaunchError, NeedsHumanLogin, login_with_account
from .config import settings
from .warm import push_probe

logger = logging.getLogger("aggregator-bootstrap")


class ReloginOutcome(Enum):
    """Why an auto re-login ended — so the heal loop can back off correctly.

    The distinction is the whole point: a `NEEDS_HUMAN` failure (a captcha/passkey
    wall, or no stored account/mailbox) will keep failing until a person acts, so it
    earns the long exponential backoff that stops the 2-minute cron re-driving a
    headed Chrome forever. A `TRANSIENT` failure (a network blip, a marketplace 5xx,
    a tick that lost the shared flock) will succeed on a retry — parking it behind
    the same hour-long backoff is what left careem/deliveroo dead for an hour when a
    manual `login --auto` fixed them instantly. It gets a short backoff instead.
    """

    OK = "ok"
    NEEDS_HUMAN = "needs_human"
    TRANSIENT = "transient"


def _load_account(channel: str, *, require_password: bool = True) -> PortalAccount:
    try:
        account = asyncio.run(pull_account(channel))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not pull account from API (%s); trying env", exc)
        account = from_env(channel)
    if account is None or not account.email:
        raise typer.BadParameter(
            f"no stored {channel} email. "
            f"Save it on Admin → Aggregators → Logins "
            f"(or: aggregator-bootstrap store-account --channel {channel})"
        )
    if require_password and not account.password:
        raise typer.BadParameter(
            f"no stored {channel} password. "
            f"Run: aggregator-bootstrap store-account --channel {channel}"
        )
    logger.info(
        "%s: using stored account %s method=%s",
        channel,
        account.email,
        account.login_method or "?",
    )
    return account


def _try_auto_relogin(channel: str) -> ReloginOutcome:
    """Drive the stored login for a dead session and push the result.

    Reuses `login_with_account`, so it handles Deliveroo's email/password and
    Noon/Talabat's email + Graph OTP. Returns a `ReloginOutcome`: `NEEDS_HUMAN` when
    the login genuinely needs a person (captcha/passkey/no mailbox, or no stored
    credentials), `TRANSIENT` for anything that a retry would clear, `OK` on success.
    Never raises — a channel's re-login must not crash the cron."""
    try:
        account = _load_account(channel, require_password=(channel != "noon"))
    except typer.BadParameter as exc:
        # No stored email/password/mailbox — a person has to save it. Human-needed,
        # not transient: retrying every tick would never fix it.
        logger.warning("%s auto re-login skipped — %s", channel, exc)
        return ReloginOutcome.NEEDS_HUMAN
    try:
        result = asyncio.run(
            login_with_account(
                channel,
                email=account.email,
                password=account.password,
                mailbox=account.mailbox or None,
            )
        )
    except ChromeLaunchError as exc:
        # The browser could not be launched (dead/mid-restart display, no debug
        # port) — infra, not a human wall. Short transient backoff so the next
        # tick retries once the Xvfb supervisor has the display back, instead of
        # flagging needs-human for an hour (the 2026-08-31 outage).
        logger.warning(
            "%s auto re-login hit a browser-launch failure (%s) — transient, "
            "will retry shortly",
            channel,
            exc,
        )
        return ReloginOutcome.TRANSIENT
    except NeedsHumanLogin as exc:
        logger.warning(
            "%s auto re-login needs a human (%s) — left flagged for a headed login",
            channel,
            exc,
        )
        return ReloginOutcome.NEEDS_HUMAN
    except Exception:  # noqa: BLE001 — one channel's re-login must not stop the rest
        logger.exception(
            "%s auto re-login failed (transient) — will retry soon", channel
        )
        return ReloginOutcome.TRANSIENT
    try:
        asyncio.run(push_probe(channel, result))
    except Exception:  # noqa: BLE001 — captured locally; a later warm will push it
        logger.exception("%s auto re-login captured but the API push failed", channel)
        return ReloginOutcome.TRANSIENT
    logger.info("%s auto re-login succeeded — session refreshed", channel)
    return ReloginOutcome.OK


def _channel_needs_reauth(bundle: dict) -> str | None:
    """Why a channel's session needs a headed re-login, or None if it is fine.

    Dead outright (`status != live`), or live but past a stored token/cookie
    expiry — the same proactive check the API's `session_store.is_session_usable`
    makes, so the daemon and the ingest agree on what "dead" means.
    """
    status = bundle.get("status")
    if status != "live":
        return status or "missing"
    now = datetime.now(timezone.utc)
    for label in ("token_expires_at", "cookie_expires_at"):
        raw = bundle.get(label)
        if not raw:
            continue
        try:
            exp = (
                raw
                if isinstance(raw, datetime)
                else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            )
        except ValueError:
            continue
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return f"{label.split('_')[0]} expired"
    return None


# ── reauth backoff ──────────────────────────────────────────────────────────
# A channel that stays dead (a human-only login: PerimeterX wall, a 2FA OTP that
# never arrives) must NOT be re-driven on every heal tick: each attempt opens a
# headed Chrome and (before Phase 3) held the shared warm `flock`, starving the
# channels that could heal. So a failed reauth arms an exponential backoff,
# persisted to the sessions volume (a one-shot heal runs as a fresh container each
# tick, so in-memory state would not survive; the daemon keeps it on disk for the
# same reason — a restart must not forget a human-only channel's backoff). A
# success — or the channel simply going healthy on its own — clears it, so a
# transient death still heals on the very next tick. Human-needed failures
# (captcha/passkey/no mailbox) back off long — a person has to act, so re-driving
# a headed Chrome every couple of minutes only wastes the single browser slot.
_REAUTH_BACKOFF_BASE_SECONDS = 5 * 60
_REAUTH_BACKOFF_MAX_SECONDS = 60 * 60
# Transient failures (network blip, marketplace 5xx, a lost browser slot) clear on
# a retry, so they get a SHORT backoff — enough not to hammer a genuinely-down
# marketplace, but nowhere near the hour that left a healable channel dead.
_REAUTH_TRANSIENT_BASE_SECONDS = 30
_REAUTH_TRANSIENT_MAX_SECONDS = 5 * 60


def _reauth_backoff_path(channel: str) -> Path:
    return Path(settings.STORAGE_STATE_DIR) / f"{channel}.reauth_backoff.json"


def _reauth_cooldown_remaining(channel: str) -> float:
    """Seconds until `channel` may be re-driven, or 0 if it is eligible now."""
    try:
        data = json.loads(_reauth_backoff_path(channel).read_text(encoding="utf-8"))
        return max(0.0, float(data.get("next_at", 0)) - time.time())
    except (OSError, ValueError, TypeError):
        return 0.0  # no/unreadable state → not in cooldown


def _record_reauth_failure(channel: str, *, transient: bool = False) -> None:
    """Arm/extend the backoff after a failed reauth (exponential, capped).

    `transient=True` uses the short profile: a retry will likely succeed, so a
    network/slot blip must not inherit the human-only channel's hour-long backoff.
    """
    base = _REAUTH_TRANSIENT_BASE_SECONDS if transient else _REAUTH_BACKOFF_BASE_SECONDS
    cap = _REAUTH_TRANSIENT_MAX_SECONDS if transient else _REAUTH_BACKOFF_MAX_SECONDS
    path = _reauth_backoff_path(channel)
    failures = 0
    try:
        failures = int(json.loads(path.read_text(encoding="utf-8")).get("failures", 0))
    except (OSError, ValueError, TypeError):
        failures = 0
    failures += 1
    delay = min(base * (2 ** (failures - 1)), cap)
    next_at = time.time() + delay
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"failures": failures, "next_at": next_at}),
            encoding="utf-8",
        )
    except OSError:  # pragma: no cover — a write failure just means no backoff
        logger.warning("reauth: could not persist %s backoff", channel)
    logger.warning(
        "reauth: %s failed %d time(s) (%s) — backing off %ds before the next attempt",
        channel,
        failures,
        "transient" if transient else "needs-human",
        int(delay),
    )
    if not transient:
        # The irreducible zero-touch gap: automated re-login cannot clear a
        # captcha/passkey/press-and-hold wall or get an OTP when the mailbox is
        # down, so a human must act. Emit a DISTINCT, greppable ERROR so the VM's
        # log-based alerting can page specifically on this (there is no ops email
        # sink — structured logs are the alert channel). The backoff above bounds
        # how often a stuck channel re-attempts, so this doubles as a debounced
        # alert rather than per-tick spam.
        logger.error(
            "AGGREGATOR_NEEDS_HUMAN channel=%s: automated re-login cannot recover "
            "this session (captcha/passkey/mailbox); a human must run "
            "`login --channel %s` on the VM. Next auto-attempt in %ds.",
            channel,
            channel,
            int(delay),
        )
    # Publish the next-attempt time to the API so the ingest's reauth wait can bail
    # out early instead of burning the full AGGREGATOR_REAUTH_WAIT_SECONDS on a
    # login this daemon will not re-drive until `next_at`. Best-effort: the backoff
    # file above is the source of truth for the daemon; this is only an optimisation
    # for the API, so a reporting blip must never disturb the heal loop.
    try:
        asyncio.run(push.report_reauth_backoff(channel, next_at))
    except Exception:  # noqa: BLE001 — best-effort telemetry to the API
        logger.warning("reauth: could not publish %s backoff to the API", channel)


# ── success cooldown (guardrail: a healthy-but-short-lived session must not be
#    re-driven on every tick either) ─────────────────────────────────────────────
# The backoff above paces FAILURES. It does nothing for a channel that re-logs in
# *successfully* and then dies again minutes later — Talabat's PerimeterX `_px3`
# rotates and expires hourly, so every heal poll that finds it dead would spawn a
# fresh headed Chrome, and each headed login pegs both of this e2-small's vCPUs
# (15 talabat re-logins in one observed day). A success stamps the channel here; it
# is not re-driven until `WORKER_MIN_RELOGIN_INTERVAL_SECONDS` passes. Stamped ONLY
# on success (not on failure), so a transient blip's short backoff still retries
# promptly — this floor targets the wasteful *repeat of healthy work*, not recovery.
def _relogin_stamp_path(channel: str) -> Path:
    return Path(settings.STORAGE_STATE_DIR) / f"{channel}.last_relogin.json"


def _record_relogin_success(channel: str) -> None:
    """Stamp the time of a successful re-login, on the persisted sessions volume so
    a daemon restart (or a one-shot heal's fresh container) does not forget it."""
    path = _relogin_stamp_path(channel)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": time.time()}), encoding="utf-8")
    except OSError:  # pragma: no cover — a write failure just means no cooldown
        logger.warning("reauth: could not persist %s relogin stamp", channel)


def _success_cooldown_remaining(channel: str) -> float:
    """Seconds until `channel` may be re-driven after its last SUCCESSFUL re-login,
    or 0 if eligible now — 0 also when the floor is disabled (<=0) or no success is
    on file, so a channel that has never healed is never blocked from its first try."""
    interval = settings.WORKER_MIN_RELOGIN_INTERVAL_SECONDS
    if interval <= 0:
        return 0.0
    try:
        data = json.loads(_relogin_stamp_path(channel).read_text(encoding="utf-8"))
        return max(0.0, float(data.get("at", 0)) + interval - time.time())
    except (OSError, ValueError, TypeError):
        return 0.0  # no/unreadable stamp → not in cooldown


def _clear_reauth_backoff(channel: str) -> None:
    """Reset the backoff — the channel is healthy again."""
    existed = False
    try:
        _reauth_backoff_path(channel).unlink()
        existed = True
    except FileNotFoundError:
        pass
    except OSError:  # pragma: no cover — best effort
        pass
    # Only tell the API when we actually cleared a standing backoff — otherwise the
    # healthy-channel tick (every heal poll for every live channel) would spam
    # clears. A successful relogin already clears it API-side via the fresh session
    # push; this covers a channel that went healthy on its own after a backoff was
    # set.
    if existed:
        try:
            asyncio.run(push.report_reauth_backoff(channel, None))
        except Exception:  # noqa: BLE001 — best-effort telemetry to the API
            logger.warning("reauth: could not clear %s backoff on the API", channel)


def _heal_once(only: set[str] | None = None) -> int:
    """Re-login every channel the API reports dead/expired. Returns how many were
    healed. Cheap when all are healthy: one API call, and headed Chrome only for
    a channel that actually needs it — and only one that is out of its backoff."""
    try:
        bundles = asyncio.run(push.pull_sessions())
    except Exception:  # noqa: BLE001 — a transient API blip must not kill the loop
        logger.exception("reauth: could not read session health from the API")
        return 0
    healed = 0
    for bundle in bundles:
        ch = bundle.get("channel")
        if not ch or (only is not None and ch not in only):
            continue
        reason = _channel_needs_reauth(bundle)
        if reason is None:
            _clear_reauth_backoff(ch)  # healthy → forget any past failures
            continue
        remaining = _reauth_cooldown_remaining(ch)
        if remaining > 0:
            logger.info(
                "reauth: %s is %s but in backoff — skipping for ~%d min",
                ch,
                reason,
                int(remaining // 60) + 1,
            )
            continue
        cooldown = _success_cooldown_remaining(ch)
        if cooldown > 0:
            logger.info(
                "reauth: %s is %s but was refreshed recently — skipping for ~%d min",
                ch,
                reason,
                int(cooldown // 60) + 1,
            )
            continue
        logger.warning("reauth: %s is %s — re-logging in", ch, reason)
        outcome = _try_auto_relogin(ch)
        if outcome is ReloginOutcome.OK:
            _record_relogin_success(ch)
            _clear_reauth_backoff(ch)
            healed += 1
        else:
            # A transient failure retries on the next tick (short backoff); only a
            # genuine human-needed wall parks the channel for the long backoff.
            _record_reauth_failure(ch, transient=(outcome is ReloginOutcome.TRANSIENT))
    return healed
