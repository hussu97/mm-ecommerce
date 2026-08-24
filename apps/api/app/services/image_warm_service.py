"""Builds the storefront's image derivatives before a customer asks for one.

`/_next/image` produces a resized, re-encoded copy on the *first* request for
each (url, width, quality, format) and caches it. Measured against production
that first request took between 0.5s and 9.8s to first byte — the encode, not
the transfer — while a warm one comes back in about 0.15s.

Nothing about that is visible from the admin console: an image looks fine the
moment it is uploaded, because whoever uploaded it warmed it by looking at it.
It is the next customer to land on a page nobody has opened yet who waits. So
the warm has to happen wherever an image URL enters the catalogue, rather than
in a script somebody has to remember to run.

Every entry point here is fire-and-forget. A slow or unreachable storefront must
never turn an admin's successful upload into a failed request.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Live fire-and-forget warms. See `warm_in_background`.
_pending: set[asyncio.Task] = set()

# The widths a browser can actually land on, given the `sizes` the storefront
# declares: product cards resolve to 384-1080 across phone and desktop DPRs, and
# the product page's 50vw at 2x is what reaches for 1920. Anything else in
# `deviceSizes` is unreachable and would be a derivative nobody requests.
WARM_WIDTHS: tuple[int, ...] = (384, 640, 750, 828, 1080, 1200, 1920)

# Matches `qualities` in the storefront's next.config.ts. Requesting any other
# value is rejected there, and would be a second full set of transforms anyway.
QUALITY = 75

# AVIF is what every current browser negotiates and the expensive one to encode.
# WebP only serves Safari older than 16, so warming it would double the work to
# cover a shrinking tail — leave that to be produced on demand.
ACCEPT = "image/avif,image/webp,image/*,*/*"

CONCURRENCY = 6
TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# A full Foodics import is ~120 images, which is 840 requests and a few minutes
# of background work. That is fine once; it is not fine if somebody re-uploads
# the CSV five times in a row. Past this the rest is left to warm on demand, and
# the truncation is logged rather than silently swallowed.
MAX_IMAGES_PER_RUN = 200

# The hero and promo bands render a hand-written <picture> against pre-built
# AVIF/WebP siblings, never through the optimiser — see apps/web/lib/images.ts.
# Warming these would mint derivatives that no page will ever request.
_ART_DIRECTED = re.compile(r"^/images/banners/", re.IGNORECASE)

# Finds image URLs anywhere in a CMS content blob without having to know which
# keys hold them — the shape differs per page and per section.
_IMAGE_VALUE = re.compile(
    r"^(https?://\S+|/[\w./-]+)\.(jpe?g|png|webp|avif)$", re.IGNORECASE
)


def warmable(url: str | None) -> bool:
    """Whether the storefront will serve this URL through `/_next/image`."""
    if not url:
        return False
    url = url.strip()
    if _ART_DIRECTED.match(url):
        return False
    return url.startswith(("http://", "https://", "/"))


def collect_image_urls(value: object) -> list[str]:
    """Every image URL reachable in a nested CMS content structure."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            if _IMAGE_VALUE.match(node.strip()):
                found.append(node.strip())
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(value)
    return found


def _variants(url: str) -> list[str]:
    base = settings.WEB_URL.rstrip("/")
    encoded = quote(url, safe="")
    return [f"{base}/_next/image?url={encoded}&w={w}&q={QUALITY}" for w in WARM_WIDTHS]


async def warm(urls: Iterable[str]) -> int:
    """Request every derivative the storefront can ask for. Returns how many succeeded.

    Never raises. The caller is an admin action that has already succeeded, and
    a storefront that is redeploying is not a reason to fail it.
    """
    targets = sorted({u.strip() for u in urls if warmable(u)})
    if not targets:
        return 0

    if len(targets) > MAX_IMAGES_PER_RUN:
        logger.warning(
            "image warm capped at %d of %d images; the rest warm on first request",
            MAX_IMAGES_PER_RUN,
            len(targets),
        )
        targets = targets[:MAX_IMAGES_PER_RUN]

    requests = [href for url in targets for href in _variants(url)]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:

        async def fetch(href: str) -> bool:
            async with semaphore:
                try:
                    response = await client.get(href, headers={"Accept": ACCEPT})
                    return response.status_code == 200
                except httpx.HTTPError as exc:
                    logger.warning("image warm failed for %s: %s", href, exc)
                    return False

        results = await asyncio.gather(
            *(fetch(href) for href in requests), return_exceptions=True
        )

    warmed = sum(1 for r in results if r is True)
    logger.info(
        "warmed %d/%d derivatives across %d images", warmed, len(requests), len(targets)
    )
    return warmed


async def warm_quietly(urls: Iterable[str]) -> None:
    """`warm`, with every failure swallowed."""
    try:
        await warm(urls)
    except Exception:  # pragma: no cover - defensive
        logger.exception("image warm task failed")


def warm_in_background(urls: Iterable[str]) -> None:
    """
    Warm the CDN without making the uploader wait for it.

    This used to be `BackgroundTasks`, which convention 5 rules out: those run
    after the response on whatever process served it, and on a serverless or
    scaled-to-zero host that process can be frozen the moment the response goes
    out — the warm is dropped and nothing says so. Same shape as
    `indexnow_service.submit_in_background`, which is the pattern that already
    solved this problem in this codebase.
    """
    urls = list(urls)
    if not urls:
        return
    try:
        task = asyncio.create_task(warm_quietly(urls))
    except RuntimeError:
        # No running loop — a management command, or a test.
        logger.debug("image warm: no event loop, skipped %d url(s)", len(urls))
        return

    # `create_task` does not keep the task alive: the loop holds only a weak
    # reference, so a task nobody awaits can be collected mid-flight. The set
    # holds it until it finishes and the callback stops the set being a leak.
    _pending.add(task)
    task.add_done_callback(_pending.discard)
