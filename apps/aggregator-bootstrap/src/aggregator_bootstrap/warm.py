"""Capture and push a session — the operation both the bootstrap and warm run.

Capturing from a logged-in `storage_state` and pushing is the same for a first
bootstrap and a periodic warm; the difference is only whether a login had to
happen first (a full bootstrap does the OTP login to establish the state; a warm
assumes it exists and just re-runs the sensor by loading a page). Both end here.

Keeta is the exception: it has no httpx sweep, so warming it also pulls its
orders in-page and pushes them. That in-page pull is the one piece still to be
ported from the standalone scraper's `channels/keeta` fetch (it needs the page's
own signing), and is stubbed here with a clear error until it lands.
"""

from __future__ import annotations

import logging
from typing import Any

from .push import push_keeta_orders, push_session
from .session_capture import capture

logger = logging.getLogger(__name__)


async def warm_channel(channel: str) -> dict[str, Any]:
    """Re-capture the channel's session from its stored state and push it."""
    payload = await capture(channel)
    result = await push_session(payload)
    logger.info("pushed %s session: %s", channel, result.get("status"))
    return result


async def pull_keeta_orders_in_page() -> dict[str, Any]:
    """Fetch Keeta's getOrders in-page (mtgsig-signed) and push the payloads.

    Not yet ported: the in-page fetch must run through Keeta's own request
    signing (see mm-aggregator-automation channels/keeta/exports.py). Until then
    this raises rather than pretending to have pulled orders.
    """
    raise NotImplementedError(
        "Keeta in-page order pull is not yet ported; see "
        "mm-aggregator-automation/.../channels/keeta/exports.py. Once wired, it "
        "calls push_keeta_orders(payloads)."
    )


__all__ = ["warm_channel", "pull_keeta_orders_in_page", "push_keeta_orders"]
