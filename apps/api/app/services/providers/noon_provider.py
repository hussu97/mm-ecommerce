"""noon Food — dual-source sales: OMS (near-realtime items) + RMS (settled fees).

Ported from the Playwright exporter the bootstrap used to drive by hand. The
browser is only there to solve the login (email OTP + a passkey nag) and to run
Akamai's sensor; once a session is captured, every read here is a plain request
the console's own SPA makes — so the hourly path never opens a browser.

**Where the reads come from.**

*OMS* (`restaurant-orders.noon.partners`):
  `POST /_oms/order/panel/history` — near-realtime per-order JSON with item
  lines, modifier quantities, and outlet/customer timing. Paginated at
  `_OMS_PAGE_SIZE` per page, capped at `_OMS_MAX_PAGES` total pages (≈1 000
  orders). Orders whose `createdAt` falls outside the `since`/`until` window are
  discarded in Python. If the OMS call fails (auth or unavailability), `fetch_sales`
  falls back to RMS-only with a truncation note — the nightly ingest keeps running.

*RMS* (`restaurant.noon.partners`):
  Two surfaces:
  - *Order-level statement*: `POST /_food-restaurant/finance/statement/orders`
    with `{"statementNrList": [...]}`. CSV of every settled order — the source of
    truth for fees (commission, payment, delivery, VAT) and `statement_id`.
  - *Wallet tabs*: `POST /_food-restaurant/finance/wallet` with
    `{"entryType": "statement" | "payment"}`. Used to discover which statement
    reference numbers to query, and by `fetch_statements` / `fetch_payouts`.

**Merge strategy.** `fetch_sales` reads both sources for the window and merges
by `external_order_id`: OMS contributes `items`, `placed_at`/`accepted_at`/
`delivered_at`, `external_outlet_id`, and the outlet-subtotal money fields; RMS
fills `commission_amount`, per-fee fields, `vat_amount`, `net_payable`, and
`statement_id`. OMS-only orders (not yet settled) are returned with fee fields
as None. RMS-only orders (settled before / outside the OMS cap) carry no items.

**Modifier quantities.** OMS items carry modifiers as a nested qty map
`{MDxxx: {Ixxx: qty}}`. `expand_modifiers` expands this into `StandardModifier`
rows with the option code as `name` and `external_ref`, and the true qty — never
`json.dumps`.

**Identity (`n-restaurantcode` / `x-project` / `x-locale`).** Shared by both
hosts. Read from `session.tokens` first, then `session.header_profile`, then
`en-ae` for the locale. Not pinned to one outlet.

**Anti-bot.** Both hosts sit behind Akamai Bot Manager (`bm_sv` cookie, scoped
to `.noon.partners` so it covers both). `uses_tls_impersonation` is set;
`_is_auth_failure` reads the body for the "Access Denied / Reference #" deny
page. A block is an auth failure, never a transient retry.

Money is `Decimal | None`: None means "noon did not say", not zero. `commission_amount`
is derived from `fees_exc_vat` minus the itemised fees; None when `fees_exc_vat`
is absent. Every RMS record keeps its `raw`; OMS items keep their source order.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.aggregator import CHANNEL_NOON
from app.services.aggregators.modifiers import expand_modifiers
from app.services.aggregators.normalized import (
    PayoutsResult,
    SalesResult,
    StandardModifier,
    StandardOrder,
    StandardOrderItem,
    StandardPayout,
    StandardStatement,
    StatementsResult,
)
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.aggregator_base import (
    AggregatorAuthError,
    AggregatorUnavailableError,
    BaseAggregatorClient,
)

logger = logging.getLogger(__name__)

_RMS = "https://restaurant.noon.partners"
_ORDER_STATEMENT_URL = f"{_RMS}/_food-restaurant/finance/statement/orders"
_WALLET_URL = f"{_RMS}/_food-restaurant/finance/wallet"
_DEFAULT_LOCALE = "en-ae"

_OMS = "https://restaurant-orders.noon.partners"
_OMS_HISTORY_URL = f"{_OMS}/_oms/order/panel/history"
#: Orders per OMS page. 100 matches the console's own page size.
_OMS_PAGE_SIZE = 100
#: Hard cap: fetch at most this many pages (≈1 000 orders) per `fetch_sales` call.
#: If `data.pages` exceeds this, a truncation note is added to the result.
_OMS_MAX_PAGES = 10

#: Noon publishes wallet statements roughly weekly (~7–8 days between refs). The
#: shared daily ingest lookback is 1 day (order-date channels), which almost
#: always contains no statement *publication* date — so nightly sales looked
#: empty even though the restaurant traded. Widen statement/payment discovery
#: to cover at least one publish cycle; upserts stay idempotent.
_PUBLICATION_LOOKBACK_DAYS = 14


def _publication_since(since: datetime) -> datetime:
    """Earliest date for wallet statement/payment discovery (weekly publish grain)."""
    return since - timedelta(days=max(_PUBLICATION_LOOKBACK_DAYS - 1, 0))


#: Distinctive phrases from an Akamai Bot Manager deny page. Kept narrow so a
#: legitimate JSON/CSV settlement body never trips them.
_AKAMAI_MARKERS = (
    "access denied",
    "reference #",
    "akamaighost",
    "errors.edgesuite.net",
    "you don't have permission to access",
)


def _num(value: Any) -> Decimal | None:
    """A money value as Decimal, or None for anything not a clean number.

    Ports the Playwright `parse_money` cleaning (strip `AED`, thousands commas,
    accounting parentheses for negatives) but returns None — "unknown" — where
    that returned 0.0, because a null fee and a zero fee are different claims.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = (
        text.replace("AED", "")
        .replace("aed", "")
        .replace(",", "")
        .replace("(", "-")
        .replace(")", "")
        .strip()
    )
    if cleaned in {"", "-"}:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _abs(value: Decimal | None) -> Decimal | None:
    """`abs`, but None-preserving — an unknown fee stays unknown."""
    return abs(value) if value is not None else None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    """First present, non-null value among `keys` — a field the CSV spells
    `snake_case` and a JSON fallback would spell `camelCase`."""
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) is not None:
            return mapping[key]
    return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    """ISO-8601 datetime string → datetime, or None. Naive strings stay naive."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _entry_type(row: dict[str, Any]) -> str:
    value = _first(row, "entry_type", "entryType", "type")
    return str(value).strip().lower() if value is not None else ""


def _row_date(row: dict[str, Any]) -> date | None:
    return _parse_date(
        _first(row, "date", "entry_date", "entryDate", "createdAt", "statementDate")
    )


def _in_window(day: date | None, since: datetime, until: datetime) -> bool:
    return day is not None and since.date() <= day <= until.date()


def _rows_from_csv(text: str) -> list[dict[str, str]]:
    """CSV → list of stripped dicts. The shape "Export Current View" produces."""
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append(
            {
                key.strip(): (value or "").strip().strip('"')
                for key, value in row.items()
                if key is not None
            }
        )
    return rows


def _rows_from_json(text: str) -> list[dict[str, Any]]:
    """JSON → list of dicts — a defensive fallback for the day an endpoint hands
    back its data as JSON rather than the CSV the browser downloaded."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if isinstance(data, list):
        candidates: Any = data
    elif isinstance(data, dict):
        candidates = None
        for key in ("data", "rows", "entries", "results", "items", "list", "lines"):
            value = data.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if candidates is None and isinstance(data.get("data"), dict):
            inner = data["data"]
            for key in ("rows", "entries", "results", "items", "list", "lines"):
                value = inner.get(key)
                if isinstance(value, list):
                    candidates = value
                    break
        candidates = candidates or []
    else:
        candidates = []
    return [row for row in candidates if isinstance(row, dict)]


