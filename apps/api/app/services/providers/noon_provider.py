"""noon Food, as its restaurant console (RMS) answers over httpx.

Ported from the Playwright exporter the bootstrap used to drive by hand. The
browser is only there to solve the login (email OTP + a passkey nag) and to run
Akamai's sensor; once a session is captured, every read here is a plain request
the console's own SPA makes — so the hourly path never opens a browser.

**Where the reads come from.** noon's RMS lives at `restaurant.noon.partners`.
Two surfaces matter:

- The *order-level statement*: `POST /_food-restaurant/finance/statement/orders`
  with `{"statementNrList": [...]}`. It answers a CSV of every settled order on
  those statements — the sales truth, with the real fees noon charged. You have
  to know which statements to ask for, which is what the wallet gives you.
- The *wallet* tabs: `POST /_food-restaurant/finance/wallet` with
  `{"entryType": "statement" | "payment"}` — the settlement ledger the console's
  "Export Current View" button serialises to CSV. The Statement tab lists the
  published statements (and their reference numbers, which feed the order-level
  call); the Payment tab lists the transfers noon actually made.

**Identity (`n-restaurantcode` / `x-project` / `x-locale`).** The RMS calls are
scoped to one restaurant and one project by three request headers. The
Playwright port read them from local secrets, hardcoded to one outlet; here they
are read generically off the captured session — `session.tokens` first (a
bootstrap that knows to stash them puts them under `restaurant_code` / `project`
/ `locale`), then whatever the browser sent verbatim in `session.header_profile`
(`n-restaurantcode` / `x-project` / `x-locale`), then `en-ae` for the locale.
Nothing is pinned to a single branch, so a second outlet is a different captured
session, not a code change. A session that carries neither the token nor the
header can't be scoped, so it is treated as needing a fresh bootstrap.

**Anti-bot.** noon sits behind Akamai Bot Manager (the `bm_sv` cookie), which
fingerprints the TLS ClientHello — so `uses_tls_impersonation` is set and, where
`curl_cffi` is installed, the base sends a real Chrome handshake. Akamai's block
is not always a 401/403: it can answer 200 (or 406) with an "Access Denied /
Reference #" page, so `_is_auth_failure` is widened to read the body for that,
and a block is routed as an auth failure (only a browser can re-arm the sensor),
never as a transient outage that would be retried into a lockout.

**Item-detail limitation.** The RMS order-level statement is per *order*, not per
*line*: it carries no item breakdown at all. So `fetch_sales` emits orders with
an empty `items` list — no `StandardOrderItem` rows. Were a companion source
(the capped OMS history feed) ever wired in, its items would be a period
aggregate, so the grain is documented as `GRAIN_AGGREGATE`; the money we do have
is order-level and known, and none of it is faked to fill the item gap.

Money is defensive throughout: `Decimal | None`, where None means "noon did not
say", never zero. `commission_amount` is derived from the statement's
`fees_exc_vat` minus the itemised fees (matching the Playwright normaliser) and
is None when `fees_exc_vat` itself is absent. Every record keeps its `raw`.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.aggregator import CHANNEL_NOON, GRAIN_AGGREGATE
from app.services.aggregators.normalized import (
    FinanceResult,
    SalesResult,
    StandardOrder,
    StandardPayout,
    StandardStatement,
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

#: The grain the item rows would carry *if* a companion source ever supplied
#: them — the RMS order-level statement carries none, so `fetch_sales` emits no
#: item rows at all (see the module docstring). Referenced so the intended grain
#: is stated in code, not only in prose.
_ITEM_GRAIN = GRAIN_AGGREGATE

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
        for key in ("data", "rows", "entries", "results", "items", "list"):
            value = data.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if candidates is None and isinstance(data.get("data"), dict):
            inner = data["data"]
            for key in ("rows", "entries", "results", "items", "list"):
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

    async def _wallet_rows(
        self, session: LoadedSession, entry_type: str
    ) -> list[dict[str, Any]]:
        """One wallet tab's ledger — the `Export Current View` dataset."""
        return await self._post_tabular(session, _WALLET_URL, {"entryType": entry_type})

    # ── sales ───────────────────────────────────────────────────────────────
    async def fetch_sales(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> SalesResult:
        """Orders settled on statements published in the window.

        noon's sales truth is the RMS order-level statement, so we first read the
        wallet Statement tab for the reference numbers in range, then ask the
        order-level endpoint for exactly those. Orders carry no item rows — the
        statement has no per-line detail (see the module docstring).
        """
        statement_rows = await self._wallet_rows(session, "statement")
        in_window = [
            row
            for row in statement_rows
            if _entry_type(row) == "statement"
            and _in_window(_row_date(row), since, until)
        ]
        statement_ids = [
            ref
            for row in in_window
            if (ref := _first(row, "reference_nr", "referenceNr", "reference"))
        ]
        truncation = _truncation_note(statement_rows, since)
        if not statement_ids:
            return SalesResult(
                orders=[],
                truncation_note=truncation
                or "No noon statements were published in the requested window.",
            )
        order_rows = await self._post_tabular(
            session, _ORDER_STATEMENT_URL, {"statementNrList": statement_ids}
        )
        orders = [
            order for row in order_rows if (order := self._order_from(row)) is not None
        ]
        return SalesResult(orders=orders, truncation_note=truncation)

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
            # The RMS order-level statement carries no per-item lines; see docstring.
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

    # ── finance (statements + payouts) ──────────────────────────────────────
    async def fetch_finance(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> FinanceResult:
        """The wallet's published statements and the transfers noon has made.

        Both come from the wallet tabs' `Export Current View` — the Statement tab
        for settlement summaries, the Payment tab for transfers.
        """
        payment_rows = await self._wallet_rows(session, "payment")
        statement_rows = await self._wallet_rows(session, "statement")
        payouts = [
            payout
            for row in payment_rows
            if _entry_type(row) == "payment"
            and _in_window(_row_date(row), since, until)
            and (payout := self._payout_from(row)) is not None
        ]
        statements = [
            statement
            for row in statement_rows
            if _entry_type(row) == "statement"
            and _in_window(_row_date(row), since, until)
            and (statement := self._statement_from(row)) is not None
        ]
        truncation = _truncation_note(payment_rows + statement_rows, since)
        return FinanceResult(
            statements=statements, payouts=payouts, truncation_note=truncation
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
