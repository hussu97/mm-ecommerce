"""How the worker's browser presents itself.

A spoofed UA plus Playwright-driven Chrome is how Cloudflare loops: TLS, Client
Hints and UA disagree, and CDP is attached during the challenge. Headed login
therefore:

- launches **branded Google Chrome** as a normal OS process (not Playwright's
  Chrome-for-Testing, not a Playwright-controlled context)
- uses a dedicated `--user-data-dir` (Chrome 136+ refuses a debug port on the
  default profile)
- injects **nothing** — no User-Agent, no Client Hints, no locale/geo override,
  no stealth `init_script` (those mocks are themselves a detection signature)
- leaves Cloudflare to run with no CDP client attached; we connect afterwards
  to harvest the session

Warm/probe reopens that same Chrome profile with `channel="chrome"` and
`no_viewport=True`, still without extra headers.

`CHROME_UA` is kept only as the httpx replay fallback when a capture did not
include a UA. It is never applied to the browser.
"""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path
from typing import Any

CHROME_MAJOR = "151"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

ACCEPT_LANGUAGE = "en-AE,en;q=0.9,ar-AE;q=0.8,ar;q=0.7"
LOCALE = "en-AE"
TIMEZONE_ID = "Asia/Dubai"
GEOLOCATION = {"latitude": 25.2048, "longitude": 55.2708}

_CHROME_BINARIES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
)


def chrome_channel() -> str:
    """Playwright/Patchright channel for branded Google Chrome."""
    return "chrome"


def chrome_binary() -> str | None:
    """Absolute path to a real Chrome, or None if we fall back to Playwright's channel."""
    override = os.environ.get("AGGREGATOR_CHROME_BINARY", "").strip()
    if override and Path(override).is_file():
        return override
    for candidate in _CHROME_BINARIES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    return found


def chrome_profile_dir(storage_state_dir: str, channel: str) -> Path:
    """On-disk Chrome user-data dir — a returning profile passes CF more easily."""
    return Path(storage_state_dir) / f"{channel}.chrome"


def free_debug_port() -> int:
    """An ephemeral localhost port for Chrome's remote-debugging server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


#: The two flags every Chrome we launch inside the container needs, whether via
#: Playwright (`warm_persistent_kwargs` / `_open_storage_state_context`) or the
#: standalone spawn (`standalone_chrome_args`). Without a user namespace the
#: sandbox helper cannot fork and Chrome exits before opening a page ("Chrome did
#: not open a debug port"); the default 64 MB `/dev/shm` crashes it under real
#: pages. As non-root `pwuser`, Playwright's own root-detection never auto-adds
#: `--no-sandbox`, so every launch path must pass these explicitly. Neither flag
#: is readable by page JS, so neither is an anti-bot signal. Harmless on a
#: developer's macOS where the sandbox would work anyway.
CONTAINER_CHROME_ARGS: list[str] = ["--no-sandbox", "--disable-dev-shm-usage"]

#: Background-only flags that trim host CPU/RAM per browser without touching the
#: page-observable fingerprint. Each disables a HOST/BACKGROUND subsystem (crash
#: upload, profile sync, component + background update pings, first-run UI, the
#: hang monitor); NONE of them is readable by page JS — the same criterion the
#: comment above uses to call `--no-sandbox` safe. Deliberately EXCLUDES anything
#: page-observable (no `--disable-features`, no `--js-flags`, no automation flag,
#: no UA/viewport change) and is applied ONLY to the automated warm/pull launches,
#: never to the standalone `login` spawn that meets the initial anti-bot wall.
#: Gated by `WORKER_LEAN_CHROME` so it is revertible on the VM without a deploy.
LOW_OVERHEAD_CHROME_ARGS: list[str] = [
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-breakpad",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-client-side-phishing-detection",
    "--disable-hang-monitor",
    "--no-first-run",
    "--no-default-browser-check",
]


def standalone_chrome_args(
    *,
    binary: str,
    user_data_dir: Path,
    port: int,
    url: str,
) -> list[str]:
    """Command line for a human Chrome. No automation flags, no fingerprint injection.

    `--remote-debugging-port` is how we harvest the session *after* Cloudflare.
    Chrome 136+ requires a non-default `--user-data-dir` alongside it.
    `--remote-allow-origins=*` is required for a CDP client on modern Chrome;
    page JS cannot read the process command line, so it is not a CF signal.

    `--no-sandbox` / `--disable-dev-shm-usage` are required to start Chrome inside
    the container at all: without a user namespace the sandbox helper cannot fork
    and Chrome exits before it ever opens the debug port ("Chrome did not open a
    debug port"), and the default 64 MB `/dev/shm` makes it crash under real
    pages. Neither is readable by page JS, so neither is an anti-bot signal — the
    Playwright launch path passes both too (`CONTAINER_CHROME_ARGS`); this is the
    standalone spawn using the same constant so a headed `login` runs on the VM,
    not only a laptop.
    """
    return [
        binary,
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        *CONTAINER_CHROME_ARGS,
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def headed_persistent_kwargs() -> dict[str, Any]:
    """Playwright's documented headed launch. Do not add headers or a UA."""
    return {
        "channel": chrome_channel(),
        "headless": False,
        "no_viewport": True,
    }


def warm_persistent_kwargs(*, headed: bool) -> dict[str, Any]:
    """Same channel as headed. Headless needs a viewport; headed uses the OS window."""
    kwargs: dict[str, Any] = {
        "channel": chrome_channel(),
        "headless": not headed,
        "args": [*CONTAINER_CHROME_ARGS],
    }
    if headed:
        kwargs["no_viewport"] = True
    else:
        kwargs["viewport"] = {"width": 1440, "height": 900}
    return kwargs


def context_kwargs(
    *, storage_state: str | None = None, headed: bool = False
) -> dict[str, Any]:
    """Fallback context for a non-persistent launch. Still no UA spoof."""
    kwargs: dict[str, Any] = {
        "no_viewport": True,
    }
    if not headed:
        # Headless has no OS window; a viewport is required. Keep it unremarkable.
        kwargs.pop("no_viewport", None)
        kwargs["viewport"] = {"width": 1440, "height": 900}
    if storage_state:
        kwargs["storage_state"] = storage_state
    return kwargs