def _parse_tabular(text: str) -> list[dict[str, Any]]:
    """The endpoints answer CSV; accept a JSON body too, so a served-shape change
    degrades to a mapping miss (empty rows) rather than a crash."""
    stripped = (text or "").strip()
    if not stripped or stripped == "[]":
        return []
    if stripped[0] in "[{":
        return _rows_from_json(stripped)
    return _rows_from_csv(stripped)


def _truncation_note(rows: list[dict[str, Any]], since: datetime) -> str | None:
    """A note when the wallet's "current view" starts newer than the requested
    window — its cap may have dropped earlier in-window entries."""
    dates = [day for row in rows if (day := _row_date(row))]
    if not dates:
        return None
    oldest = min(dates)
    if oldest > since.date():
        return (
            f"noon wallet export returned entries only back to {oldest.isoformat()}, "
            f"newer than the requested start {since.date().isoformat()}; the wallet "
            "'current view' may be capped, so earlier in-window entries could be missing."
        )
    return None


def _merge_oms_into_rms(oms: StandardOrder, rms: StandardOrder) -> StandardOrder:
    """Merge an OMS order (items/timing) with a matching RMS order (settled fees).

    OMS is the primary source for: items, timestamps, external_outlet_id, and
    the outlet-subtotal money (gross_sales / net_sales as a fallback when RMS
    carries no value).  RMS is authoritative for the settlement layer:
    commission_amount, payment/delivery/vat/cancellation fees, net_payable, and
    statement_id.  The `coalesce(rms, oms)` rule mirrors what the ingest's
    `_PRESERVE_IF_NULL` would do across two separate passes, but collapses them
    into a single upsert so the row is complete on arrival.
    """
    return StandardOrder(
        external_order_id=rms.external_order_id,
        external_outlet_id=rms.external_outlet_id or oms.external_outlet_id,
        business_date=rms.business_date or oms.business_date,
        placed_at=oms.placed_at or rms.placed_at,
        accepted_at=oms.accepted_at or rms.accepted_at,
        delivered_at=oms.delivered_at or rms.delivered_at,
        cancelled_at=oms.cancelled_at or rms.cancelled_at,
        status=rms.status or oms.status,
        currency=rms.currency or oms.currency,
        customer_name=oms.customer_name,
        customer_phone=oms.customer_phone,
        gross_sales=rms.gross_sales if rms.gross_sales is not None else oms.gross_sales,
        net_sales=rms.net_sales if rms.net_sales is not None else oms.net_sales,
        commission_amount=rms.commission_amount,
        payment_fee=rms.payment_fee if rms.payment_fee is not None else oms.payment_fee,
        delivery_fee=rms.delivery_fee
        if rms.delivery_fee is not None
        else oms.delivery_fee,
        vat_amount=rms.vat_amount,
        cancellation_fee=rms.cancellation_fee,
        refund_amount=rms.refund_amount,
        net_payable=rms.net_payable if rms.net_payable is not None else oms.net_payable,
        statement_id=rms.statement_id,
        items=oms.items,
        raw=oms.raw,
    )


