"""The worker's command line: login, hydrate, capture-and-push, warm-sessions."""

from __future__ import annotations

import asyncio
import logging
import time

import typer

from .accounts import from_env, pull_account, push_account
from .browser import (
    NeedsHumanLogin,
    NotLoggedInError,
    login_interactive,
    login_with_account,
)
from .channels.probes import CHANNEL_PROBES
from .daemon import run_daemon
from .hydrate import hydrate_from_api
from .observability import init_sentry
from .reauth import (
    ReloginOutcome,
    _heal_once,
    _load_account,
    _try_auto_relogin,
)
from .warm import hydrate_then_warm, push_probe, warm_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aggregator-bootstrap")

app = typer.Typer(help="Aggregator session bootstrap/warmer worker.")


@app.callback()
def _bootstrap_observability() -> None:
    """Initialise Sentry before any command runs — the daemon and every one-shot.

    A Typer callback fires ahead of the chosen subcommand, so `serve` and a
    hand-run `login`/`heal-sessions` all report to Sentry with no per-command
    wiring. No-op without a `SENTRY_DSN` (local/dev).
    """
    init_sentry()


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
            if auto_relogin and _try_auto_relogin(channel) is ReloginOutcome.OK:
                continue
            logger.error("%s needs a headed login: %s", channel, exc)
        except Exception:  # noqa: BLE001 — one channel must not stop the rest
            logger.exception("%s capture failed", channel)


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


@app.command("create-keeta-item")
def create_keeta_item(
    shop_id: str = typer.Option(..., help="Keeta shop id (from SHOP_IDS)"),
    name: str = typer.Option(..., help="Item name"),
    category_id: str = typer.Option(..., help="Keeta shopCategory id (menu section)"),
    price: str = typer.Option(..., help="Price, e.g. 35"),
    backend_category_id: str = typer.Option(
        "",
        "--backend-category-id",
        help="Platform backend category (后台类目); default: read from listConfig.",
    ),
    active: bool = typer.Option(
        False, "--active", help="Put it on-shelf immediately (default: off-shelf)."
    ),
) -> None:
    """Create one Keeta menu item in-page (mtgsig `saveSpu`), off-shelf by default.

    A live storefront WRITE — deliberate, never part of a sweep. The `saveSpu` payload
    is verified live (create-then-delete, code 0). The backend category is resolved
    from `listConfig` unless you pass `--backend-category-id`. Reads the hydrated Keeta
    session; open a headed `login --channel keeta` first if the session is dead.
    """
    from .warm import create_keeta_item_in_page

    try:
        result = asyncio.run(
            create_keeta_item_in_page(
                shop_id=shop_id,
                name=name,
                category_id=category_id,
                price=price,
                active=active,
                backend_category_id=backend_category_id or None,
            )
        )
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        logger.error("keeta create needs a headed login: %s", exc)
        raise typer.Exit(code=1) from exc
    code = result.get("code") if isinstance(result, dict) else None
    if code == 0:
        logger.info("keeta item created (code 0): %s", result.get("data"))
    else:
        logger.error("keeta create did not succeed: %s", result)
        raise typer.Exit(code=1)


@app.command("delete-keeta-item")
def delete_keeta_item(
    shop_id: str = typer.Option(..., help="Keeta shop id (from SHOP_IDS)"),
    spu_id: str = typer.Option(..., help="Keeta spuId to delete (from listSpu)"),
) -> None:
    """Delete one Keeta menu item in-page (mtgsig `deleteSpu`).

    The reverse of `create-keeta-item` — the cleanup half of the controlled
    create-then-delete verification, and the operator's remove entry point.
    Endpoint verified live 2026-09-01 (a bad id returns a *validation* error, not
    path-not-found). A live storefront WRITE, deliberate, never part of a sweep.
    """
    from .warm import delete_keeta_item_in_page

    try:
        result = asyncio.run(delete_keeta_item_in_page(shop_id=shop_id, spu_id=spu_id))
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        logger.error("keeta delete needs a headed login: %s", exc)
        raise typer.Exit(code=1) from exc
    code = result.get("code") if isinstance(result, dict) else None
    if code == 0:
        logger.info("keeta item deleted (code 0): %s", spu_id)
    else:
        logger.error("keeta delete did not succeed: %s", result)
        raise typer.Exit(code=1)


