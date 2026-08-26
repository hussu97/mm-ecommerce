"""Talabat, as its partner portal's two GraphQLs answer.

The hardest of the five, on two counts. First, it is two APIs, not one: orders
live behind a supergraph, finance behind a separate gateway, and the two speak
different request envelopes.

- Sales: `POST https://vm-supergraph.eu.prd.portal.restaurant/graphql`. There is
  no "give me the orders" query — Talabat only exports orders as an async CSV
  job. So a pull is three GraphQL calls (`EstimateExportSize` → `RequestExport`
  → poll `ListExports` until the export completes and carries a `downloadUrl`),
  then an authenticated GET of that CSV, which is parsed into orders and their
  line items. This mirrors the browser's Report Builder flow exactly; the only
  thing the browser did that this cannot is *discover* the store ids by scraping
  the page, so those are read from the session (see `_order_account_ids`).
- Finance: `POST https://vagw-api.eu.prd.portal.restaurant/query`. Payouts
  (`ListPayouts`) and settlement metadata (`ListAdditionalStatements`) are real
  paginated queries, so finance is a straight map. The `accounts` descriptors
  these queries key on were captured off the network by the bootstrap and are
  read back from the session (see `_finance_accounts`).

Second, the bot wall. Talabat fronts everything with PerimeterX, which
fingerprints the TLS ClientHello, so `uses_tls_impersonation` is set and, where
`curl_cffi` is installed, the base sends a Chrome ClientHello. PerimeterX also
answers a flagged request not only with a 403 but sometimes a 200 whose body is
a "press & hold" / captcha challenge, so `_is_auth_failure` is overridden to
read the body as well as the status — a challenge is a dead session, not data.

Auth is a bearer `accessToken`. In the browser it is read from the `accessToken`
cookie first and localStorage second; here it is read from the session's cookies
first and its tokens second (`_access_token`), and sent as `Authorization:
Bearer …` on every call plus an `x-global-entity-id: tb_ae` header. Entity is
`TB_AE` (Talabat UAE).

Money is taken verbatim and left null where a column is absent (None is
"unknown", not zero — a blank commission cell is not a free order), every order
keeps its `raw`, and a window the portal could not export in full sets a
`truncation_note` rather than implying it covered everything.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.aggregator import CHANNEL_TALABAT, GRAIN_LINE
from app.services.aggregators.normalized import (
    FinanceResult,
    SalesResult,
    StandardOrder,
    StandardOrderItem,
    StandardPayout,
    StandardStatement,
)
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.aggregator_base import (
    AggregatorUnavailableError,
    BaseAggregatorClient,
)

logger = logging.getLogger(__name__)

# ── Endpoints and entity ──────────────────────────────────────────────────────
_ORDERS_GRAPHQL = "https://vm-supergraph.eu.prd.portal.restaurant/graphql"
_FINANCE_GRAPHQL = "https://vagw-api.eu.prd.portal.restaurant/query"
_GLOBAL_ENTITY_ID = "TB_AE"

# ── Report Builder export constants (orders) ──────────────────────────────────
_EXPORT_TYPE_ORDERS = "EXPORT_TYPE_ORDERS_LIST"
_EXPORT_ACCOUNT_TYPE_VENDOR = "EXPORT_ACCOUNT_TYPE_VENDOR"
_DELIVERY_METHOD_DIRECT = "DELIVERY_METHOD_DIRECT"
_EXPORT_FORMAT_CSV = "CSV"
_EXPORT_STATUS_COMPLETED = "EXPORT_STATUS_COMPLETED"
_EXPORT_STATUS_TERMINAL_BAD = {
    "EXPORT_STATUS_FAILED",
    "EXPORT_STATUS_CANCELLED",
    "EXPORT_STATUS_EXPIRED",
}
_EXPORT_POLL_TIMEOUT_SECONDS = 180
_EXPORT_POLL_INTERVAL_SECONDS = 5

# ── Finance pagination ────────────────────────────────────────────────────────
_FINANCE_PAGE_SIZE = 100
#: Hard ceiling so a stable `nextPageToken` (an API bug, or a session that has
#: quietly expired into a redirect) cannot spin the loop forever against a live
#: portal — ported verbatim from the Playwright scraper's guard.
_MAX_FINANCE_PAGES = 500

# ── GraphQL documents (ported verbatim from the Playwright exporter) ───────────
_ESTIMATE_EXPORT_SIZE_QUERY = """
query EstimateExportSize($input: EstimateExportSizeReq!) {
  estimateExportSize(input: $input) {
    withinLimits
    estimatedFileCount
    estimatedRowCount
    __typename
  }
}
""".strip()

_REQUEST_EXPORT_MUTATION = """
mutation RequestExport($input: RequestExportReq!) {
  requestExport(input: $input) {
    exportId
    status
    __typename
  }
}
""".strip()

_LIST_EXPORTS_QUERY = """
query ListExports($input: ListExportsReq!) {
  listExports(input: $input) {
    exports {
      exportId
      name
      exportType
      status
      requestedAt
      startedAt
      expiresAt
      downloadUrl
      columns {
        paths
        __typename
      }
      filters {
        from
        to
        accountIds
        accountType
        __typename
      }
      __typename
    }
    nextPageToken
    prevPageToken
    __typename
  }
}
""".strip()

_LIST_PAYOUTS_QUERY = """
query ListPayouts($params: ListPayoutsRequest!) {
  finances {
    listPayouts(input: $params) {
      nextPageToken
      prevPageToken
      payouts {
        payoutId: id
        payoutAmount: netPayout
        payoutCurrency: currency
        payoutOrders: ordersCount
        at: paymentDateLocal
        status: payoutStatus
        payoutAttachments: attachments
        payoutAccount: account {
          grid
          billingParentId
          chainId
          __typename
        }
        invoices {
          invoiceId: id
          invoiceAmount: totalPayout
          invoiceCurrency: currency
          invoiceOrders: ordersCount
          processedDate
          invoiceAttachments: attachments
          period: earningsPeriod {
            from: invoiceStartDate
            to: invoiceEndDate
            __typename
          }
          invoiceAccount: account {
            grid
            billingParentId
            chainId
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
""".strip()

_LIST_ADDITIONAL_STATEMENTS_QUERY = """
query ListAdditionalStatements($params: ListAdditionalStatementsRequest!) {
  finances {
    listAdditionalStatements(input: $params) {
      nextPageToken
      prevPageToken
      additionalStatements {
        statementId
        statementType
        amountGross
        currency
        statementDate
        globalEntityId
        attachments {
          path
          type
          __typename
        }
        account {
          grid
          billingParentId
          chainId
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
""".strip()


# ── value helpers ─────────────────────────────────────────────────────────────
def _money(value: Any) -> Decimal | None:
    """A Talabat money cell as Decimal, or None for a blank/absent/bad value.

    Ported from the exporter's `parse_money` cleaning (strip `AED`, thousands
    commas, nbsp, and parenthesised negatives) but returning None rather than
    0.0 where the source was empty — None is "the export did not say", which the
    normalized layer keeps distinct from a real zero.
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
        .replace(",", "")
        .replace("\xa0", "")
        .replace("(", "-")
        .replace(")", "")
        .strip()
    )
    if cleaned in {"", "-"}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    """The first present, non-null value among `keys` — for a field the two
    APIs (or the CSV's two header spellings) name more than one way."""
    for key in keys:
        if isinstance(row, dict) and row.get(key) is not None:
            return row[key]
    return None


def _parse_dt(value: Any) -> datetime | None:
    """The CSV's `Order received at` (`YYYY-MM-DD HH:MM[:SS]`) as a datetime."""
    if not value or not isinstance(value, str):
        return None
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    """A finance date (`Aug 26, 2026` / `2026-08-26` / `26/08/2026`) as a date."""
    if not value or not isinstance(value, str):
        return None
    for pattern in ("%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    return None


def _date_str(value: Any) -> str | None:
    """A finance date normalised to `YYYY-MM-DD`, or None."""
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else None


def _parse_items_text(value: str | None) -> list[tuple[Decimal, str]]:
    """The CSV's free-text `Order Items` field split into (qty, name) lines.

    A cell is a delimiter-joined list of `"<qty> <name>"` tokens; a token that
    does not start with a number is taken as quantity 1. Ported from the
    exporter's `_parse_talabat_order_items_text`.
    """
    if not value:
        return []
    tokens = [tok.strip() for tok in re.split(r"[\n,;]+", value) if tok.strip()]
    parsed: list[tuple[Decimal, str]] = []
    for token in tokens:
        match = re.match(r"^(?P<qty>\d+(?:\.\d+)?)\s+(?P<name>.+)$", token)
        if match:
            parsed.append((Decimal(match.group("qty")), match.group("name").strip()))
        else:
            parsed.append((Decimal(1), token))
    return parsed


class TalabatClient(BaseAggregatorClient):
    channel = CHANNEL_TALABAT
    uses_tls_impersonation = True
    impersonate_target = "chrome"

    # ── PerimeterX-aware auth-failure detection ───────────────────────────────
    def _is_auth_failure(self, response: Any) -> bool:
        """A dead session — a 401/403, or a PerimeterX bot challenge.

        PerimeterX flags a bad TLS/cookie fingerprint with a 403, but also
        sometimes with a 200 whose body is the "press & hold" / captcha
        interstitial rather than the expected JSON. Either way the session can
        only be revived by a browser bootstrap, so both map to an auth failure
        and never to a retry.
        """
        if getattr(response, "status_code", None) in (401, 403):
            return True
        try:
            body = (getattr(response, "text", "") or "")[:4000].lower()
        except Exception:  # noqa: BLE001 - an unreadable body is not a challenge
            return False
        markers = (
            "px-captcha",
            "perimeterx",
            "_pxhd",
            "press & hold",
            "press and hold",
            "access to this page has been denied",
            "please enable js",
            "human verification",
        )
        return any(marker in body for marker in markers)

    # ── session-sourced credentials ───────────────────────────────────────────
    @staticmethod
    def _access_token(session: LoadedSession) -> str | None:
        """The bearer `accessToken`, from the session's cookies then tokens.

        The browser read it from the `accessToken` cookie first and the
        `authentication` localStorage blob second; the bootstrap captures both
        into the session, so this reads `cookies["accessToken"]` first, then the
        common token keys.
        """
        cookie_token = (session.cookies or {}).get("accessToken")
        if isinstance(cookie_token, str) and cookie_token.strip():
            return cookie_token.strip()
        tokens = session.tokens or {}
        for key in ("accessToken", "access_token", "bearer", "token"):
            value = tokens.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _finance_accounts(session: LoadedSession) -> list[dict[str, Any]]:
        """The `accounts` descriptors the finance queries key on.

        The browser captured these off the finance page's own network traffic;
        the bootstrap stores them under one of these token keys. Each is a
        `{grid, billingParentId, chainId}` dict the gateway echoes back.
        """
        tokens = session.tokens or {}
        for key in ("finance_accounts", "accounts", "financeAccounts"):
            value = tokens.get(key)
            if isinstance(value, list) and value:
                return [a for a in value if isinstance(a, dict)]
        return []

    def _order_account_ids(self, session: LoadedSession) -> list[str]:
        """The store ids the order export scopes to.

        The browser scraped these from the Report Builder store picker; here
        they come from the session's tokens (`account_ids`/`store_ids`), falling
        back to the `grid`s of the finance accounts, which are the same numeric
        store ids.
        """
        tokens = session.tokens or {}
        for key in ("account_ids", "store_ids", "accountIds"):
            value = tokens.get(key)
            if isinstance(value, list) and value:
                return [str(v) for v in value if v is not None]
        grids = [
            str(a["grid"])
            for a in self._finance_accounts(session)
            if a.get("grid") is not None
        ]
        return grids

    # ── GraphQL transport ─────────────────────────────────────────────────────
    async def _graphql(
        self,
        session: LoadedSession,
        *,
        endpoint: str,
        query: str,
        variables: dict[str, Any],
        operation_name: str,
    ) -> dict[str, Any]:
        """One GraphQL POST, returning its `data` object.

        Adds the bearer and the entity header, and turns a GraphQL-level
        `errors` array into `AggregatorUnavailableError` — the base already maps
        transport/auth failures, so only the application-level error is left to
        raise here.
        """
        headers = {
            "content-type": "application/json",
            "x-global-entity-id": _GLOBAL_ENTITY_ID.lower(),
        }
        token = self._access_token(session)
        if token:
            headers["authorization"] = f"Bearer {token}"
        payload = await self.request_json(
            session,
            "POST",
            endpoint,
            headers=headers,
            json_body={
                "query": query,
                "variables": variables,
                "operationName": operation_name,
            },
        )
        if isinstance(payload, dict) and payload.get("errors"):
            raise AggregatorUnavailableError(
                f"{self.channel} GraphQL {operation_name} returned errors: "
                f"{payload['errors']}"
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise AggregatorUnavailableError(
                f"{self.channel} GraphQL {operation_name} returned no data object"
            )
        return data

    # ── sales (Report Builder async export → CSV) ─────────────────────────────
    async def fetch_sales(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> SalesResult:
        account_ids = self._order_account_ids(session)
        if not account_ids:
            raise AggregatorUnavailableError(
                f"{self.channel} session carries no order store ids "
                "(tokens.account_ids / finance accounts) — cannot scope an export"
            )
        from_iso = since.date().isoformat()
        to_iso = until.date().isoformat()
        filters = {
            "from": from_iso,
            "to": to_iso,
            "accountType": _EXPORT_ACCOUNT_TYPE_VENDOR,
            "accountIds": account_ids,
        }

        truncation_note: str | None = None
        estimate = await self._graphql(
            session,
            endpoint=_ORDERS_GRAPHQL,
            query=_ESTIMATE_EXPORT_SIZE_QUERY,
            variables={
                "input": {
                    "globalEntityId": _GLOBAL_ENTITY_ID,
                    "exportType": _EXPORT_TYPE_ORDERS,
                    "filters": filters,
                }
            },
            operation_name="EstimateExportSize",
        )
        size = estimate.get("estimateExportSize")
        if isinstance(size, dict) and size.get("withinLimits") is False:
            truncation_note = (
                "Talabat estimated the order export exceeds the portal's file "
                f"limits ({size}); the export may be capped short of the window."
            )

        requested = await self._graphql(
            session,
            endpoint=_ORDERS_GRAPHQL,
            query=_REQUEST_EXPORT_MUTATION,
            variables={
                "input": {
                    "name": f"sales-{from_iso}-{to_iso}",
                    "globalEntityId": _GLOBAL_ENTITY_ID,
                    "exportType": _EXPORT_TYPE_ORDERS,
                    "filters": filters,
                    "format": _EXPORT_FORMAT_CSV,
                    "deliveryMethod": _DELIVERY_METHOD_DIRECT,
                    "locale": "en",
                }
            },
            operation_name="RequestExport",
        )
        request_export = requested.get("requestExport")
        if not isinstance(request_export, dict) or not request_export.get("exportId"):
            raise AggregatorUnavailableError(
                f"{self.channel} RequestExport returned no export id: {requested}"
            )
        export_id = str(request_export["exportId"])

        ready = await self._poll_export_ready(session, export_id=export_id)
        download_url = str(ready.get("downloadUrl") or "")
        if not download_url:
            raise AggregatorUnavailableError(
                f"{self.channel} export {export_id} completed without a download URL"
            )
        csv_text = await self._download_csv(session, download_url)
        orders = self._orders_from_csv(csv_text)
        return SalesResult(orders=orders, truncation_note=truncation_note)

    async def _poll_export_ready(
        self, session: LoadedSession, *, export_id: str
    ) -> dict[str, Any]:
        """Poll `ListExports` until our export is completed (or terminally bad)."""
        deadline = asyncio.get_event_loop().time() + _EXPORT_POLL_TIMEOUT_SECONDS
        last: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            data = await self._graphql(
                session,
                endpoint=_ORDERS_GRAPHQL,
                query=_LIST_EXPORTS_QUERY,
                variables={
                    "input": {
                        "globalEntityId": _GLOBAL_ENTITY_ID,
                        "pagination": {"pageSize": 20, "sortOrder": "SORT_ORDER_DESC"},
                        "filter": {"exportType": []},
                    }
                },
                operation_name="ListExports",
            )
            exports = (data.get("listExports") or {}).get("exports") or []
            for export in exports:
                if not isinstance(export, dict) or export.get("exportId") != export_id:
                    continue
                last = export
                status = str(export.get("status") or "")
                if status == _EXPORT_STATUS_COMPLETED and export.get("downloadUrl"):
                    return export
                if status in _EXPORT_STATUS_TERMINAL_BAD:
                    raise AggregatorUnavailableError(
                        f"{self.channel} export {export_id} ended as {status}"
                    )
            await asyncio.sleep(_EXPORT_POLL_INTERVAL_SECONDS)
        raise AggregatorUnavailableError(
            f"{self.channel} export {export_id} did not finish in "
            f"{_EXPORT_POLL_TIMEOUT_SECONDS}s (last seen: {last})"
        )

    async def _download_csv(self, session: LoadedSession, download_url: str) -> str:
        """GET the completed export's CSV, replaying the bearer as the browser did."""
        token = self._access_token(session)
        headers = {"authorization": f"Bearer {token}"} if token else None
        response = await self.request_raw(session, "GET", download_url, headers=headers)
        if self._is_auth_failure(response):
            raise AggregatorUnavailableError(
                f"{self.channel} report download was challenged/blocked"
            )
        status = getattr(response, "status_code", 0)
        if status >= 400:
            raise AggregatorUnavailableError(
                f"{self.channel} report download returned HTTP {status}"
            )
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray)):
            return bytes(content).decode("utf-8-sig", errors="replace")
        return str(getattr(response, "text", "") or "")

    def _orders_from_csv(self, csv_text: str) -> list[StandardOrder]:
        """Every order row in the export CSV, with its parsed line items.

        Columns and money mapping are ported from the exporter's
        `parse_talabat_order_details_csv` / `parse_talabat_order_items_csv`. The
        outlet is left as Talabat's own `Store ID` (the ingest resolves it to a
        branch via `aggregator_branch_map`), and — unlike the scraper, which
        dropped everything but `delivered` — all statuses are kept, verbatim, so
        cancellations remain in the sales truth for reconciliation.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        orders: list[StandardOrder] = []
        for row in reader:
            external = (row.get("Order ID") or "").strip()
            if not external:
                continue
            placed_at = _parse_dt(row.get("Order received at"))
            status = (row.get("Order status") or "").strip() or None
            subtotal = _money(row.get("Subtotal"))
            orders.append(
                StandardOrder(
                    external_order_id=external,
                    external_outlet_id=(row.get("Store ID") or "").strip() or None,
                    business_date=placed_at.date().isoformat() if placed_at else None,
                    placed_at=placed_at,
                    status=status,
                    currency="AED",
                    gross_sales=subtotal,
                    net_sales=subtotal,
                    commission_amount=_money(row.get("Commission")),
                    payment_fee=_money(row.get("Online Payment Fee")),
                    # Talabat direct delivery: the vendor CSV carries no delivery
                    # fee column at all, so it stays unknown (None) rather than 0.
                    delivery_fee=None,
                    vat_amount=_money(_first(row, "Tax Amount", "Tax Charge")),
                    cancellation_fee=_money(row.get("Avoidable cancellation fee")),
                    net_payable=_money(
                        _first(row, "Payout Amount", "Estimated earnings")
                    ),
                    items=self._items_from_row(row, external, subtotal),
                    raw=dict(row),
                )
            )
        return orders

    @staticmethod
    def _items_from_row(
        row: dict[str, Any], external_order_id: str, subtotal: Decimal | None
    ) -> list[StandardOrderItem]:
        """Line items parsed out of the CSV's free-text `Order Items` cell.

        The export gives per-line money only implicitly: when a row has exactly
        one item, that item's amount is the order subtotal; with several items
        the per-line split is unknown, so `amount_is_known` is False and the
        money is left null rather than guessed.
        """
        parsed = _parse_items_text(row.get("Order Items"))
        known = len(parsed) == 1
        items: list[StandardOrderItem] = []
        for index, (quantity, name) in enumerate(parsed, start=1):
            unit_price: Decimal | None = None
            if known and subtotal is not None and quantity:
                unit_price = subtotal / quantity
            items.append(
                StandardOrderItem(
                    source_key=f"{external_order_id}:{index}",
                    grain=GRAIN_LINE,
                    item_name=name,
                    quantity=quantity,
                    unit_price=unit_price,
                    gross_sales=subtotal if known else None,
                    net_sales=subtotal if known else None,
                    amount_is_known=known,
                )
            )
        return items

    # ── finance (payouts + additional statements) ─────────────────────────────
    async def fetch_finance(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> FinanceResult:
        accounts = self._finance_accounts(session)
        if not accounts:
            raise AggregatorUnavailableError(
                f"{self.channel} session carries no finance accounts "
                "(tokens.finance_accounts) — cannot query payouts/statements"
            )
        from_date = since.date()
        to_date = until.date()

        payout_rows = await self._paginate_finance(
            session,
            accounts=accounts,
            from_date=from_date,
            to_date=to_date,
            query=_LIST_PAYOUTS_QUERY,
            operation_name="ListPayouts",
            root_key="listPayouts",
            item_key="payouts",
            date_keys=("startDate", "endDate"),
        )
        statement_rows = await self._paginate_finance(
            session,
            accounts=accounts,
            from_date=from_date,
            to_date=to_date,
            query=_LIST_ADDITIONAL_STATEMENTS_QUERY,
            operation_name="ListAdditionalStatements",
            root_key="listAdditionalStatements",
            item_key="additionalStatements",
            date_keys=("statementDateFrom", "statementDateTo"),
        )

        payouts = [self._payout_from(r) for r in payout_rows]
        payouts = [p for p in payouts if p is not None]
        statements = [self._statement_from(r) for r in statement_rows]
        statements = [s for s in statements if s is not None]
        return FinanceResult(statements=statements, payouts=payouts)

    async def _paginate_finance(
        self,
        session: LoadedSession,
        *,
        accounts: list[dict[str, Any]],
        from_date: date,
        to_date: date,
        query: str,
        operation_name: str,
        root_key: str,
        item_key: str,
        date_keys: tuple[str, str],
    ) -> list[dict[str, Any]]:
        """Walk a finance listing to exhaustion, deduping and token-loop-guarded.

        Ported from `_talabat_paginate_finance_rows`: it stops on an absent
        `nextPageToken`, on a token it has already followed, and on the hard page
        ceiling — any one of which prevents an infinite live-portal loop.
        """
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_tokens: set[str] = set()
        page_token: str | None = None
        for _ in range(_MAX_FINANCE_PAGES):
            pagination: dict[str, Any] = {"pageSize": _FINANCE_PAGE_SIZE}
            if page_token:
                pagination["pageToken"] = page_token
            params = {
                "globalEntityId": _GLOBAL_ENTITY_ID,
                "accounts": accounts,
                date_keys[0]: from_date.isoformat(),
                date_keys[1]: to_date.isoformat(),
                "filter": {},
                "pagination": pagination,
            }
            data = await self._graphql(
                session,
                endpoint=_FINANCE_GRAPHQL,
                query=query,
                variables={"params": params},
                operation_name=operation_name,
            )
            listing = (data.get("finances") or {}).get(root_key)
            if not isinstance(listing, dict):
                break
            for item in listing.get(item_key) or []:
                if not isinstance(item, dict):
                    continue
                row_id = str(_first(item, "payoutId", "statementId", "id") or id(item))
                if row_id not in seen_ids:
                    seen_ids.add(row_id)
                    rows.append(item)
            page_token = str(listing.get("nextPageToken") or "").strip() or None
            if not page_token or page_token in seen_tokens:
                break
            seen_tokens.add(page_token)
        return rows

    @staticmethod
    def _payout_from(row: dict[str, Any]) -> StandardPayout | None:
        """A `ListPayouts` row mapped to a payout — port of
        `normalize_talabat_graphql_payout_rows`."""
        payout_id = str(_first(row, "payoutId", "id") or "").strip()
        transfer_date = _date_str(_first(row, "at", "paymentDateLocal"))
        if not payout_id or not transfer_date:
            return None
        payment_reference = payout_id
        invoices = row.get("invoices")
        if isinstance(invoices, list) and invoices and isinstance(invoices[0], dict):
            invoice_id = str(invoices[0].get("invoiceId") or "").strip()
            if invoice_id:
                payment_reference = invoice_id
        status = str(_first(row, "status", "payoutStatus") or "").lower() or None
        return StandardPayout(
            transfer_id=payout_id,
            transfer_date=transfer_date,
            payment_due_date=transfer_date,
            transfer_amount=_money(_first(row, "payoutAmount", "netPayout")),
            transfer_status=status,
            payment_reference=payment_reference,
            currency=str(_first(row, "payoutCurrency", "currency") or "AED"),
        )

    @staticmethod
    def _statement_from(row: dict[str, Any]) -> StandardStatement | None:
        """A `ListAdditionalStatements` row mapped to a statement — port of
        `normalize_talabat_graphql_statement_rows`.

        This is the settlement *metadata* (id, date, gross); the per-order fee
        breakdown lives only inside the downloadable detailed xlsx bundle, which
        this httpx port does not fetch (see module docstring), so `lines` is
        empty and `total_fees`/`total_vat` are left unknown.
        """
        statement_id = str(row.get("statementId") or "").strip()
        statement_date = _date_str(row.get("statementDate"))
        if not statement_id or not statement_date:
            return None
        gross = _money(row.get("amountGross"))
        return StandardStatement(
            statement_id=statement_id,
            period_end=statement_date,
            payment_due_date=statement_date,
            gross_sales=gross,
            net_payable=gross,
            currency=str(row.get("currency") or "AED"),
            raw=dict(row),
        )


#: The module-level singleton, matching the careem/grubops/foodics providers —
#: stateless (the session is passed per call), so sharing it is free.
provider = TalabatClient()
