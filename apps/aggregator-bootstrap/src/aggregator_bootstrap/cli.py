"""The worker's command line: login, hydrate, capture-and-push, warm-sessions."""

from __future__ import annotations

import asyncio
import logging
import time

import typer

from .accounts import PortalAccount, from_env, pull_account, push_account
from .browser import (
    NeedsHumanLogin,
    NotLoggedInError,
    login_interactive,
    login_with_account,
)
from .channels.probes import CHANNEL_PROBES
from .hydrate import hydrate_from_api
from .warm import hydrate_then_warm, push_probe, warm_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aggregator-bootstrap")

app = typer.Typer(help="Aggregator session bootstrap/warmer worker.")

_CHANNELS = tuple(CHANNEL_PROBES)


def _run_for(channels: list[str], *, hydrate_first: bool) -> None:
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
        except NeedsHumanLogin as exc:
            logger.error("%s needs a headed login: %s", channel, exc)
        except NotLoggedInError as exc:
            logger.error("%s needs a fresh login: %s", channel, exc)
        except Exception:  # noqa: BLE001 — one channel must not stop the rest
            logger.exception("%s capture failed", channel)


@app.command("login")
def login(
    channel: str = typer.Option(..., help="Channel to sign in to"),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Drive stored login: Deliveroo email/password, Noon email+Graph OTP.",
    ),
) -> None:
    """Open a headed browser, wait for you to sign in, capture and push.

    Default: you complete OTP/captcha/passkey in the window. `--auto` drives
    Deliveroo (email/password) or Noon (email + Graph OTP after mailbox-auth).
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


def _load_account(
    channel: str, *, require_password: bool = True
) -> PortalAccount:
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
    while time.monotonic() < deadline and "code" not in holder and "error" not in holder:
        server.handle_request()
    server.server_close()
    if holder.get("code"):
        return holder["code"]
    raise typer.BadParameter(holder.get("error") or "timed out waiting for Microsoft sign-in")


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
) -> None:
    """Hydrate from the API, refresh anti-bot cookies/tokens, push back.

    This is the VM cron entrypoint. It never drives an OTP login.
    """
    _run_for([channel] if channel else list(_CHANNELS), hydrate_first=True)


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