class NoonClient(BaseAggregatorClient):
    channel = CHANNEL_NOON
    #: Akamai fingerprints the TLS ClientHello, so a plain Python handshake is
    #: flagged even with perfect cookies — the base sends a Chrome ClientHello.
    uses_tls_impersonation = True

    # ── identity / scoping ──────────────────────────────────────────────────
    def _rms_context(self, session: LoadedSession) -> tuple[str, str, str]:
        """`(restaurant_code, project, locale)` for the RMS headers.

        Read generically — `session.tokens` first, then the verbatim header
        profile the browser sent, then `en-ae` for the locale. Never pinned to
        one outlet: a second restaurant is a different captured session.
        """
        tokens = session.tokens or {}
        profile = session.header_profile or {}
        code = (
            tokens.get("restaurant_code")
            or tokens.get("n-restaurantcode")
            or tokens.get("restaurant_id")
            or profile.get("n-restaurantcode")
        )
        project = (
            tokens.get("project")
            or tokens.get("x-project")
            or tokens.get("project_id")
            or profile.get("x-project")
        )
        locale = (
            tokens.get("locale")
            or tokens.get("x-locale")
            or profile.get("x-locale")
            or _DEFAULT_LOCALE
        )
        if not code or not project:
            # Not transient and not a dead cookie — the capture is incomplete, so
            # only a fresh bootstrap that stashes the identity can fix it.
            raise AggregatorAuthError(
                f"{self.channel} session is missing the restaurant code / project "
                "needed to scope RMS calls — re-bootstrap to capture them"
            )
        return str(code), str(project), str(locale)

    def _rms_headers(self, session: LoadedSession) -> dict[str, str]:
        code, project, locale = self._rms_context(session)
        return {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "n-restaurantcode": code,
            "x-locale": locale,
            "x-platform": "web",
            "x-project": project,
        }

    # ── anti-bot ─────────────────────────────────────────────────────────────
    def _is_auth_failure(self, response: Any) -> bool:
        """A dead cookie *or* an Akamai block. Both need a browser, not a retry."""
        if getattr(response, "status_code", None) in (401, 403):
            return True
        return self._looks_like_akamai_block(response)

    @staticmethod
    def _looks_like_akamai_block(response: Any) -> bool:
        """Akamai Bot Manager can answer 200/406 with an "Access Denied /
        Reference #" page instead of a clean status, so the body is read."""
        body = getattr(response, "text", "") or ""
        if not isinstance(body, str):
            return False
        lowered = body[:2000].lower()
        return any(marker in lowered for marker in _AKAMAI_MARKERS)

    # ── transport: a raw POST whose body is CSV (or JSON) ───────────────────
    async def _post_tabular(
        self, session: LoadedSession, url: str, json_body: Any
    ) -> list[dict[str, Any]]:
        """POST an RMS endpoint and parse its CSV/JSON body into rows.

        `request_json` can't be used — these answer `text/csv` — so the status
        checks `request_json` does are done here by hand, mapping the same two
        signals (`AggregatorAuthError` / `AggregatorUnavailableError`).
        """
        response = await self.request_raw(
            session,
            "POST",
            url,
            headers=self._rms_headers(session),
            json_body=json_body,
        )
        if self._is_auth_failure(response):
            raise AggregatorAuthError(
                f"{self.channel} returned {getattr(response, 'status_code', '?')} "
                "or an Akamai block — session no longer authenticates"
            )
        status = getattr(response, "status_code", 0)
        if status >= 500:
            raise AggregatorUnavailableError(f"{self.channel} returned {status}")
        if status >= 400:
            raise AggregatorUnavailableError(
                f"{self.channel} returned {status}: "
                f"{getattr(response, 'text', '')[:200]}"
            )
        return _parse_tabular(getattr(response, "text", "") or "")

    async def _post_tabular_with_raw(
        self, session: LoadedSession, url: str, json_body: Any
    ) -> tuple[list[dict[str, Any]], bytes]:
        """POST an RMS endpoint and return (parsed rows, raw response bytes).

        Same auth/status validation as `_post_tabular`; also returns the raw
        bytes so callers can archive the settlement document verbatim.
        """
        response = await self.request_raw(
            session,
            "POST",
            url,
            headers=self._rms_headers(session),
            json_body=json_body,
        )
        if self._is_auth_failure(response):
            raise AggregatorAuthError(
                f"{self.channel} returned {getattr(response, 'status_code', '?')} "
                "or an Akamai block — session no longer authenticates"
            )
        status = getattr(response, "status_code", 0)
        if status >= 500:
            raise AggregatorUnavailableError(f"{self.channel} returned {status}")
        if status >= 400:
            raise AggregatorUnavailableError(
                f"{self.channel} returned {status}: "
                f"{getattr(response, 'text', '')[:200]}"
            )
        text = getattr(response, "text", "") or ""
        # Prefer the original bytes (preserves encoding); fall back to UTF-8.
        raw_bytes: bytes = getattr(response, "content", None) or text.encode("utf-8")
        return _parse_tabular(text), raw_bytes

    async def _wallet_rows(
        self, session: LoadedSession, entry_type: str
    ) -> list[dict[str, Any]]:
        """One wallet tab's ledger — the `Export Current View` dataset."""
        return await self._post_tabular(session, _WALLET_URL, {"entryType": entry_type})

    # ── OMS (near-realtime items + modifiers) ───────────────────────────────
    async def _fetch_oms_orders(
        self,
        session: LoadedSession,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[list[StandardOrder], str | None]:
        """Paginate OMS order history and return orders in the since/until window.

        Fetches up to `_OMS_MAX_PAGES` pages of `_OMS_PAGE_SIZE` orders each.
        Orders whose `createdAt` falls outside the window are skipped. Returns
        `(orders, truncation_note)`; the note is non-None when the cap was hit
        and more pages exist.
        """
        orders: list[StandardOrder] = []
        truncation: str | None = None
        total_pages = 1

        for page_no in range(1, _OMS_MAX_PAGES + 1):
            data = await self.request_json(
                session,
                "POST",
                _OMS_HISTORY_URL,
                headers=self._rms_headers(session),
                json_body={"pageNo": page_no, "pageSize": _OMS_PAGE_SIZE},
            )
            data_block = (data or {}).get("data") if isinstance(data, dict) else None
            if not isinstance(data_block, dict):
                break
            total_pages = int(data_block.get("pages") or 1)
            raw_orders = data_block.get("orders") or []
            if not raw_orders:
                break

            for raw_order in raw_orders:
                if not isinstance(raw_order, dict):
                    continue
                placed_at = _parse_datetime(raw_order.get("createdAt"))
                if placed_at is None:
                    continue
                if placed_at.date() < since.date() or placed_at.date() > until.date():
                    continue
                order = self._order_from_oms(raw_order)
                if order:
                    orders.append(order)

            if page_no >= total_pages:
                break

            if page_no >= _OMS_MAX_PAGES:
                truncation = (
                    f"noon OMS history capped at {_OMS_MAX_PAGES} pages "
                    f"({_OMS_MAX_PAGES * _OMS_PAGE_SIZE} orders); "
                    f"{total_pages} total pages exist — orders earlier in the "
                    "window may be missing."
                )
                break

        return orders, truncation

    def _order_from_oms(self, order: dict[str, Any]) -> StandardOrder | None:
        """Parse an OMS JSON order into a StandardOrder with items.

        Provides: identity, timing, outlet, gross_sales/net_sales, and item rows
        with expanded modifier quantities.  Settlement fields (commission, fees,
        vat, statement_id) are left None — RMS fills them in `_merge_oms_into_rms`.
        """
        order_id = _str_or_none(_first(order, "orderNr", "order_nr"))
        if not order_id:
            return None
        placed_at = _parse_datetime(order.get("createdAt"))
        outlet_info = order.get("outletInfo") or {}
        return StandardOrder(
            external_order_id=order_id,
            external_outlet_id=_str_or_none(
                outlet_info.get("outletCode") if isinstance(outlet_info, dict) else None
            ),
            business_date=placed_at.date().isoformat() if placed_at else None,
            placed_at=placed_at,
            accepted_at=_parse_datetime(order.get("estimatedAcceptedAt")),
            delivered_at=_parse_datetime(order.get("estimatedDeliveryAt")),
            cancelled_at=None,
            status=_str_or_none(order.get("orderStatusCode")),
            currency=_str_or_none(order.get("currencyCode")) or "AED",
            customer_name=None,
            customer_phone=None,
            gross_sales=_num(
                order.get("orderOutletSubtotal")
                if order.get("orderOutletSubtotal") is not None
                else order.get("orderSubtotal")
            ),
            net_sales=_num(order.get("orderRestaurantToInvoice")),
            commission_amount=None,
            payment_fee=_abs(_num(order.get("orderPostpaidFee"))),
            delivery_fee=_abs(_num(order.get("orderDeliveryFeeOutlet"))),
            vat_amount=None,
            cancellation_fee=None,
            refund_amount=None,
            net_payable=_num(order.get("orderRestaurantToInvoice")),
            statement_id=None,
            items=self._items_from_oms(order),
            raw=order,
        )

    def _items_from_oms(self, order: dict[str, Any]) -> list[StandardOrderItem]:
        """Extract and normalise OMS line items with expanded modifier quantities."""
        order_nr = str(order.get("orderNr") or "")
        menu_info = order.get("menuInfo") or {}
        if not isinstance(menu_info, dict):
            menu_info = {}

        # Name/category lookups from menuInfo
        item_lookup: dict[str, dict[str, Any]] = {}
        for mi in menu_info.get("items") or []:
            if isinstance(mi, dict) and mi.get("itemCode"):
                item_lookup[str(mi["itemCode"])] = mi

        category_lookup: dict[str, str] = {}
        for cat in menu_info.get("categories") or []:
            if isinstance(cat, dict) and cat.get("categoryCode"):
                category_lookup[str(cat["categoryCode"])] = str(
                    cat.get("nameEn") or cat.get("nameAr") or cat.get("name") or ""
                )

        placed_at = _parse_datetime(order.get("createdAt"))
        result: list[StandardOrderItem] = []
        for index, item in enumerate(order.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            item_code = str(item.get("itemCode") or f"item-{index}")
            menu_item = item_lookup.get(item_code, {})
            category_code = str(menu_item.get("categoryCode") or "")
            raw_mods = item.get("modifiers")
            mods: list[StandardModifier] = (
                expand_modifiers(raw_mods) if raw_mods else []
            )
            result.append(
                StandardOrderItem(
                    source_key=f"{order_nr}:{item_code}:{index}",
                    item_name=str(
                        menu_item.get("name")
                        or menu_item.get("nameEn")
                        or menu_item.get("nameAr")
                        or item_code
                    ),
                    category_name=category_lookup.get(category_code) or None,
                    quantity=_num(item.get("qty")),
                    unit_price=_num(item.get("price")),
                    gross_sales=_num(item.get("totalPrice")),
                    net_sales=_num(item.get("totalPrice")),
                    amount_is_known=True,
                    modifiers=mods,
                    business_date=placed_at.date().isoformat() if placed_at else None,
                )
            )
        return result

    # ── sales (dual-source: OMS items + RMS fees) ───────────────────────────
    async def fetch_sales(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> SalesResult:
        """Orders in the window, items from OMS, settlement fees from RMS.

        Two sources are read and merged by `external_order_id`:

        1. OMS history (`_oms/order/panel/history`): near-realtime per-order JSON
           with item lines and modifier quantities. Capped at
           `_OMS_MAX_PAGES × _OMS_PAGE_SIZE` orders.  If the OMS call fails, a
           truncation note is added and the method falls back to RMS-only so the
           nightly ingest keeps running.

        2. RMS order-level statement (`statement/orders`): settled orders with
           commission / fee / vat figures. Statement publication is weekly so
           `_publication_since` widens discovery.

        OMS-only orders (not yet settled) are returned with fee fields as None.
        RMS-only orders (outside the OMS cap / window) carry no items.
        """
        # ── OMS pull (best-effort; auth/network failures fall back gracefully) ──
        oms_orders: dict[str, StandardOrder] = {}
        oms_truncation: str | None = None
        try:
            raw_oms, oms_truncation = await self._fetch_oms_orders(
                session, since=since, until=until
            )
            for o in raw_oms:
                if o.external_order_id:
                    oms_orders[o.external_order_id] = o
        except (AggregatorAuthError, AggregatorUnavailableError) as exc:
            oms_truncation = (
                f"noon OMS history unavailable ({exc}); order items will be "
                "absent until the next successful OMS pull"
            )
            logger.warning("noon OMS history failed, continuing with RMS-only: %s", exc)

        # ── RMS pull (authoritative for fees; propagates auth failures) ──────
        statement_rows = await self._wallet_rows(session, "statement")
        publish_since = _publication_since(since)
        in_window = [
            row
            for row in statement_rows
            if _entry_type(row) == "statement"
            and _in_window(_row_date(row), publish_since, until)
        ]
        statement_ids = [
            ref
            for row in in_window
            if (ref := _first(row, "reference_nr", "referenceNr", "reference"))
        ]
        rms_truncation = _truncation_note(statement_rows, publish_since)

        rms_orders: dict[str, StandardOrder] = {}
        if statement_ids:
            order_rows = await self._post_tabular(
                session, _ORDER_STATEMENT_URL, {"statementNrList": statement_ids}
            )
            for row in order_rows:
                o = self._order_from(row)
                if o and o.external_order_id:
                    rms_orders[o.external_order_id] = o
        else:
            logger.info(
                "noon sales: 0/%s wallet statements in publication window "
                "%s..%s (ingest since=%s)",
                len(statement_rows),
                publish_since.date().isoformat(),
                until.date().isoformat(),
                since.date().isoformat(),
            )

        # ── merge ──────────────────────────────────────────────────────────────
        merged: list[StandardOrder] = []
        for order_id, oms_order in oms_orders.items():
            rms_order = rms_orders.get(order_id)
            merged.append(
                _merge_oms_into_rms(oms_order, rms_order)
                if rms_order is not None
                else oms_order
            )
        for order_id, rms_order in rms_orders.items():
            if order_id not in oms_orders:
                merged.append(rms_order)

        notes = [n for n in (oms_truncation, rms_truncation) if n]
        if not merged and not notes:
            notes.append(
                "No noon orders found: the OMS window is empty and no "
                "statements were published in the requested window."
            )
        return SalesResult(
            orders=merged,
            truncation_note=" | ".join(notes) if notes else None,
        )

    def _order_from(self, row: dict[str, Any]) -> StandardOrder | None:
        order_id = _first(row, "order_nr", "orderNr", "order_id")
        if not order_id:
            return None
        order_date = _parse_date(
            _first(row, "order_date", "orderDate", "business_date")
        )
        status = _first(row, "order_status", "orderStatus")
        return StandardOrder(
            external_order_id=str(order_id),
            external_outlet_id=_str_or_none(_first(row, "outlet_code", "outletCode")),
            business_date=order_date.isoformat() if order_date else None,
            placed_at=(
                datetime.combine(order_date, datetime.min.time())
                if order_date
                else None
            ),
            status=(
                str(status).strip().lower().replace(" ", "_") or None
                if status is not None
                else None
            ),
            currency=_first(row, "currency", "currencyCode") or "AED",
            gross_sales=_num(_first(row, "order_value", "item_value", "orderValue")),
            net_sales=_num(_first(row, "rest_invoice", "order_value", "restInvoice")),
            commission_amount=self._commission_from(row),
            payment_fee=_abs(_num(_first(row, "payment_fee", "paymentFee"))),
            delivery_fee=_abs(_num(_first(row, "delivery_fee", "deliveryFee"))),
            vat_amount=_abs(_num(_first(row, "total_vat", "totalVat"))),
            cancellation_fee=_abs(
                _num(_first(row, "cancellation_fee", "cancellationFee"))
            ),
            net_payable=_num(_first(row, "net_payable", "netPayable")),
            statement_id=_str_or_none(_first(row, "statement_nr", "statementNr")),
            # RMS has no per-item lines; OMS items are merged in by fetch_sales.
            items=[],
            raw=row,
        )

    @staticmethod
    def _commission_from(row: dict[str, Any]) -> Decimal | None:
        """The real commission, backed out of the statement the way the
        Playwright normaliser did: `fees_exc_vat` less the itemised non-
        commission fees, floored at zero. None when `fees_exc_vat` is absent —
        an unknown cut, not a zero one.
        """
        fees_exc_vat = _num(_first(row, "fees_exc_vat", "feesExcVat"))
        if fees_exc_vat is None:
            return None
        other = Decimal(0)
        for snake, camel in (
            ("payment_fee", "paymentFee"),
            ("delivery_fee", "deliveryFee"),
            ("cancellation_fee", "cancellationFee"),
            ("lead_generation_fee", "leadGenerationFee"),
            ("discount_service_fee", "discountServiceFee"),
            ("long_distance_fee_mp", "longDistanceFeeMp"),
            ("delivery_discount_fee", "deliveryDiscountFee"),
        ):
            fee = _abs(_num(_first(row, snake, camel)))
            if fee is not None:
                other += fee
        value = abs(fees_exc_vat) - other
        return value if value > 0 else Decimal(0)

    # ── finance (statements + payouts as distinct wallet tabs) ──────────────
    async def fetch_statements(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> StatementsResult:
        """Wallet Statement tab — settlement summaries (invoice archival deferred until PDF discovery)."""
        statement_rows = await self._wallet_rows(session, "statement")
        publish_since = _publication_since(since)
        statements: list[StandardStatement] = []
        for row in statement_rows:
            if _entry_type(row) != "statement":
                continue
            if not _in_window(_row_date(row), publish_since, until):
                continue
            statement = self._statement_from(row)
            if statement is not None:
                statements.append(statement)

        return StatementsResult(
            statements=statements,
            truncation_note=_truncation_note(statement_rows, publish_since),
        )

    async def fetch_payouts(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> PayoutsResult:
        """Wallet Payment tab — transfers noon has made (not statements)."""
        payment_rows = await self._wallet_rows(session, "payment")
        publish_since = _publication_since(since)
        payouts = [
            payout
            for row in payment_rows
            if _entry_type(row) == "payment"
            and _in_window(_row_date(row), publish_since, until)
            and (payout := self._payout_from(row)) is not None
        ]
        return PayoutsResult(
            payouts=payouts,
            truncation_note=_truncation_note(payment_rows, publish_since),
        )

    @staticmethod
    def _payout_from(row: dict[str, Any]) -> StandardPayout | None:
        day = _row_date(row)
        ref = _first(row, "reference_nr", "referenceNr", "reference")
        transfer_id = (
            str(ref) if ref else (f"payment-{day.isoformat()}" if day else None)
        )
        if transfer_id is None:
            return None
        return StandardPayout(
            transfer_id=transfer_id,
            transfer_date=day.isoformat() if day else None,
            payment_due_date=day.isoformat() if day else None,
            transfer_amount=_abs(_num(_first(row, "amount", "value"))),
            transfer_status="paid",
            payment_reference=_first(row, "invoice_nr", "invoiceNr") or transfer_id,
            currency=_first(row, "currency", "currencyCode") or "AED",
        )

    @staticmethod
    def _statement_from(row: dict[str, Any]) -> StandardStatement | None:
        day = _row_date(row)
        statement_id = (
            _first(row, "reference_nr", "referenceNr")
            or _first(row, "invoice_nr", "invoiceNr")
            or (day.isoformat() if day else None)
        )
        if statement_id is None:
            return None
        return StandardStatement(
            statement_id=str(statement_id),
            period_end=day.isoformat() if day else None,
            payment_due_date=day.isoformat() if day else None,
            net_payable=_num(_first(row, "amount", "value")),
            currency=_first(row, "currency", "currencyCode") or "AED",
            raw=row,
        )


#: The module-level singleton, matching the other providers — stateless (the
#: session is passed in per call), so sharing it is free.
provider = NoonClient()
