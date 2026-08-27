"""The worker's command line: login, hydrate, capture-and-push, warm-sessions."""

from __future__ import annotations

import asyncio
import logging

import typer

from .browser import NeedsHumanLogin, NotLoggedInError, login_interactive
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
) -> None:
    """Open a headed browser, wait for you to sign in, capture and push.

    This is the only way a session is minted. OTP/captcha/passkey happen in
    the window. The resulting Playwright state is pushed to the API so the VM
    worker can hydrate it after a deploy or restart without asking you again.
    """
    if channel not in CHANNEL_PROBES:
        raise typer.BadParameter(f"unknown channel {channel}")
    try:
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
