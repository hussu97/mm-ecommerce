"""
Tell the search engines a URL changed, instead of waiting to be crawled.

IndexNow is a one-shot POST: here is a host, here is proof I own it, here are
the URLs that changed. `api.indexnow.org` fans the submission out to every
participating engine — Bing, Yandex, Seznam, Naver — so one call covers them
all. Google does not participate.

**Why this is worth having here at all.** Bing is the index ChatGPT's search
grounding leans on, and this shop's own measurements put it well ahead of Google
on unbranded queries: first in the map pack and around second organic for
"brownies delivery sharjah home bakery", against nothing at all on Google. So
the crawl that matters most is the one that can be pushed rather than waited
for. Migration 120 renamed all eight category slugs and moved 36 product URLs
with them; without this, the only signal Bing gets is the 308 it happens to
follow next time it visits.

**The key is public on purpose.** It is served at
`{WEB_URL}/{key}.txt` and its whole job is to prove that whoever is submitting
can write files on that host. It is not a credential and is not kept in the
environment: it lives in the repo next to the file that serves it, and
`test_indexnow.py` fails if the two drift apart. Someone who copies it can
submit URLs on this host and nothing else, which costs them effort and us
nothing.

**Never raises, and never blocks anything real.** A submission is a hint to a
crawler. Nothing a customer or an operator is doing should fail, or wait, because
a search engine was slow — so every call is fire-and-forget with a short timeout,
and every failure is a log line.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.background import report_result
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Live fire-and-forget submissions. See `submit_in_background`.
_pending: set[asyncio.Task] = set()

#: The shared endpoint. One POST reaches every participating engine, so there is
#: no reason to talk to `bing.com/indexnow` directly and no list to maintain.
ENDPOINT = "https://api.indexnow.org/indexnow"

#: Served from `apps/web/public/{INDEXNOW_KEY}.txt`. Public by design — see the
#: module docstring. Changing it means renaming that file in the same commit;
#: `test_indexnow.py` enforces it.
INDEXNOW_KEY = "08bc754bd7768fec80967166e019d610"

#: The protocol's cap on one request.
MAX_URLS = 10_000

#: Short on purpose. Nothing waits for this, and a slow crawler must not become
#: a slow admin save.
TIMEOUT_SECONDS = 5.0


#: The languages the storefront serves. A page exists at both, so a change is
#: two URLs. Mirrors `NEXT_PUBLIC_SUPPORTED_LOCALES` on the web side, which has
#: the same pair hard-coded as its default.
LOCALES = ("en", "ar")


def category_urls(slug: str) -> list[str]:
    """`/{locale}/{slug}` — the listing page, in both languages."""
    return [f"/{locale}/{slug}" for locale in LOCALES]


def product_urls(category_slug: str, slug: str) -> list[str]:
    """`/{locale}/{category}/{slug}`. Products are nested under their category,
    so a category rename moves every product with it — which is exactly the case
    `record_rename` submits for."""
    return [f"/{locale}/{category_slug}/{slug}" for locale in LOCALES]


def blog_urls(slug: str) -> list[str]:
    return [f"/{locale}/blog/{slug}" for locale in LOCALES]


def _absolute(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return f"{settings.WEB_URL.rstrip('/')}/{path_or_url.lstrip('/')}"


async def submit(paths: list[str]) -> bool:
    """
    Push a batch of changed URLs. Returns whether the engines accepted them.

    Paths may be site-relative (`/en/brownies`) or absolute; both end up
    absolute against `WEB_URL`, which is also what decides the `host` field —
    submitting a URL whose host does not match the key file is a 422.

    Silent outside production. A staging deploy announcing `localhost:3000` to
    Bing is at best noise and at worst a 422 that counts against the host.
    """
    if not paths:
        return False

    if not settings.is_production:
        logger.debug("IndexNow: skipped %d url(s) outside production", len(paths))
        return False

    urls = [_absolute(p) for p in paths][:MAX_URLS]
    if len(paths) > MAX_URLS:
        # Said out loud rather than truncated quietly: a caller that trips this
        # is submitting a whole catalogue and should know only part of it went.
        logger.warning(
            "IndexNow: %d urls exceeds the %d cap — submitting the first %d",
            len(paths),
            MAX_URLS,
            MAX_URLS,
        )

    host = httpx.URL(settings.WEB_URL).host
    payload = {
        "host": host,
        "key": INDEXNOW_KEY,
        "keyLocation": f"{settings.WEB_URL.rstrip('/')}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(ENDPOINT, json=payload)
    except Exception as exc:  # noqa: BLE001 — a hint to a crawler may not raise
        logger.warning("IndexNow: submission failed for %d url(s): %s", len(urls), exc)
        return False

    # 200 accepted; 202 accepted with the key still being validated, which is the
    # normal answer for the first submission after a key changes. Both are fine.
    if response.status_code in (200, 202):
        logger.info(
            "IndexNow: submitted %d url(s) — %s", len(urls), response.status_code
        )
        return True

    # 400 malformed, 403 key not found or not matching, 422 urls not on `host`,
    # 429 too many. All of them are our fault and all of them are silent without
    # this line, because nothing upstream is checking the return value.
    logger.warning(
        "IndexNow: rejected with %s for %d url(s) on %s — %s",
        response.status_code,
        len(urls),
        host,
        response.text[:200],
    )
    return False


def submit_in_background(paths: list[str]) -> None:
    """
    Fire a submission and stop caring about it.

    The callers are admin writes — saving a product, renaming a category — and
    none of them should spend a network round trip on a crawler hint before
    answering the person who clicked save. Convention 5 rules out
    `BackgroundTasks` (dropped on serverless); an orphaned task on the running
    loop is the right weight for something whose failure mode is "Bing finds out
    a day later".
    """
    if not paths:
        return
    try:
        task = asyncio.create_task(submit(paths))
    except RuntimeError:
        # No running loop — a management command, or a test. Nothing to do.
        logger.debug("IndexNow: no event loop, skipped %d url(s)", len(paths))
        return

    # `create_task` does not keep the task alive: the loop holds only a weak
    # reference, so a task nobody is awaiting can be collected mid-flight and
    # the submission simply never happens. Holding it in a module-level set
    # until it finishes is the documented way round that, and the discard is
    # what stops the set being a leak.
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    # Report an exception that escapes the task body (past submit()'s own
    # handling) instead of letting it vanish with the task's last reference.
    task.add_done_callback(report_result)