@app.command("copy-keeta-menu")
def copy_keeta_menu(
    source_shop_id: str = typer.Option(
        ...,
        help="Source Keeta shop id (a Foodics-synced branch, e.g. Sharjah 1644174206)",
    ),
    target_shop_ids: str = typer.Option(
        ...,
        help="Comma-separated target Keeta shop ids (non-Foodics, e.g. 1644336388,1644170195)",
    ),
) -> None:
    """Copy a Keeta store's FULL menu into other stores (mtgsig `synchronizeMenu`).

    The easy path to sync the non-Foodics Keeta branches: copy from a Foodics-synced
    source (Sharjah/Barsha) into the non-Foodics targets (Al Karama, DSO). Fully
    replaces each target's menu via a server-side task that settles over a few
    minutes — check it with `keeta-copy-tasks`. Endpoint + payload captured live
    2026-09-05. A live storefront WRITE, deliberate, never part of a sweep. Foodics
    branches are not valid targets (the portal locks them).
    """
    from .warm import copy_keeta_menu_in_page

    targets = [t.strip() for t in target_shop_ids.split(",") if t.strip()]
    if not targets:
        logger.error("no target shop ids given")
        raise typer.Exit(code=1)
    try:
        result = asyncio.run(
            copy_keeta_menu_in_page(
                source_shop_id=source_shop_id, target_shop_ids=targets
            )
        )
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        logger.error("keeta copy-menu needs a headed login: %s", exc)
        raise typer.Exit(code=1) from exc
    code = result.get("code") if isinstance(result, dict) else None
    if code == 0:
        logger.info(
            "keeta copy-menu task created: source=%s targets=%s — poll keeta-copy-tasks",
            source_shop_id,
            targets,
        )
    else:
        logger.error("keeta copy-menu did not succeed: %s", result)
        raise typer.Exit(code=1)


@app.command("keeta-copy-tasks")
def keeta_copy_tasks() -> None:
    """List the Keeta menu-copy sync tasks (the portal's "Task progress" tab).

    Poll after `copy-keeta-menu` to see each task's status (running / success /
    partial failure). Read-only — never part of a sweep.
    """
    from .warm import list_keeta_menu_copy_tasks_in_page

    try:
        result = asyncio.run(list_keeta_menu_copy_tasks_in_page())
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        logger.error("keeta copy-tasks needs a headed login: %s", exc)
        raise typer.Exit(code=1) from exc
    logger.info(
        "keeta menu-copy tasks: %s",
        result.get("data") if isinstance(result, dict) else result,
    )


@app.command("fetch-keeta-hours")
def fetch_keeta_hours() -> None:
    """Audit each Keeta shop's business status + today's opening hours.

    Keeta exposes only *today's* hours (no weekly schedule), signed in-page. Opens
    the persistent `keeta.chrome` profile; a headed `login --channel keeta` first
    if the profile is missing. Read-only — never part of a sweep.
    """
    from .warm import pull_keeta_hours_in_page

    def _hhmm(seconds: object) -> str:
        s = int(seconds or 0)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"

    try:
        shops = asyncio.run(pull_keeta_hours_in_page())
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        logger.error("keeta hours need a headed login: %s", exc)
        raise typer.Exit(code=1) from exc
    if not shops:
        logger.warning("keeta hours: nothing returned (no shops or signed out)")
        raise typer.Exit(code=1)
    for shop in shops:
        wins = "; ".join(
            f"{_hhmm(w.get('startTime'))}-{_hhmm(w.get('endTime'))}"
            for w in (shop.get("todayBusinessHours") or [])
        )
        status = shop.get("businessStatus")
        print(  # noqa: T201 — operator output
            f"keeta shop {shop.get('shopId')}: businessStatus={status} "
            f"(1=open) today={wins or 'closed'}"
        )


