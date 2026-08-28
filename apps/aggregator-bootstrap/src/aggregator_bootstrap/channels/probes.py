"""Per-channel capture recipe: what to load, and what to lift off the request.

The session the API replays is a fingerprint: the cookies, a few tokens, and the
exact header set the browser sends. Rather than a bespoke scraper per channel,
each channel declares here the page whose first authenticated API call carries
that fingerprint, the URL fragment that identifies that call, which request
headers make up the profile, and how its bearer / ids are lifted. `capture` then
runs the same steps for every channel.

The mapping mirrors what each mm-ecommerce provider reads from the session:
- careem: Authorization + Application/uuid/Meta/Time-Zone headers.
- deliveroo: the `token` cookie; org id from the reporting call.
- talabat: the accessToken cookie; x-global-entity-id header.
- noon: n-restaurantcode / x-project / x-locale headers.
- keeta: cookies only (its data is pulled in-page, not replayed).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelProbe:
    #: The page to open; its own scripts make the authenticated call we read.
    probe_url: str
    #: The URL fragment identifying that authenticated call.
    match: str
    #: Request headers to copy verbatim into `header_profile` (lower-cased match,
    #: original casing preserved from the request).
    header_keys: tuple[str, ...]
    #: request header name (lower) -> tokens key. e.g. authorization -> authorization.
    token_from_header: dict[str, str] = field(default_factory=dict)
    #: cookie names to lift into tokens as well (kept in cookies too).
    token_from_cookie: dict[str, str] = field(default_factory=dict)


_COMMON_UA = (
    "user-agent",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "accept-language",
)

CHANNEL_PROBES: dict[str, ChannelProbe] = {
    "careem": ChannelProbe(
        probe_url="https://partners.careem.com/saturn-ext/merchant/finances",
        match="/api/saturn-ext/",
        # Careem's provider replays Authorization from the header profile (it
        # does not re-add it from tokens), so it must be captured into the profile.
        header_keys=_COMMON_UA
        + ("authorization", "application", "time-zone", "meta", "uuid", "lat", "lng"),
        token_from_header={"authorization": "authorization"},
    ),
    "deliveroo": ChannelProbe(
        probe_url="https://partner-hub.deliveroo.com/reporting-platform",
        match="/api/",
        header_keys=_COMMON_UA + ("authorization",),
        token_from_cookie={"token": "token"},
    ),
    "talabat": ChannelProbe(
        probe_url="https://partner-app.talabat.com/finance",
        match="portal.restaurant",
        header_keys=_COMMON_UA + ("authorization", "x-global-entity-id"),
        token_from_header={"authorization": "authorization"},
        token_from_cookie={"accessToken": "accessToken"},
    ),
    "noon": ChannelProbe(
        # The console SPA root — an HTML page. It MUST NOT be a bare API path:
        # `page.goto` GETs the probe_url, and pointing it at the POST-only JSON
        # endpoint `/_food-restaurant/finance/wallet` made the browser navigation
        # fail with net::ERR_HTTP2_PROTOCOL_ERROR, so the whole warm aborted and
        # noon's Akamai cookie (bm_sv/_abck) was never rotated — the session was
        # kept alive only by the httpx ingest touching it, not by the warm. The
        # root loads under Akamai (rotating the cookie, the point of the warm),
        # and its own scripts make the authenticated `/_food-restaurant/` calls
        # the `match` lifts scope headers off.
        probe_url="https://restaurant.noon.partners/",
        match="/_food-restaurant/",
        header_keys=_COMMON_UA
        + ("n-restaurantcode", "x-project", "x-locale", "x-platform"),
        token_from_header={
            "n-restaurantcode": "restaurant_code",
            "x-project": "project",
        },
    ),
    "keeta": ChannelProbe(
        probe_url="https://merchant.mykeeta.com/m/web/app/home",
        match="/api/",
        header_keys=_COMMON_UA,
    ),
}
