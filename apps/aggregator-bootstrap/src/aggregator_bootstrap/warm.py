"""Capture and push a session — bootstrap, warm, and post-deploy hydrate.

Capturing from a logged-in `storage_state` and pushing is the same for a first
login and a periodic warm. The difference is only how the state got there: a
headed `login` writes it; a deploy/restart hydrates it from the API; a warm
reopens it, rotates the anti-bot cookie, and pushes the refresh back.

Keeta is the exception: it has no httpx sweep, so warming it also pulls its
orders (and best-effort finance) in-page (its `mtgsig` signing lives in the
page) and pushes the raw payloads to `/aggregators/keeta/orders` and
`/aggregators/keeta/finance`.
"""

from __future__ import annotations

import logging
from typing import Any

from .accounts import pull_account
from .browser import NeedsHumanLogin, NotLoggedInError, _storage_state_path
from .deliveroo_pull import fetch_deliveroo_invoices
from .hydrate import hydrate_from_api
from .keeta_pull import fetch_keeta_finance, fetch_keeta_orders
from .push import (
    push_deliveroo_finance,
    push_keeta_finance,
    push_keeta_orders,
    push_session,
)
from .session_capture import capture, payload_from_probe

logger = logging.getLogger(__name__)


async def hydrate_then_warm(channel: str | None = None) -> dict[str, Any]:
    """Pull sessions from the API, then warm one channel or all of them.

    Called on every worker start so a new image with an empty volume resumes
    the previous session instead of looking logged-out.
    """
    try:
        restored = await hydrate_from_api()
        logger.info("hydrated channels from API: %s", restored or "(none)")
    except Exception:  # noqa: BLE001 — local files may still be good
        logger.exception(
            "hydrate from API failed; continuing with any local storage_state"
        )
    if channel:
        return await warm_channel(channel)
    return {"hydrated": True}


async def warm_channel(channel: str) -> dict[str, Any]:
    """Re-capture the channel's session from its stored state and push it.

    Keeta is the exception: it has no httpx replay path, so warming it means
    pulling its orders (and best-effort finance) in-page (mtgsig is signed
    there) and pushing those, rather than capturing a session fingerprint for
    the sweep to replay.
    """
    if channel == "keeta":
        # Still recapture the browser state so a Keeta warm refreshes cookies
        # in the DB, then pull orders + finance in-page.
        try:
            payload = await capture("keeta")
            await push_session(payload)
        except NeedsHumanLogin:
            logger.error("keeta needs a headed login; skipping in-page pull")
            raise
        return await pull_keeta_orders_in_page()
    payload = await capture(channel)
    result = await push_session(payload)
    logger.info("pushed %s session: %s", channel, result.get("status"))
    if channel == "deliveroo":
        # The invoice list replays over httpx, but the invoice DOWNLOAD 403s
        # behind Cloudflare — so pull it in-page here, after the session warm.
        # Best-effort: a download failure must not fail the session refresh.
        try:
            finance = await pull_deliveroo_invoices_in_page()
            result = {**result, "deliveroo_finance": finance}
        except Exception:  # noqa: BLE001 — the session warm already succeeded
            logger.exception(
                "deliveroo in-page invoice pull failed — session warm still ok"
            )
    return result


async def push_probe(channel: str, result) -> dict[str, Any]:
    """Push a session assembled from an already-open probe (headed login)."""
    payload = payload_from_probe(channel, result)
    pushed = await push_session(payload)
    logger.info("pushed %s session after login: %s", channel, pushed.get("status"))
    return pushed


