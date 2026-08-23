"""Who is carrying an order, as one small badge the register and admin can show.

Every surface that wants to *identify* a carrier — the POS board, the admin
order list, the fulfilment panel — needs the same three things: a stable code, a
display name, and a logo. This module is the one place that answers that, for
both kinds of carrier:

* the couriers MM dispatches itself (`lalamove`, `noon_send`, `slider`,
  `third_party`), keyed by the order's fulfilment `provider`; and
* the marketplace channels an aggregator order arrives on (`talabat`, `keeta`,
  `noon_food`, `deliveroo`, `careem`), keyed by the order's `aggregator_channel`.

The **logo URL is served from the `couriers` table**, not hard-coded, so a logo
can be swapped in the database and every app follows without a release. The rows
are cached in-process (they change almost never) and refreshed at startup by
`load`; until then, and in tests with no database, a conventional URL
(`{LOGO_BASE}/{code}.png`) stands in so a badge always has *a* logo. The badges
live in the same public GCS image bucket the storefront's product images do,
under a `couriers/` prefix.

`noon_food` (the Noon Food marketplace) and `noon_send` (the Rider-on-Demand
courier) are two different businesses and carry two different logos — kept apart
by their distinct codes and image files.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Where the badges are served from — the public GCS image bucket, under a
#: `couriers/` prefix (migration 134). Only the fallback uses it; the real URL
#: is read from `couriers.logo_url` so it can be swapped in the database.
_LOGO_BASE = "https://storage.googleapis.com/mm-product-images/couriers"

__all__ = [
    "COURIER_NAMES",
    "AGGREGATOR_CODES",
    "code_for_channel",
    "logo_url_for",
    "badge_for_order",
    "load",
]

#: code → display name. The four dispatch couriers plus the five marketplace
#: channels. `noon_food` is kept distinct from the `noon_send` courier on
#: purpose — two different "noon" businesses.
COURIER_NAMES: dict[str, str] = {
    "lalamove": "Lalamove",
    "noon_send": "noon Send",
    "slider": "Slider",
    "third_party": "Third party",
    "talabat": "Talabat",
    "keeta": "Keeta",
    "noon_food": "Noon Food",
    "deliveroo": "Deliveroo",
    "careem": "Careem",
}

#: The marketplace channel codes, so callers can tell the two kinds apart.
AGGREGATOR_CODES: frozenset[str] = frozenset(
    {"talabat", "keeta", "noon_food", "deliveroo", "careem"}
)

#: code → logo URL, loaded from the `couriers` table by `load`. Empty until then;
#: `logo_url_for` falls back to the convention while it is.
_LOGO_CACHE: dict[str, str] = {}

#: The channel name GrubOps sends (its `foodAggregatorName` / `sourceDisplayName`)
#: normalised to our code. Lower-cased and matched loosely so "Keeta 2.0",
#: "Noon", "Noon Food" and the like all land. Anything unrecognised returns None
#: rather than guessing — an unknown channel simply shows no badge.
_CHANNEL_ALIASES: dict[str, str] = {
    "talabat": "talabat",
    "keeta": "keeta",
    "keeta 2.0": "keeta",
    "keeta2.0": "keeta",
    "noon": "noon_food",
    "noon food": "noon_food",
    "noonfood": "noon_food",
    "deliveroo": "deliveroo",
    "careem": "careem",
    "careem now": "careem",
    "careemnow": "careem",
}


async def load(db) -> None:
    """Refresh the code → logo_url cache from the `couriers` table.

    Called at app startup. Best-effort: a failure leaves the convention
    fallback in place rather than taking a request path down.
    """
    from sqlalchemy import select

    from app.models.courier import Courier

    try:
        rows = (await db.execute(select(Courier.code, Courier.logo_url))).all()
    except Exception:  # noqa: BLE001 — startup best-effort
        logger.exception("courier logo cache load failed; using convention URLs")
        return
    _LOGO_CACHE.clear()
    for code, logo_url in rows:
        if logo_url:
            _LOGO_CACHE[code] = logo_url
    logger.info("courier logo cache loaded: %d logos", len(_LOGO_CACHE))


def code_for_channel(channel: str | None) -> str | None:
    """Our courier code for a GrubOps channel name, or None if unrecognised."""
    if not channel:
        return None
    key = channel.strip().lower()
    if key in _CHANNEL_ALIASES:
        return _CHANNEL_ALIASES[key]
    # Tolerate trailing version noise like "Keeta 2.0" → keep only letters/space.
    letters = "".join(c for c in key if c.isalpha() or c == " ").strip()
    return _CHANNEL_ALIASES.get(letters)


def logo_url_for(code: str) -> str:
    """The logo URL for a courier code — the DB value if loaded, else the
    conventional ``{LOGO_BASE}/{code}.png`` in the public GCS image bucket."""
    if code in _LOGO_CACHE:
        return _LOGO_CACHE[code]
    return f"{_LOGO_BASE}/{code}.png"


def _badge(code: str | None) -> dict | None:
    if not code or code not in COURIER_NAMES:
        return None
    return {
        "code": code,
        "name": COURIER_NAMES[code],
        "logo_url": logo_url_for(code),
        "is_aggregator": code in AGGREGATOR_CODES,
    }


def badge_for_order(
    *,
    source: str | None,
    aggregator_channel: str | None = None,
    delivery_provider: str | None = None,
) -> dict | None:
    """The carrier badge for an order, from whichever key applies.

    An aggregator order is identified by its channel; any other delivery order
    by the courier its fulfilment was handed to. A counter sale, or an order
    with neither, has no carrier and returns None.
    """
    if source == "aggregator":
        return _badge(code_for_channel(aggregator_channel))
    if delivery_provider:
        return _badge(delivery_provider)
    return None