@app.command("sync-keeta-hours")
def sync_keeta_hours() -> None:
    """Apply MM's weekly schedule to each Keeta shop in-page — the on-demand twin
    of the nightly `KEETA_HOURS` job.

    Keeta cannot be written over httpx (its `mtgsig` request signing lives in the
    page and bypasses fetch/XHR), so this is how Keeta joins the other five: pull
    MM's schedule from the API (`GET /worker/keeta/hours`), mirror it on the
    persistent `keeta.chrome` profile, and report each shop's outcome back
    (`POST /keeta/hours-result`, so it lands in `branch_hours_sync_run`). `dry_run`
    follows the API's `BRANCH_HOURS_SYNC_LIVE`. One Chrome — stop the daemon first
    if it holds `keeta.chrome`.
    """
    from . import daemon, push, warm

    async def _run() -> None:
        sched = await push.pull_keeta_hours()
        shops = sched.get("shops") or []
        dry = bool(sched.get("dry_run", True))
        if not shops:
            print(  # noqa: T201 — operator output
                "keeta hours: nothing to sync (gate off, or no mapped shop has an "
                "MM schedule)"
            )
            return
        windows = [
            {"shop_id": s.get("shop_id"), "weekly": s.get("weekly")}
            for s in shops
            if s.get("shop_id")
        ]
        result = await warm.write_keeta_hours_in_page(
            windows=windows, persist=True, dry_run=dry
        )
        outcomes = daemon._keeta_hours_outcomes(result, dry_run=dry)
        if outcomes:
            await push.push_keeta_hours_result(outcomes)
        print(  # noqa: T201 — operator output
            f"keeta hours sync: dry_run={dry} shops={len(windows)} "
            f"saved={result.get('saved')} outcomes={len(outcomes)}"
        )
        for o in outcomes:
            print(  # noqa: T201 — operator output
                f"  shop {o['shop_id']}: ok={o['ok']} dry={o.get('dry_run')} "
                f"err={o.get('error')}"
            )

    try:
        asyncio.run(_run())
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        logger.error("keeta hours sync needs a headed login: %s", exc)
        raise typer.Exit(code=1) from exc


@app.command("probe-keeta-hours-save")
def probe_keeta_hours_save(
    wait_seconds: int = typer.Option(
        90,
        help=(
            "Seconds to sit on Shop settings listening for a save XHR. "
            "Attach CDP and click Save once during this window."
        ),
    ),
) -> None:
    """Listen for the Keeta hours-save XHR on the persistent profile.

    The save verb is captured (`POST /api/scm/business-hour/update`); the nightly
    `KEETA_HOURS` job POSTs it. This command stays listen-only (`persist=False`)
    so a confirmation click-Save cannot collide with a write. One Chrome — stop
    the daemon first if it holds `keeta.chrome`.
    """
    from .warm import write_keeta_hours_in_page

    try:
        result = asyncio.run(
            write_keeta_hours_in_page(wait_seconds=wait_seconds, persist=False)
        )
    except (NeedsHumanLogin, NotLoggedInError) as exc:
        logger.error("keeta hours probe needs a headed login: %s", exc)
        raise typer.Exit(code=1) from exc
    save_path = result.get("save_path")
    captured = result.get("captured_xhrs") or []
    shop_posts = result.get("all_shop_posts") or []
    if save_path:
        print(f"keeta hours save path: {save_path}")  # noqa: T201
        for path in captured:
            print(f"keeta hours captured xhr: {path}")  # noqa: T201
        return
    for path in shop_posts:
        print(f"keeta shop POST (not hours-save shaped): {path}")  # noqa: T201
    logger.error(
        "keeta hours save XHR not captured. Attach CDP during the wait, click "
        "Save on Shop hours, re-run."
    )
    raise typer.Exit(code=1)


@app.command("serve")
def serve() -> None:
    """Run the always-on worker daemon (compose `serve`).

    The one long-lived process that replaces the three host-cron one-shots: one
    priority queue, one browser at a time (Chrome spawned per job, torn down
    after), a scheduler that enqueues the nightly anti-bot warm, the 3-hourly Keeta
    pull, the Deliveroo invoice pull and a session-health heal poll on cadence, and
    a hard per-job timeout that SIGKILLs a wedged Chrome. See `daemon.py`.
    """
    asyncio.run(run_daemon())


@app.command("serve-reauth")
def serve_reauth(
    interval: float = typer.Option(
        10.0, help="Seconds between health checks when idle."
    ),
) -> None:
    """One-shot heal loop — superseded by `serve`, kept for manual re-auth runs.

    Watches session health and re-logs-in any channel that goes dead. `serve` now
    does this (and the warms/pulls) as the resident service; this remains a light
    stand-alone loop for a hand-run recovery. It holds no browser open, spawning
    headed Chrome only for the seconds it takes to re-login a channel that needs it.
    """
    logger.info("reauth loop started (interval=%ss)", interval)
    while True:
        try:
            _heal_once()
        except Exception:  # noqa: BLE001 — the loop must outlive any one failure
            logger.exception("reauth loop: heal pass failed")
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
