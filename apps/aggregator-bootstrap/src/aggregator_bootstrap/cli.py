"""The worker's command line: login, hydrate, capture-and-push, warm-sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import typer

from . import push
from .accounts import PortalAccount, from_env, pull_account, push_account
from .browser import (
    NeedsHumanLogin,
    NotLoggedInError,
    login_interactive,
    login_with_account,
)
from .channels.probes import CHANNEL_PROBES
from .config import settings
from .hydrate import hydrate_from_api
from .warm import hydrate_then_warm, push_probe, warm_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aggregator-bootstrap")

app = typer.Typer(help="Aggregator session bootstrap/warmer worker.")

_CHANNELS = tuple(CHANNEL_PROBES)


def _run_for(
    channels: list[str], *, hydrate_first: bool, auto_relogin: bool = False
) -> None:
    if hydrate_first:
        try:
            asyncio.run(hydrate_then_warm())
        except Exception:  # noqa: BLE001 — warm each channel anyway
            logger.exception("hydrate failed; warming from local files if any")
    for channel in channels:
        if channel not in CHANNEL_PROBES:
            logger.error(
                "unknown channel %s (known: %s)", channel, ", ".join(_CHANNELS)
            )
            continue
        try:
            asyncio.run(warm_channel(channel))
        except (NeedsHumanLogin, NotLoggedInError) as exc:
            # A warm can't rescue a session the stored state no longer
            # authenticates — that is exactly `needs_bootstrap`. Rather than wait
            # for a human, drive the SAME stored login the `login --auto` command
            # does (email/password, plus the Graph-mailbox OTP for Noon/Talabat)
            # and push the fresh session. Only a login that genuinely can't finish
            # unattended (a captcha/passkey, or no connected mailbox for the OTP)
            # falls through to the operator.
            if auto_relogin and _try_auto_relogin(channel):
                continue
            logger.error("%s needs a headed login: %s", channel, exc)
        except Exception:  # noqa: BLE001 — one channel must not stop the rest
            logger.exception("%s capture failed", channel)


def _try_auto_relogin(channel: str) -> bool:
    """Drive the stored login for a dead session and push the result.

    Returns whether a fresh session was captured. Reuses `login_with_account`, so
    it handles Deliveroo's email/password and Noon/Talabat's email + Graph OTP;
    anything that still needs a human (captcha, passkey, no mailbox) raises
    `NeedsHumanLogin`, which we swallow so the channel stays flagged for a headed
    `login` instead of crashing the cron."""
    try:
        account = _load_account(channel, require_password=(channel != "noon"))
    except typer.BadParameter as exc:
        logger.warning("%s auto re-login skipped — %s", channel, exc)
        return False
    try:
        result = asyncio.run(
            login_with_account(
                channel,
                email=account.email,
                password=account.password,
                mailbox=account.mailbox or None,
            )
        )
    except NeedsHumanLogin as exc:
        logger.warning(
            "%s auto re-login needs a human (%s) — left flagged for a headed login",
            channel,
            exc,
        )
        return False
    except Exception:  # noqa: BLE001 — one channel's re-login must not stop the rest
        logger.exception("%s auto re-login failed", channel)
        return False
    try:
        asyncio.run(push_probe(channel, result))
    except Exception:  # noqa: BLE001 — captured locally; a later warm will push it
        logger.exception("%s auto re-login captured but the API push failed", channel)
        return False
    logger.info("%s auto re-login succeeded — session refreshed", channel)
    return True


@app.command("login")
def login(
    channel: str = typer.Option(..., help="Channel to sign in to"),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Drive stored login: Deliveroo email/password; Noon/Talabat email+Graph OTP.",
    ),
) -> None:
    """Open a headed browser, wait for you to sign in, capture and push.

    Default: you complete OTP/captcha/passkey in the window. `--auto` drives
    Deliveroo (email/password) or Noon/Talabat (email + Graph OTP after mailbox-auth).
    The resulting Playwright state is pushed to the API.
    """
    if channel not in CHANNEL_PROBES:
        raise typer.BadParameter(f"unknown channel {channel}")
    try:
        if auto:
            account = _load_account(channel, require_password=(channel != "noon"))
            result = asyncio.run(
                login_with_account(
                    channel,
                    email=account.email,
                    password=account.password,
                    mailbox=account.mailbox or None,
                )
            )
        else:
            result = asyncio.run(login_interactive(channel))
        try:
            asyncio.run(push_probe(channel, result))
        except Exception:  # noqa: BLE001 — local files are already written
            logger.exception(
                "%s captured locally but the API push failed; the session files "
                "are in STORAGE_STATE_DIR. Retry warm-sessions once the API is "
                "reachable — do not log in again.",
                channel,
            )
    except NeedsHumanLogin as exc:
        logger.error("%s: %s", channel, exc)
        raise typer.Exit(code=1) from exc


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


@app.command("store-account")
def store_account(
    channel: str = typer.Option(..., help="Channel this recipe belongs to"),
    email: str = typer.Option("", help="Portal login email; default env *_EMAIL"),
    password: str = typer.Option(
        "",
        help="Portal login password; default env *_PASSWORD. Never logged.",
    ),
    login_method: str = typer.Option(
        "",
        help="email_password | email_otp | email_password_otp | sso | manual",
    ),
    extra: list[str] | None = typer.Option(
        None,
        "--extra",
        help="Non-secret portal config as key=value (repeatable). e.g. org_id=497912",
    ),
) -> None:
    """Encrypt and store a login recipe on aggregator_account via the API.

    The password is sealed by the API under AGGREGATOR_CONFIG_ENCRYPTION_KEY.
    It never sits in git. Omit --password to keep a previously stored secret
    while updating email or extras.
    """
    if channel not in CHANNEL_PROBES:
        raise typer.BadParameter(f"unknown channel {channel}")
    env = from_env(channel)
    body: dict = {"channel": channel, "account_ref": ""}
    resolved_email = email.strip() or (env.email if env else "")
    resolved_password = password or (env.password if env else "")
    if resolved_email:
        body["email"] = resolved_email
    if resolved_password:
        body["password"] = resolved_password
    if login_method.strip():
        body["login_method"] = login_method.strip()
    extras: dict[str, str] = {}
    for item in extra or []:
        if "=" not in item:
            raise typer.BadParameter(f"--extra must be key=value, got {item!r}")
        key, _, value = item.partition("=")
        extras[key.strip()] = value.strip()
    if extras:
        body["extras"] = extras
    try:
        echoed = asyncio.run(push_account(body))
    except Exception as exc:
        logger.error("store-account failed: %s", exc)
        raise typer.Exit(code=1) from exc
    logger.info(
        "stored %s login_method=%s email=%s has_password=%s extras=%s",
        echoed.get("channel"),
        echoed.get("login_method"),
        echoed.get("email"),
        echoed.get("has_password"),
        echoed.get("extras"),
    )


def _wait_for_auth_code(redirect_uri: str, *, timeout: float = 300.0) -> str:
    """Listen on the Azure redirect URI until the browser comes back with a code.

    Only works when the redirect has an explicit port we can bind (e.g.
    ``http://localhost:8765/callback``). The credit-card scraper's shared
    Microsoft app uses bare ``http://localhost`` — use `_prompt_for_auth_code`
    for that flow instead.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port
    if port is None:
        raise typer.BadParameter(
            f"redirect_uri {redirect_uri!r} has no port to bind. "
            "Paste the ?code=… URL when prompted, or register a "
            "http://localhost:8765/callback redirect on the Azure app."
        )
    holder: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            code = (query.get("code") or [""])[0]
            error = (query.get("error_description") or query.get("error") or [""])[0]
            if code:
                holder["code"] = code
            if error:
                holder["error"] = error
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if holder.get("code"):
                self.wfile.write(b"Microsoft sign-in complete. You can close this tab.")
            else:
                self.wfile.write(b"Sign-in did not return a code. Check the terminal.")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = HTTPServer((host, port), Handler)
    server.timeout = 1.0
    deadline = time.monotonic() + timeout
    while (
        time.monotonic() < deadline and "code" not in holder and "error" not in holder
    ):
        server.handle_request()
    server.server_close()
    if holder.get("code"):
        return holder["code"]
    raise typer.BadParameter(
        holder.get("error") or "timed out waiting for Microsoft sign-in"
    )


def _prompt_for_auth_code() -> str:
    """Same paste-the-redirect flow as credit-card-scraper's OAuth Token Setup.

    Microsoft redirects to ``http://localhost?code=…`` (page may fail to load —
    that is fine). The operator copies the address bar URL (or the raw code)
    and pastes it here.
    """
    from urllib.parse import parse_qs, urlparse

    print(  # noqa: T201
        "\nAfter Microsoft signs you in it redirects to http://localhost — the\n"
        "page may not load. Copy the FULL browser URL (it contains ?code=…)\n"
        "and paste it below (or paste just the code).\n",
        flush=True,
    )
    raw = input("Paste redirect URL or code: ").strip()  # noqa: S322
    if not raw:
        raise typer.BadParameter("empty paste — re-run mailbox-auth")
    if "code=" in raw or raw.startswith("http"):
        query = parse_qs(urlparse(raw).query)
        code = (query.get("code") or [""])[0].strip()
        error = (query.get("error_description") or query.get("error") or [""])[0]
        if error and not code:
            raise typer.BadParameter(error)
        if not code:
            raise typer.BadParameter("no code= in that URL")
        return code
    return raw


@app.command("mailbox-auth")
def mailbox_auth(
    channel: str = typer.Option(..., help="Channel whose Microsoft app to connect"),
) -> None:
    """One-time Microsoft sign-in for this aggregator's Graph mailbox.

    Uses the client id + secret already saved on that channel's login recipe
    (Admin → Logins). Same pattern as credit-card-scraper: open Authorize,
    paste the ``http://localhost?code=…`` redirect URL, store the refresh
    token. Each aggregator has its own Azure app — never a global EMAIL_MS_*.
    """
    import webbrowser
    from urllib.parse import urlparse

    from .graph_mail import GraphApp, GraphMailboxError, exchange_code

    if channel not in CHANNEL_PROBES:
        raise typer.BadParameter(f"unknown channel {channel}")
    account = asyncio.run(pull_account(channel))
    if account is None:
        raise typer.BadParameter(
            f"no stored {channel} login recipe. Save the Microsoft client id "
            f"and secret on Admin → Aggregators → Logins first."
        )
    try:
        app = GraphApp.from_mailbox(account.mailbox)
    except GraphMailboxError as exc:
        raise typer.BadParameter(str(exc)) from exc
    url = app.authorize_url(state=channel)
    logger.info(
        "Opening Microsoft sign-in for %s (tenant=%s, redirect=%s).",
        channel,
        app.tenant,
        app.redirect_uri,
    )
    print(url)  # noqa: T201 — the operator needs the URL
    webbrowser.open(url)
    try:
        # Bare http://localhost (CCS shared app) → paste flow.
        # Explicit :port callback → local listener.
        if urlparse(app.redirect_uri).port is None:
            code = _prompt_for_auth_code()
        else:
            code = _wait_for_auth_code(app.redirect_uri)
        tokens = exchange_code(app, code)
    except GraphMailboxError as exc:
        logger.error("%s mailbox-auth failed: %s", channel, exc)
        raise typer.Exit(code=1) from exc
    refresh = str(tokens.get("refresh_token") or "")
    if not refresh:
        raise typer.Exit(code=1)
    body = {
        "channel": channel,
        "mailbox": {
            "provider": "graph",
            "client_id": app.client_id,
            "tenant": app.tenant,
            "redirect_uri": app.redirect_uri,
            "refresh_token": refresh,
        },
    }
    try:
        asyncio.run(push_account(body))
    except Exception as exc:  # noqa: BLE001
        logger.error("could not store the refresh token: %s", exc)
        raise typer.Exit(code=1) from exc
    logger.info(
        "%s Microsoft mailbox connected (refresh token stored; secret never logged)",
        channel,
    )


@app.command("hydrate")
def hydrate() -> None:
    """Pull encrypted sessions from the API and write local storage_state files.

    Run on every worker start (warm-sessions does this itself). A new container
    with an empty volume resumes the previous session from the database.
    """
    restored = asyncio.run(hydrate_from_api())
    logger.info("hydrated: %s", restored or "(nothing stored yet)")


@app.command("capture-and-push")
def capture_and_push(
    channel: str = typer.Option(None, help="Channel to capture"),
    all_channels: bool = typer.Option(False, "--all", help="All channels"),
) -> None:
    """Capture the session for a channel (or --all) and push it to the API."""
    _run_for(
        list(_CHANNELS) if all_channels else [channel] if channel else [],
        hydrate_first=True,
    )


@app.command("warm-sessions")
def warm_sessions(
    channel: str = typer.Option(None, help="Channel to warm; omit for all"),
    auto_relogin: bool = typer.Option(
        True,
        "--auto-relogin/--no-auto-relogin",
        help="Drive the stored login (incl. Graph OTP) for a dead session, "
        "instead of leaving it for a human.",
    ),
) -> None:
    """Hydrate from the API, refresh anti-bot cookies/tokens, push back.

    This is the VM cron entrypoint. A live session is just warmed; a session the
    API has flagged `needs_bootstrap` (its stored state no longer authenticates)
    is automatically re-logged-in from the stored credentials — the same
    `login --auto` flow, including the Graph-mailbox OTP — so recovery no longer
    waits for a person. Pass `--no-auto-relogin` for the old warm-only behaviour;
    a login that truly needs a human (captcha/passkey, or no connected mailbox)
    still falls through to a headed `login`.
    """
    _run_for(
        [channel] if channel else list(_CHANNELS),
        hydrate_first=True,
        auto_relogin=auto_relogin,
    )


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
# never arrives) must NOT be re-driven on every 2-minute heal tick: each attempt
# opens a headed Chrome and holds the shared warm `flock`, starving the channels
# that could heal. So a failed reauth arms an exponential backoff, persisted to
# the sessions volume (the heal runs as a fresh container each tick, so in-memory
# state would not survive). A success — or the channel simply going healthy on its
# own — clears it, so a transient death still heals on the very next tick.
_REAUTH_BACKOFF_BASE_SECONDS = 5 * 60
_REAUTH_BACKOFF_MAX_SECONDS = 60 * 60


def _reauth_backoff_path(channel: str) -> Path:
    return Path(settings.STORAGE_STATE_DIR) / f"{channel}.reauth_backoff.json"


def _reauth_cooldown_remaining(channel: str) -> float:
    """Seconds until `channel` may be re-driven, or 0 if it is eligible now."""
    try:
        data = json.loads(_reauth_backoff_path(channel).read_text(encoding="utf-8"))
        return max(0.0, float(data.get("next_at", 0)) - time.time())
    except (OSError, ValueError, TypeError):
        return 0.0  # no/ën unreadable state → not in cooldown


def _record_reauth_failure(channel: str) -> None:
    """Arm/extend the backoff after a failed reauth (exponential, capped)."""
    path = _reauth_backoff_path(channel)
    failures = 0
    try:
        failures = int(json.loads(path.read_text(encoding="utf-8")).get("failures", 0))
    except (OSError, ValueError, TypeError):
        failures = 0
    failures += 1
    delay = min(
        _REAUTH_BACKOFF_BASE_SECONDS * (2 ** (failures - 1)),
        _REAUTH_BACKOFF_MAX_SECONDS,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"failures": failures, "next_at": time.time() + delay}),
            encoding="utf-8",
        )
    except OSError:  # pragma: no cover — a write failure just means no backoff
        logger.warning("reauth: could not persist %s backoff", channel)
    logger.warning(
        "reauth: %s failed %d time(s) — backing off %d min before the next attempt",
        channel,
        failures,
        int(delay // 60),
    )


def _clear_reauth_backoff(channel: str) -> None:
    """Reset the backoff — the channel is healthy again."""
    try:
        _reauth_backoff_path(channel).unlink()
    except FileNotFoundError:
        pass
    except OSError:  # pragma: no cover — best effort
        pass


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
        logger.warning("reauth: %s is %s — re-logging in", ch, reason)
        if _try_auto_relogin(ch):
            _clear_reauth_backoff(ch)
            healed += 1
        else:
            _record_reauth_failure(ch)
    return healed


@app.command("heal-sessions")
def heal_sessions(
    channel: str = typer.Option(None, help="Channel to check; omit for all"),
) -> None:
    """Re-login ONLY the sessions the API reports dead/expired, once, and exit.

    No headed Chrome for a healthy session — so this is cheap enough to run
    often, and is the single-shot form of the `serve-reauth` daemon."""
    only = {channel} if channel else None
    if _heal_once(only) == 0:
        logger.info("heal-sessions: nothing to heal")


@app.command("serve-reauth")
def serve_reauth(
    interval: float = typer.Option(
        10.0, help="Seconds between health checks when idle."
    ),
) -> None:
    """Trigger-based re-auth daemon: watch session health and re-login any channel
    that goes dead, so a session that dies mid-day self-heals in ~one interval
    plus the login time — the API request path only has to mark a session
    `needs_bootstrap` and wait, and this brings it back.

    Long-running but light: it holds no browser open, spawning headed Chrome only
    for the seconds it takes to re-login a channel that actually needs it. Run as
    the worker's persistent service (compose `serve-reauth`) alongside the daily
    warm cron.
    """
    logger.info("reauth daemon started (interval=%ss)", interval)
    while True:
        try:
            _heal_once()
        except Exception:  # noqa: BLE001 — the daemon must outlive any one failure
            logger.exception("reauth daemon: heal pass failed")
        time.sleep(max(interval, 2.0))


@app.command("bootstrap")
def bootstrap(
    channel: str = typer.Option(None, help="Channel to bootstrap"),
    all_channels: bool = typer.Option(False, "--all", help="All channels"),
) -> None:
    """Hydrate + warm. If a session is dead, tell the operator to run `login`.

    Does not poll IMAP for OTPs. A dead session is a headed login, once.
    """
    _run_for(
        list(_CHANNELS) if all_channels else [channel] if channel else [],
        hydrate_first=True,
    )


if __name__ == "__main__":
    app()
