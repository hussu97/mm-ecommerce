"""Deliveroo Partner Hub, as its reporting-platform exports answer.

Deliveroo publishes no partner API this shop is on, so — like the browser the
bootstrap drove — this client works the Partner Hub *reporting platform*: it asks
the platform to build a CSV over a date window, waits for it, downloads it, and
parses the CSV. There is no bot wall (so plain `httpx` replays the session), and
the only credential is the `token` cookie, which `build_headers` already sends
from `session.cookies`. Everything is scoped to one `orgId` (497912 for this
brand's account); see `_org_id`.

Two data paths, ported from the Playwright exporter:

- **Sales** — the reporting platform's `orders` and `items_sold` reports. Both
  are built the same way: `POST /api/reporting_platform/reports` to trigger,
  poll `GET /api/reporting_platform/reports` until the report is ready, then
  `GET /api/reporting_platform/reports/{id}/download` for the CSV. Deliveroo caps
  a custom range at 15 days, so the window is chunked. The `orders` report is one
  row per completed order (the sales truth); the `items_sold` report is a
  *period-window aggregate* per menu item (`grain=aggregate`), not order-scoped,
  so it is carried on one synthetic aggregate order per outlet per window (see
  `_items_carrier`).

- **Finance** — the invoice/statement exports. `GET /api/invoices` lists the
  published statements; each has a numeric id used both as its `statement_id`
  and to pull `GET /api/invoices/{id}/download?file_type=statement_csv`, whose
  rows become the per-order settlement lines (real commission, VAT, adjustments).
  Each invoice also yields one scheduled payout keyed on its due date.

**What is confirmed vs inferred.** The download URL shapes
(`/api/reporting_platform/reports/{id}/download`,
`/api/invoices/{id}/download?file_type=statement_csv`), the `orgId`, and every
CSV column mapping are ported verbatim from the working exporter. The *request*
side of listing/triggering (the report-create body and the reports/invoices JSON
shapes) was UI-driven in the Playwright code, so it is reconstructed here and
read defensively — `.get`, `_first`, money left `None` (unknown, not zero) — and
every record keeps its `raw`, so the shapes are refined against real payloads
without re-porting. A report that never becomes ready, or a CSV that will not
download, is recorded as a `truncation_note` rather than raised, so a short pull
is visible instead of silent.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.models.aggregator import CHANNEL_DELIVEROO, GRAIN_AGGREGATE
from app.services.aggregators.normalized import (
    FinanceResult,
    SalesResult,
    StandardOrder,
    StandardOrderItem,
    StandardPayout,
    StandardStatement,
    StandardStatementLine,
)
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.aggregator_base import BaseAggregatorClient

logger = logging.getLogger(__name__)

_HUB = "https://partner-hub.deliveroo.com"
_API = f"{_HUB}/api"

#: The org the captured session belongs to (from the account audit). Only used
#: when the session carries no org ref of its own — see `_org_id`.
_DEFAULT_ORG_ID = "497912"

#: The shop's clock. A business date is a Dubai date, whatever zone the source
#: states its timestamps in — Deliveroo's statement CSV is explicitly UTC.
_BUSINESS_TZ = ZoneInfo("Asia/Dubai")

#: Deliveroo's reporting platform rejects a custom range wider than 15 days.
_MAX_WINDOW_DAYS = 15

#: How long to wait for a triggered report to build before giving up on that
#: window and noting the gap. ~2 min at 4s per poll.
_REPORT_POLL_ATTEMPTS = 30
_REPORT_POLL_SECONDS = 4.0

_REPORT_READY = {"complete", "completed", "ready", "done", "available", "success"}


def _num(value: Any) -> Decimal | None:
    """A money value as Decimal, or None for anything not a clean number.

    Ported from the exporter's `parse_money`, but honest about absence: a blank
    or unparseable cell is `None` (unknown), never `0` (charged nothing).
    Accepts `AED`/`د.إ` prefixes, thousands separators, and `(1.23)` negatives.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = (
        text.replace("AED", "")
        .replace("د.إ", "")
        .replace(",", "")
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


def _abs(value: Decimal | None) -> Decimal | None:
    """`abs`, but None-preserving — a fee the CSV did not state stays unknown."""
    return abs(value) if value is not None else None


def _first(mapping: Any, *keys: str) -> Any:
    """The first present, non-null value among `keys` — for a field a payload
    spells more than one way across the platform's shapes."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def _as_list(payload: Any, *keys: str) -> list[Any]:
    """A list out of a payload that is either a bare list or wraps one under a
    known key (`reports`, `invoices`, `data`)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_date(value: Any) -> date | None:
    """A `date` from an ISO string, a Deliveroo `Wed 3 Sep 2025` label, or a
    datetime — else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%a %d %b %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _local_dt(date_str: str | None, time_str: str | None) -> datetime | None:
    """A Dubai-local timestamp from the orders report's split date/time columns,
    which are already stated in local time."""
    stamp = f"{(date_str or '').strip()} {(time_str or '').strip()}".strip()
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=_BUSINESS_TZ
        )
    except ValueError:
        return None


def _utc_to_business(value: str | None) -> datetime | None:
    """Deliveroo's statement CSV states delivery time in UTC; the shop books it
    to the Dubai (UTC+4) business day, so an evening order is not shunted a day
    back."""
    if not value or not value.strip():
        return None
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone.utc).astimezone(_BUSINESS_TZ)


def _windows(from_date: date, to_date: date) -> list[tuple[date, date]]:
    """The requested range split into <=15-day chunks the platform will accept."""
    windows: list[tuple[date, date]] = []
    cursor = from_date
    while cursor <= to_date:
        end = min(cursor + timedelta(days=_MAX_WINDOW_DAYS - 1), to_date)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    return windows


class DeliverooClient(BaseAggregatorClient):
    channel = CHANNEL_DELIVEROO
    uses_tls_impersonation = False

    # ── scoping ──────────────────────────────────────────────────────────────
    def _org_id(self, session: LoadedSession) -> str:
        """The `orgId` every reporting-platform call is scoped to.

        Prefers a value the session carries (so a re-bootstrap onto another
        brand's org needs no code change); falls back to the audited default,
        which is the only org this account has today.
        """
        for source in (session.tokens or {}, session.header_profile or {}):
            value = _first(
                source, "org_id", "orgId", "organisation_id", "organization_id"
            )
            if value:
                return str(value)
        return _DEFAULT_ORG_ID

    # ── sales ────────────────────────────────────────────────────────────────
    async def fetch_sales(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> SalesResult:
        org_id = self._org_id(session)
        orders: list[StandardOrder] = []
        gaps: list[str] = []
        for w_start, w_end in _windows(since.date(), until.date()):
            orders_csv = await self._report_csv(
                session, org_id, "orders", w_start, w_end
            )
            if orders_csv is None:
                gaps.append(f"orders {w_start.isoformat()}..{w_end.isoformat()}")
            else:
                orders.extend(self._parse_orders_csv(orders_csv))

            items_csv = await self._report_csv(
                session, org_id, "items_sold", w_start, w_end
            )
            if items_csv is None:
                gaps.append(f"items {w_start.isoformat()}..{w_end.isoformat()}")
            else:
                orders.extend(self._parse_items_csv(items_csv, w_start, w_end))
        return SalesResult(
            orders=orders,
            truncation_note=(
                "Deliveroo reports not ready in time for: " + "; ".join(gaps)
                if gaps
                else None
            ),
        )

    async def _report_csv(
        self,
        session: LoadedSession,
        org_id: str,
        report_type: str,
        start_date: date,
        end_date: date,
    ) -> str | None:
        """Trigger, await and download one reporting-platform CSV as text.

        Returns None (rather than raising) when the report does not build in the
        poll budget or the download is not a clean 200 — the caller records that
        as truncation, so a short pull is visible instead of silently complete.
        An auth failure surfaces earlier, on the JSON trigger/poll calls, where
        the base maps it.
        """
        report_id = await self._create_report(
            session, org_id, report_type, start_date, end_date
        )
        if report_id is None:
            return None
        for _ in range(_REPORT_POLL_ATTEMPTS):
            if await self._report_ready(session, org_id, report_id):
                return await self._download_report(session, org_id, report_id)
            await asyncio.sleep(_REPORT_POLL_SECONDS)
        return None

    async def _create_report(
        self,
        session: LoadedSession,
        org_id: str,
        report_type: str,
        start_date: date,
        end_date: date,
    ) -> str | None:
        data = await self.request_json(
            session,
            "POST",
            f"{_API}/reporting_platform/reports",
            params={"orgId": org_id},
            json_body={
                "report_type": report_type,
                "order_source": "core",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                # The exporter selected "all sites"; the platform's own default is
                # the whole org, so no per-site id is sent (and none is hardcoded).
                "site_selection": "all",
            },
        )
        node = data.get("report") if isinstance(data, dict) else None
        source = node if isinstance(node, dict) else data
        report_id = _first(source, "id", "report_id", "reportId", "uuid")
        return str(report_id) if report_id is not None else None

    async def _report_ready(
        self, session: LoadedSession, org_id: str, report_id: str
    ) -> bool:
        listing = await self.request_json(
            session,
            "GET",
            f"{_API}/reporting_platform/reports",
            params={"orgId": org_id},
        )
        for row in _as_list(listing, "reports", "data"):
            rid = _first(row, "id", "report_id", "reportId", "uuid")
            if rid is not None and str(rid) == report_id:
                status = _first(row, "status", "state") or ""
                return str(status).strip().lower() in _REPORT_READY
        return False

    async def _download_report(
        self, session: LoadedSession, org_id: str, report_id: str
    ) -> str | None:
        response = await self.request_raw(
            session,
            "GET",
            f"{_API}/reporting_platform/reports/{report_id}/download",
            params={"orgId": org_id},
        )
        if getattr(response, "status_code", 0) != 200:
            return None
        return getattr(response, "text", None)

    def _parse_orders_csv(self, text: str) -> list[StandardOrder]:
        """One completed order per row — the sales truth.

        Column mapping ported verbatim from `parse_deliveroo_orders_report_csv`.
        The outlet is keyed by its restaurant name (the only outlet discriminator
        the report carries); the ingest resolves it to a branch through
        `aggregator_branch_map`.
        """
        reader = csv.DictReader(io.StringIO(text))
        orders: list[StandardOrder] = []
        for row in reader:
            status = (row.get("Order status") or "").strip().lower().replace(" ", "_")
            if status != "completed":
                continue
            placed_at = _local_dt(row.get("Date submitted"), row.get("Time submitted"))
            business_at = (
                _local_dt(row.get("Date delivered"), row.get("Time delivered"))
                or placed_at
            )
            if business_at is None:
                continue
            order_number = (row.get("Order number") or "").strip()
            if not order_number:
                continue
            outlet = (row.get("Restaurant name") or "").strip() or None
            gross = _num(row.get("Subtotal"))
            commission = _abs(_num(row.get("Deliveroo commission")))
            vat = _abs(_num(row.get("VAT on Deliveroo commission")))
            net_payable = (
                gross - commission - vat
                if None not in (gross, commission, vat)
                else None
            )
            orders.append(
                StandardOrder(
                    external_order_id=order_number,
                    external_outlet_id=outlet,
                    business_date=_iso(business_at.date()),
                    placed_at=placed_at or business_at,
                    status=status,
                    currency="AED",
                    gross_sales=gross,
                    net_sales=gross,
                    commission_amount=commission,
                    vat_amount=vat,
                    net_payable=net_payable,
                    raw=dict(row),
                )
            )
        return orders

    def _parse_items_csv(
        self, text: str, period_start: date, period_end: date
    ) -> list[StandardOrder]:
        """The period-window item aggregates, grouped into one carrier order per
        outlet.

        The `items_sold` report is not order-scoped — it is a sum per menu item
        over the window — so its rows cannot hang off a real order. Each outlet's
        rows are gathered onto one synthetic `aggregate` order (see
        `_items_carrier`) whose money is left `None` so it never inflates sales
        totals; the reconciliation ignores non-`line` grain. Column mapping ported
        from `parse_deliveroo_items_sold_report_csv`.
        """
        reader = csv.DictReader(io.StringIO(text))
        by_outlet: dict[str, list[StandardOrderItem]] = {}
        for index, row in enumerate(reader, start=1):
            outlet = (row.get("Restaurant name") or "").strip()
            item_name = (row.get("Item name") or "").strip()
            if not outlet or not item_name:
                continue
            category = (row.get("Category") or "").strip() or None
            subtotal = _num(row.get("Subtotal"))
            item_key = (
                f"{outlet}:{category or 'uncategorized'}:{item_name}"
                f":{period_start.isoformat()}:{period_end.isoformat()}"
            )
            by_outlet.setdefault(outlet, []).append(
                StandardOrderItem(
                    source_key=f"{item_key}:{index}",
                    grain=GRAIN_AGGREGATE,
                    item_name=item_name,
                    category_name=category,
                    quantity=_num(row.get("Quantity")),
                    unit_price=_num(row.get("Price")),
                    gross_sales=subtotal,
                    net_sales=subtotal,
                    amount_is_known=subtotal is not None,
                    period_start=period_start.isoformat(),
                    period_end=period_end.isoformat(),
                )
            )
        return [
            self._items_carrier(outlet, items, period_start, period_end)
            for outlet, items in by_outlet.items()
        ]

    @staticmethod
    def _items_carrier(
        outlet: str,
        items: list[StandardOrderItem],
        period_start: date,
        period_end: date,
    ) -> StandardOrder:
        """A synthetic parent for one outlet's window aggregates.

        `SalesResult` carries items only nested under a `StandardOrder`, and these
        aggregates belong to no single order, so they ride one clearly-namespaced
        carrier per `(outlet, window)`. It holds no money (all `None`) and a
        distinct `items_aggregate` status, so it adds item rows without adding a
        sale — the id namespace keeps it out of the real order keyspace.
        """
        return StandardOrder(
            external_order_id=(
                f"deliveroo-items:{outlet}"
                f":{period_start.isoformat()}:{period_end.isoformat()}"
            ),
            external_outlet_id=outlet,
            business_date=period_end.isoformat(),
            status="items_aggregate",
            currency="AED",
            items=items,
        )

    # ── finance (statements + payouts) ───────────────────────────────────────
    async def fetch_finance(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> FinanceResult:
        org_id = self._org_id(session)
        from_date, to_date = since.date(), until.date()
        statements: list[StandardStatement] = []
        payouts: list[StandardPayout] = []
        gaps: list[str] = []
        for invoice in await self._list_invoices(session, org_id):
            period_start = _parse_date(
                _first(invoice, "period_start", "start_date", "from", "billing_start")
            )
            period_end = _parse_date(
                _first(invoice, "period_end", "end_date", "to", "billing_end")
            )
            # Keep only invoices overlapping the requested window.
            if period_end and period_end < from_date:
                continue
            if period_start and period_start > to_date:
                continue
            invoice_id = _first(invoice, "id", "invoice_id", "reference", "number")
            if invoice_id is None:
                continue
            statement_id = str(invoice_id)
            due_date = _parse_date(
                _first(invoice, "payment_due_date", "due_date", "paid_at", "pay_date")
            )
            net_payable = _num(
                _first(invoice, "net_payable", "total", "amount", "amount_due")
            )
            currency = _first(invoice, "currency", "currency_code") or "AED"

            lines: list[StandardStatementLine] = []
            csv_text = await self._invoice_csv(session, org_id, statement_id)
            if csv_text is None:
                gaps.append(statement_id)
            else:
                lines = self._statement_lines(statement_id, csv_text)

            statements.append(
                StandardStatement(
                    statement_id=statement_id,
                    period_start=_iso(period_start),
                    period_end=_iso(period_end),
                    payment_due_date=_iso(due_date),
                    net_payable=net_payable,
                    currency=currency,
                    lines=lines,
                    raw=invoice if isinstance(invoice, dict) else None,
                )
            )
            payouts.append(
                StandardPayout(
                    transfer_id=statement_id,
                    statement_id=statement_id,
                    transfer_date=_iso(due_date),
                    payment_due_date=_iso(due_date),
                    transfer_amount=net_payable,
                    transfer_status="scheduled",
                    payment_reference=statement_id,
                    currency=currency,
                )
            )
        return FinanceResult(
            statements=statements,
            payouts=payouts,
            truncation_note=(
                "Deliveroo statement CSVs unavailable for invoices: " + ", ".join(gaps)
                if gaps
                else None
            ),
        )

    async def _list_invoices(
        self, session: LoadedSession, org_id: str
    ) -> list[dict[str, Any]]:
        listing = await self.request_json(
            session, "GET", f"{_API}/invoices", params={"orgId": org_id}
        )
        return [
            row
            for row in _as_list(listing, "invoices", "data")
            if isinstance(row, dict)
        ]

    async def _invoice_csv(
        self, session: LoadedSession, org_id: str, invoice_id: str
    ) -> str | None:
        response = await self.request_raw(
            session,
            "GET",
            f"{_API}/invoices/{invoice_id}/download",
            params={"file_type": "statement_csv", "orgId": org_id},
        )
        if getattr(response, "status_code", 0) != 200:
            return None
        return getattr(response, "text", None)

    def _statement_lines(
        self, statement_id: str, text: str
    ) -> list[StandardStatementLine]:
        """The per-order settlement lines behind one invoice.

        Ported from `normalize_deliveroo_statement_lines`: the CSV has a preamble,
        so the header row is found by its `Restaurant Name` / `Order ID` shape,
        and each data row emits up to five non-zero lines (order value, an
        activity adjustment, commission, its VAT, and the net payable).
        """
        reader = csv.reader(io.StringIO(text))
        headers: list[str] | None = None
        lines: list[StandardStatementLine] = []
        row_number = 0
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) == 1:
                continue
            if row[0].strip() == "Restaurant Name" and "Order ID" in row:
                headers = [cell.strip() for cell in row]
                continue
            if not headers:
                continue
            row_number += 1
            data = dict(zip(headers, row, strict=False))
            external_order_id = (data.get("Order ID") or "").strip() or None
            activity = (data.get("Activity") or "").strip()
            note = (data.get("Note") or "").strip() or None
            line_date = _iso(
                dt.date()
                if (dt := _utc_to_business(data.get("Delivery Date & Time (UTC)")))
                else None
            )
            base_key = external_order_id or f"{statement_id}:{row_number}"
            values = (
                ("gross_sales", "gross_sales", _num(data.get("Order Value (د.إ)"))),
                (
                    "adjustment",
                    activity.lower().replace(" ", "_") or "adjustment",
                    _num(data.get("Adjustment Net (د.إ)")),
                ),
                ("fee", "commission", _num(data.get("Deliveroo Commission (د.إ)"))),
                (
                    "vat",
                    "commission_vat",
                    _num(data.get("Commission / Adjustment VAT (د.إ)")),
                ),
                ("net_payable", "net_payable", _num(data.get("Total Payable"))),
            )
            for line_type, fee_category, amount in values:
                if amount is None or amount == 0:
                    continue
                lines.append(
                    StandardStatementLine(
                        source_key=f"{statement_id}:{base_key}:{fee_category}",
                        statement_id=statement_id,
                        external_order_id=external_order_id,
                        line_date=line_date,
                        line_type=line_type,
                        fee_category=fee_category,
                        description=note or activity or fee_category,
                        amount=amount,
                        currency="AED",
                    )
                )
        return lines


#: The module-level singleton, matching the careem/grubops/foodics providers —
#: stateless (the session is passed per call), so sharing it is free.
provider = DeliverooClient()
