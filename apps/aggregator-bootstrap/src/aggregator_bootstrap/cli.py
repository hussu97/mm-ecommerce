"""The worker's command line: capture-and-push and warm-sessions."""

from __future__ import annotations

import asyncio
import logging

import typer

from .browser import NotLoggedInError
from .channels.probes import CHANNEL_PROBES
from .warm import warm_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aggregator-bootstrap")

app = typer.Typer(help="Aggregator session bootstrap/warmer worker.")

_CHANNELS = tuple(CHANNEL_PROBES)


def _run_for(channels: list[str]) -> None:
    for channel in channels:
        if channel not in CHANNEL_PROBES:
            logger.error(
                "unknown channel %s (known: %s)", channel, ", ".join(_CHANNELS)
            )
            continue
        try:
            asyncio.run(warm_channel(channel))
        except NotLoggedInError as exc:
            logger.error("%s needs a fresh login: %s", channel, exc)
        except Exception:  # noqa: BLE001 — one channel must not stop the rest
            logger.exception("%s capture failed", channel)


@app.command("capture-and-push")
def capture_and_push(
    channel: str = typer.Option(None, help="Channel to capture"),
    all_channels: bool = typer.Option(False, "--all", help="All channels"),
) -> None:
    """Capture the session for a channel (or --all) and push it to the API."""
    _run_for(list(_CHANNELS) if all_channels else [channel] if channel else [])


@app.command("warm-sessions")
def warm_sessions(
    channel: str = typer.Option(None, help="Channel to warm; omit for all"),
) -> None:
    """Refresh sessions (re-run the anti-bot sensor, refresh tokens) and push."""
    _run_for([channel] if channel else list(_CHANNELS))


if __name__ == "__main__":
    app()
