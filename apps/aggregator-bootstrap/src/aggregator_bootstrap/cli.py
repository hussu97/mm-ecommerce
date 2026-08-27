"""The worker's command line: login, hydrate, capture-and-push, warm-sessions."""

from __future__ import annotations

import asyncio
import logging

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
        help="Fill stored email/password after Cloudflare (Deliveroo).",
    ),
) -> None:
    """Open a headed browser, wait for you to sign in, capture and push.

    Default: you complete OTP/captcha/passkey in the window. `--auto` (Deliveroo
    only) types the email/password from `aggregator_account` once the login
    form is visible. The resulting Playwright state is pushed to the API.
    """
    if channel not in CHANNEL_PROBES:
        raise typer.BadParameter(f"unknown channel {channel}")
    try:
        if auto:
            account = _load_account(channel)
            result = asyncio.run(
                login_with_account(
                    channel, email=account.email, password=account.password
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


def _load_account(channel: str) -> PortalAccount:
    try:
        account = asyncio.run(pull_account(channel))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not pull account from API (%s); trying env", exc)
        account = from_env(channel)
    if account is None or not account.email or not account.password:
        raise typer.BadParameter(
            f"no stored {channel} credentials. "
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