async def pull_keeta_orders_in_page(*, months_back: int = 1) -> dict[str, Any]:
    """Fetch Keeta orders + best-effort finance in-page and push the payloads.

    Uses the hydrated Playwright `storage_state` + sessionStorage, *not* the
    headed Chrome profile. That profile often keeps a stale HK login redirect
    that clears `LOGIN_ACCOUNTID` even when the API session blob is good.
    Playwright is imported lazily so this module imports without the browser lib.

    The Keeta account's `extras` (`shop_ids`, `customer_id`) are threaded into
    the finance pull as *fallbacks* — the live V2 endpoint and sessionStorage
    still win, and an account with no extras behaves exactly as before (same
    idiom as the Deliveroo `org_id` pull below).
    """
    from .browser import _open_storage_state_context
    from .engine import async_playwright

    account = await pull_account("keeta")
    extras = (account.extras if account else None) or {}
    raw_shop_ids = extras.get("shop_ids")
    fallback_shop_ids = (
        [str(s) for s in raw_shop_ids if s] if isinstance(raw_shop_ids, list) else None
    ) or None
    customer_id = str(extras.get("customer_id") or "").strip() or None

    state = _storage_state_path("keeta")
    if not state.exists():
        raise NotLoggedInError(
            f"keeta session state missing at {state}; run a login/bootstrap first"
        )

    async with async_playwright() as pw:
        opened = await _open_storage_state_context(pw, "keeta")
        try:
            payloads = await fetch_keeta_orders(opened.context, months_back=months_back)
            finance_payloads, finance_note = await fetch_keeta_finance(
                opened.context,
                months_back=max(months_back, 2),
                fallback_shop_ids=fallback_shop_ids,
                customer_id=customer_id,
            )
        finally:
            await opened.close()

    out: dict[str, Any] = {"ingested": 0, "payloads": 0}
    if payloads:
        result = await push_keeta_orders(payloads)
        logger.info("pushed %d keeta getOrders payloads: %s", len(payloads), result)
        out.update(result)
        out["payloads"] = len(payloads)
    else:
        logger.warning("keeta: no getOrders payloads fetched; nothing to push")

    if finance_payloads:
        finance_result = await push_keeta_finance(finance_payloads)
        logger.info(
            "pushed %d keeta finance payloads: %s",
            len(finance_payloads),
            finance_result,
        )
        out["finance"] = finance_result
    elif finance_note:
        logger.warning("keeta finance: %s", finance_note)
        out["finance_truncation_note"] = finance_note

    return out


async def pull_deliveroo_invoices_in_page(*, since_days: int = 45) -> dict[str, Any]:
    """Fetch Deliveroo invoices (CSV + PDF) in-page and push the payloads.

    Resolves the org id from the stored Deliveroo account `extras` (the same
    `org_id` the httpx provider reads), opens the hydrated storage_state context
    (headed real Chrome under Xvfb, which passes Cloudflare), downloads each
    in-window invoice's statement CSV and PDF in-page, and pushes them to the
    API's `/deliveroo/finance` endpoint. Playwright is imported lazily so this
    module imports without the browser lib.
    """
    from .browser import _open_storage_state_context
    from .engine import async_playwright

    account = await pull_account("deliveroo")
    org_id = str((account.extras.get("org_id") if account else None) or "").strip()
    if not org_id:
        logger.warning(
            "deliveroo: no org_id in account extras — skipping in-page invoice pull"
        )
        return {"skipped": "no org_id"}

    state = _storage_state_path("deliveroo")
    if not state.exists():
        raise NotLoggedInError(
            f"deliveroo session state missing at {state}; run a login/bootstrap first"
        )

    async with async_playwright() as pw:
        opened = await _open_storage_state_context(pw, "deliveroo")
        try:
            # Refresh the browser token FIRST. The hydrated storage_state's web
            # session goes stale quickly — the console redirects to /login and
            # the invoice DOWNLOAD then 401s (verified on the VM). Headed real
            # Chrome already clears Cloudflare on the download (the block is 401
            # auth, no longer a 403 interstitial), so all that is missing is a
            # fresh token: an email/password re-login (Deliveroo has no OTP) mints
            # a new `token` cookie context-wide, which the in-page download then
            # carries. Best-effort — a login failure still attempts the fetch, so
            # a transient login hiccup degrades to "list only", never a crash.
            if account and account.email and account.password:
                try:
                    from .channels.login import login_deliveroo

                    await login_deliveroo(
                        opened.context,
                        email=account.email,
                        password=account.password,
                    )
                except Exception:  # noqa: BLE001 — fall through to a best-effort fetch
                    logger.warning(
                        "deliveroo: in-page re-login failed; attempting the "
                        "invoice fetch with the hydrated session anyway"
                    )
            payloads, note = await fetch_deliveroo_invoices(
                opened.context, org_id=org_id, since_days=since_days
            )
        finally:
            await opened.close()

    out: dict[str, Any] = {"payloads": len(payloads)}
    if payloads:
        result = await push_deliveroo_finance(payloads)
        logger.info("pushed %d deliveroo invoice payloads: %s", len(payloads), result)
        out.update(result)
    else:
        logger.warning("deliveroo: no invoice payloads fetched; nothing to push")
    if note:
        logger.warning("deliveroo finance: %s", note)
        out["truncation_note"] = note
    return out


__all__ = [
    "warm_channel",
    "pull_keeta_orders_in_page",
    "pull_deliveroo_invoices_in_page",
    "push_keeta_orders",
    "hydrate_then_warm",
    "push_probe",
]
