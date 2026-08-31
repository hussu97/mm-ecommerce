"""Careem Now, as its partner console's private API answers.

The cleanest of the five: plain REST under `partners.careem.com/api/saturn-ext`,
a `Bearer` on every call, and no bot wall (Dynatrace RUM only), so a captured
session replays over `httpx` without impersonation. Endpoints and shapes here
were taken from the live console:

- Scope: `GET /v2/admin/merchants/user/scope?attributes[]=area` → the
  company/brand/merchant tree with each merchant's area, which is how a Careem
  outlet id is tied to one of our branches (Barsha 1067984, Silicon Oasis/DSO
  1069463, Al Majaz/Sharjah 1087801 — the last `statusId=3`, i.e. shut).
- Sales: the per-outlet
  `/v1/careem/{city}/company/{c}/brand/{b}/outlet/{o}/partner-orders-minimal`
  lists the ids in the window (id/status/date/total only), then
  `GET /v2/admin/orders/{id}?all_localizations=true` — the console's order popup —
  fills each with items, modifiers, category, the captain (name + mobile), the
  customer dropoff address and the full delivery status timeline. (The minimal
  feed alone is why Careem once looked "thin"; the detail endpoint is not.)
- Payouts: `POST /v1/billing/payoutRequests/list` with a date window.
- Balances: `POST /v1/billing/billingAccounts/earnings` (a balance snapshot).
- Invoices: `POST /v1/billing/billingReports/list` (reportType=INVOICE) lists the
  monthly Tax Invoices, and `GET /v1/billing/billingReports/{id}/download?
  billableId&billableType&tenant=FOOD` hands back a `{"fileUrl": <pre-signed S3>}`
  for the PDF. Careem has no PER-ORDER settlement — the order detail carries the
  goods and the captain but not the merchant's commission — so the fees live only
  on that monthly Tax Invoice, which `fetch_statements` archives as the VAT
  document.

The billing calls need the account's `billableId`/`billableType` triple, which
comes from the scope tree, so `fetch_finance` resolves scope first.

Response field extraction is deliberately defensive: it reads the ids and dates
it is sure of and leaves money null (unknown, not zero) where a key is not
confirmed, and every record keeps its `raw`, so the mapping is refined against
real payloads without re-fetching. The account had no open orders/payouts in the
windows sampled at build time.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from app.models.aggregator import CHANNEL_CAREEM
from app.services.aggregators.normalized import (
    PayoutsResult,
    SalesResult,
    StandardModifier,
    StandardOrder,
    StandardOrderItem,
    StandardPayout,
    StandardStatement,
    StandardStatusEvent,
    StatementsResult,
)
from app.services.aggregators.session_store import LoadedSession
from app.services.aggregators.statement_docs import store_statement_invoice
from app.services.providers._agg_parse import DUBAI_TZ as _DUBAI
from app.services.providers._agg_parse import first_present as _first
from app.services.providers._agg_parse import parse_money as _num
from app.services.providers.aggregator_base import (
    AggregatorAuthError,
    BaseAggregatorClient,
)

logger = logging.getLogger(__name__)

_API = "https://partners.careem.com/api/saturn-ext"
_TENANT = "FOOD"
_PAGE_SIZE = 50
#: Careem scopes the per-outlet orders endpoint by city id (1 = Dubai). The live
#: value is read off the session (`tokens["city_id"]`, injected from
#: `aggregator_account.extras` by `session_store.enrich_session`); this is the
#: fallback when the account carries none, so behaviour is unchanged until an
#: operator sets it. NOTE: a *per-outlet* city ultimately belongs on
#: `aggregator_branch_map` (an outlet, not an account, has a city) — account-level
#: `extras["city_id"]` is the pragmatic single-brand step until an outlet spans
#: more than one city.
_DEFAULT_CITY_ID = "1"
#: Hard ceiling on payout pages so a bad/stuck `totalRecords` (or a session that
#: has quietly expired into a redirect) cannot spin the loop forever against the
#: live console — mirrors talabat's `_MAX_FINANCE_PAGES` guard.
_MAX_PAYOUT_PAGES = 200
#: Careem publishes a month's Tax Invoice ~a week after the month ends, so the
#: invoice discovery reaches back at least this far — a 1-day finance sweep would
#: otherwise never see the just-published prior-month invoice.
_INVOICE_LOOKBACK_DAYS = 62
#: Timeout for fetching the pre-signed S3 invoice PDF (a plain download, not the
#: Careem console).
_S3_TIMEOUT = 30.0
#: The rich per-order detail endpoint — the console's order popup. Unlike
#: `partner-orders-minimal` (id/status/date/total only) this carries items,
#: modifiers, the captain, the dropoff address and the delivery status timeline.
_ORDER_DETAIL = f"{_API}/v2/admin/orders"
#: Cap on per-order detail fetches in one sales pass, so a runaway window cannot
#: hammer the console. A daily pass is a few dozen orders; a wide backfill is
#: bounded and records the shortfall as a truncation note.
_MAX_DETAIL_FETCHES = 400


def _localized_en(value: Any) -> str | None:
    """The English string from a Careem `{ar, en}` localisation object."""
    if isinstance(value, dict):
        text = value.get("en") or value.get("default")
        return str(text) if text else None
    return None


def _careem_address(detail: dict[str, Any]) -> dict[str, Any] | None:
    """The customer's dropoff address, structured. Careem withholds the customer's
    name/phone (only a `user_id`), but the delivery address is exposed."""
    drop = detail.get("dropoff_address")
    if not isinstance(drop, dict):
        return None
    loc = drop.get("location") if isinstance(drop.get("location"), dict) else {}
    out = {
        "area": drop.get("area"),
        "building": drop.get("building"),
        "street": drop.get("street"),
        "number": drop.get("number"),
        "city": drop.get("city"),
        "nickname": drop.get("nickname"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
    }
    out = {k: v for k, v in out.items() if v not in (None, "")}
    return out or None


def _items_from_detail(external: str, items_raw: Any) -> list[StandardOrderItem]:
    """Per-line items + modifiers from the detail payload. The product name is on
    `menu_item.item`; each `groups[].options[]` is a chosen option (the priced
    variant or an add-on), carried as a `StandardModifier` and summed to the line
    price (Careem puts the price on the option, not the base item)."""
    items: list[StandardOrderItem] = []
    for idx, it in enumerate(items_raw or []):
        if not isinstance(it, dict):
            continue
        menu = it.get("menu_item") if isinstance(it.get("menu_item"), dict) else {}
        name = (
            _first(menu, "item")
            or _localized_en(menu.get("item_localized"))
            or _first(it, "name", "item_name", "title")
        )
        count = _num(_first(it, "count", "quantity", "qty")) or Decimal("1")
        # The line price is `item.price.total_with_options` (the item + its priced
        # options) — NOT the sum of the option prices, which are 0 for a box whose
        # contents are free (the box itself carries the price). Fall back to the
        # menu item's price when the line has no own price.
        item_price = it.get("price") if isinstance(it.get("price"), dict) else {}
        menu_price = menu.get("price") if isinstance(menu.get("price"), dict) else {}
        gross = _num(
            _first(item_price, "total_with_options", "total", "original")
        ) or _num(_first(menu_price, "total", "original"))
        unit = (gross / count) if (gross is not None and count) else None
        # Options are still captured as modifiers (the box contents / add-ons),
        # with their own prices (often 0), for the itemised record.
        modifiers: list[StandardModifier] = []
        for group in it.get("groups") or []:
            if not isinstance(group, dict):
                continue
            for opt in group.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                oname = _first(opt, "name") or _localized_en(opt.get("name_localized"))
                oprice = _num(_first(opt.get("price") or {}, "total", "original"))
                ocount = _num(_first(opt, "count", "count_per_item")) or Decimal("1")
                if oname:
                    modifiers.append(
                        StandardModifier(
                            name=str(oname), quantity=ocount, unit_price=oprice
                        )
                    )
        items.append(
            StandardOrderItem(
                source_key=f"{external}:{idx}",
                item_name=str(name) if name else None,
                category_name=_first(it, "category_name"),
                quantity=count,
                unit_price=unit,
                gross_sales=gross,
                amount_is_known=gross is not None,
                modifiers=modifiers,
            )
        )
    return items


def _status_events_from_detail(detail: dict[str, Any]) -> list[StandardStatusEvent]:
    """The order's lifecycle: the order-level timestamps plus the captain's
    delivery `status_log` (DRIVER_ASSIGNED → DRIVER_HERE → TRIP_STARTED →
    TRIP_ENDED). Careem timestamps are tz-aware, kept as-is."""
    events: list[StandardStatusEvent] = []
    seq = 0
    for field, word in (
        ("pending_at", "pending"),
        ("accepted_at", "accepted"),
        ("delivered_at", "delivered"),
        ("cancelled_at", "cancelled"),
    ):
        at = _parse_dt(detail.get(field))
        if at is not None:
            seq += 1
            events.append(StandardStatusEvent(status=word, at=at, sequence=seq))
    delivery = (
        detail.get("delivery") if isinstance(detail.get("delivery"), dict) else {}
    )
    for entry in delivery.get("status_log") or []:
        if not isinstance(entry, dict):
            continue
        at = _parse_dt(_first(entry, "created_at", "createdAt"))
        status = _first(entry, "status")
        if status and at is not None:
            seq += 1
            events.append(
                StandardStatusEvent(
                    status=str(status).lower(), at=at, sequence=seq, raw=entry
                )
            )
    return events


def _currency_code(value: Any) -> str:
    """The 3-letter code from Careem's `currency`, which is an object
    (`{code: "AED", …}`) on the orders endpoint but may be a bare string
    elsewhere. Defaults to AED."""
    if isinstance(value, dict):
        code = _first(value, "code", "currency_code", "iso_code")
        return str(code) if code else "AED"
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "AED"


class CareemClient(BaseAggregatorClient):
    channel = CHANNEL_CAREEM
    # Careem's partner API sits behind Cloudflare and rejects a plain-httpx TLS
    # ClientHello with a bare 401 (not a challenge page) even with a valid bearer
    # + cookies — verified live: the identical request via curl_cffi impersonating
    # Chrome returns 200. So it needs TLS impersonation like Talabat/Noon.
    uses_tls_impersonation = True
    impersonate_target = "chrome"

    @staticmethod
    def _city_id(session: LoadedSession) -> str:
        """The city id the per-outlet orders endpoint is scoped by.

        Read from `tokens["city_id"]` (injected from `aggregator_account.extras`
        by `session_store.enrich_session`), falling back to `_DEFAULT_CITY_ID`
        (Dubai) when the account carries none — so the request is identical to
        before until an operator sets it.
        """
        value = (session.tokens or {}).get("city_id")
        if value not in (None, "", 0):
            return str(value)
        return _DEFAULT_CITY_ID

    # ── scope / outlet discovery ────────────────────────────────────────────
    async def discover_outlets(self, session: LoadedSession) -> list[dict[str, Any]]:
        """The merchant tree, flattened to one dict per outlet.

        Feeds the seed/mapping: each row carries the ids the `aggregator_branch_map`
        needs plus the human `area_name` to match to a branch and the `active`
        flag (Careem `statusId != 1` — 3 is shut, which is Sharjah today).
        """
        data = await self.request_json(
            session,
            "GET",
            f"{_API}/v2/admin/merchants/user/scope",
            params={"attributes[]": "area"},
        )
        outlets: list[dict[str, Any]] = []
        for company in data.get("companies", []) or []:
            for brand in company.get("brands", []) or []:
                for merchant in brand.get("merchants", []) or []:
                    status_id = _first(merchant, "statusId", "status_id")
                    outlets.append(
                        {
                            "external_outlet_id": str(merchant.get("id")),
                            "external_brand_id": str(brand.get("id")),
                            "external_company_id": str(company.get("id")),
                            "area_name": merchant.get("areaName"),
                            "name": merchant.get("name"),
                            "status_id": status_id,
                            "active": status_id == 1,
                        }
                    )
        return outlets

    def _billing_accounts(self, outlets: list[dict[str, Any]]) -> list[dict[str, str]]:
        """The `billableId`/`billableType` list the billing endpoints expect —
        the company, the brand, and every merchant, deduped."""
        accounts: dict[tuple[str, str], dict[str, str]] = {}
        for o in outlets:
            for bid, btype in (
                (o["external_company_id"], "COMPANY"),
                (o["external_brand_id"], "BRAND"),
                (o["external_outlet_id"], "MERCHANT"),
            ):
                if bid and bid != "None":
                    accounts[(bid, btype)] = {
                        "billableId": int(bid) if bid.isdigit() else bid,
                        "billableType": btype,
                    }
        return list(accounts.values())

    # ── sales ───────────────────────────────────────────────────────────────
    async def fetch_sales(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> SalesResult:
        """Two-step: `partner-orders-minimal` lists the ids in the window, then the
        `v2/admin/orders/{id}` detail (the console's order popup) fills each one
        with its items, modifiers, captain, dropoff address and status timeline —
        none of which the minimal feed carries. Detail is best-effort per order: a
        failed detail falls back to the minimal row so the order is never lost."""
        outlets = await self.discover_outlets(session)
        city_id = self._city_id(session)
        orders: list[StandardOrder] = []
        seen: set[str] = set()
        detail_failures = 0
        capped = False
        for outlet in outlets:
            if not outlet["active"]:
                continue
            payload = await self.request_json(
                session,
                "GET",
                f"{_API}/v1/careem/{city_id}/company/{outlet['external_company_id']}"
                f"/brand/{outlet['external_brand_id']}"
                f"/outlet/{outlet['external_outlet_id']}/partner-orders-minimal",
                params={
                    "all_localizations": "true",
                    "startDate": since.strftime("%Y-%m-%d"),
                    "endDate": until.strftime("%Y-%m-%d"),
                },
            )
            for row in _first(payload, "orders", "data") or []:
                minimal = self._order_from(row, outlet["external_outlet_id"])
                if minimal is None:
                    continue
                # `partner-orders-minimal` ignores startDate/endDate (rolling
                # window) AND returns every outlet's orders on each outlet call, so
                # honour the requested range by Dubai business date and dedupe.
                bdate = _dubai_date(minimal.placed_at)
                if not (bdate is None or since.date() <= bdate <= until.date()):
                    continue
                if minimal.external_order_id in seen:
                    continue
                seen.add(minimal.external_order_id)

                if len(seen) > _MAX_DETAIL_FETCHES:
                    capped = True
                    orders.append(minimal)
                    continue
                detail = None
                try:
                    detail = await self._fetch_detail(
                        session, minimal.external_order_id
                    )
                except AggregatorAuthError:
                    raise  # a dead session must stop the pass, not be swallowed
                except Exception:  # noqa: BLE001 — one bad detail must not lose the run
                    logger.exception(
                        "careem order %s detail failed", minimal.external_order_id
                    )
                    detail_failures += 1
                orders.append(
                    self._order_from_detail(detail, minimal)
                    if detail is not None
                    else minimal
                )
        notes = []
        if detail_failures:
            notes.append(f"{detail_failures} order detail(s) unavailable")
        if capped:
            notes.append(f"detail fetch capped at {_MAX_DETAIL_FETCHES}")
        return SalesResult(orders=orders, truncation_note="; ".join(notes) or None)

    async def _fetch_detail(
        self, session: LoadedSession, order_id: str
    ) -> dict[str, Any] | None:
        """The rich order detail (`v2/admin/orders/{id}`), or None if it has no
        body. Auth failures propagate; other errors are the caller's to handle."""
        payload = await self.request_json(
            session, "GET", f"{_ORDER_DETAIL}/{order_id}?all_localizations=true"
        )
        if not isinstance(payload, dict):
            return None
        root = payload.get("data", payload)
        return root if isinstance(root, dict) else None

    def _order_from_detail(
        self, detail: dict[str, Any], minimal: StandardOrder
    ) -> StandardOrder:
        """Build the full order from the detail payload, keeping the minimal row's
        outlet/date resolution as the fallback."""
        external = str(_first(detail, "id") or minimal.external_order_id)
        placed_at = _parse_dt(_first(detail, "created_at", "createdAt")) or (
            minimal.placed_at
        )
        bdate = _dubai_date(placed_at)
        price = detail.get("price") if isinstance(detail.get("price"), dict) else {}
        # The shop's sale is the MENU value (`price.sub_total`/`total`/`original`),
        # NOT `total_price`/`charge_amount` — those are what the customer was
        # charged, which includes Careem's own CPlus markup on top of the menu
        # price (e.g. 57.75 charged on a 55 menu subtotal) and is not the shop's
        # revenue. Tax is 0 for this (non-tax-registered) merchant, so sub_total is
        # the goods value.
        gross = _num(_first(price, "sub_total", "total", "original"))
        if gross is None:
            gross = minimal.gross_sales
        merchant = (
            detail.get("merchant") if isinstance(detail.get("merchant"), dict) else {}
        )
        delivery = (
            detail.get("delivery") if isinstance(detail.get("delivery"), dict) else {}
        )
        captain = (
            delivery.get("captain") if isinstance(delivery.get("captain"), dict) else {}
        )
        currency = _currency_code(_first(merchant.get("currency") or {}, "code"))
        return StandardOrder(
            external_order_id=external,
            external_outlet_id=str(
                _first(merchant, "id") or minimal.external_outlet_id
            ),
            business_date=bdate.isoformat() if bdate else minimal.business_date,
            placed_at=placed_at,
            accepted_at=_parse_dt(detail.get("accepted_at")),
            delivered_at=_parse_dt(detail.get("delivered_at")),
            cancelled_at=_parse_dt(detail.get("cancelled_at")),
            status=_first(detail, "status", "state") or minimal.status,
            currency=currency or minimal.currency or "AED",
            customer_address=_careem_address(detail),
            driver_name=_first(captain, "name") or None,
            driver_phone=_first(captain, "mobile", "phone") or None,
            driver_status=_first(delivery, "status") or None,
            status_events=_status_events_from_detail(detail),
            gross_sales=gross if gross is not None else minimal.gross_sales,
            vat_amount=_num(_first(price, "tax")),
            items=_items_from_detail(external, detail.get("items")),
            raw=detail,
        )

    def _order_from(self, row: dict[str, Any], outlet_id: str) -> StandardOrder | None:
        external = _first(row, "id", "orderId", "order_id", "reference")
        if external is None:
            return None
        placed_raw = _first(row, "created_at", "createdAt", "placedAt", "date")
        placed_at = _parse_dt(placed_raw)
        # `partner-orders-minimal` nests the order value under `price.total` and the
        # currency under a `currency` OBJECT (`{code: "AED", …}`), not a string.
        price = _first(row, "price", "totals", "amount")
        price = price if isinstance(price, dict) else {}
        gross = _num(_first(row, "total", "grand_total", "totalAmount"))
        if gross is None:
            gross = _num(_first(price, "total", "grand_total", "amount"))
        items = [
            StandardOrderItem(
                source_key=f"{external}:{idx}",
                item_name=_first(it, "name", "item_name", "title"),
                quantity=_num(_first(it, "quantity", "qty", "count")),
                unit_price=_num(_first(it, "unit_price", "price", "unitPrice")),
                gross_sales=_num(_first(it, "total", "total_price", "amount")),
                amount_is_known=_first(it, "total", "total_price", "amount")
                is not None,
            )
            for idx, it in enumerate(
                _first(row, "items", "orderItems", "lineItems") or []
            )
        ]
        # Attribute the order to the outlet it actually belongs to via its own
        # `merchant.id` — the per-outlet orders endpoint returns every outlet's
        # orders on each call, so the loop's `outlet_id` is not reliable and all
        # orders would collapse onto one branch. Fall back to the loop id.
        merchant = row.get("merchant")
        merchant_id = _first(merchant, "id") if isinstance(merchant, dict) else None
        bdate = _dubai_date(placed_at)
        return StandardOrder(
            external_order_id=str(external),
            external_outlet_id=str(merchant_id or outlet_id),
            business_date=bdate.isoformat() if bdate else None,
            placed_at=placed_at,
            status=_first(row, "status", "state", "orderStatus"),
            currency=_currency_code(_first(row, "currency", "currencyCode")),
            gross_sales=gross,
            commission_amount=_num(_first(row, "commission", "commissionAmount")),
            delivery_fee=_num(_first(row, "delivery_fee", "deliveryFee")),
            vat_amount=_num(_first(row, "tax", "vat", "taxAmount")),
            items=items,
            raw=row,
        )

    # ── finance ─────────────────────────────────────────────────────────────
    async def _invoice_reports(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> list[dict[str, Any]]:
        """The monthly Tax-Invoice reports whose period falls in the window.

        `billingReports/list` (reportType=INVOICE) is what the console's Finances
        → Invoices tab calls; each row is one billable account's invoice for one
        calendar month, `{id, referenceId, billableId, billableType, startDate,
        endDate}`. Careem publishes an invoice ~a week AFTER the month it covers,
        so the window is widened to at least `_INVOICE_LOOKBACK_DAYS` — a 1-day
        finance sweep would otherwise always miss the just-published prior month.
        """
        outlets = await self.discover_outlets(session)
        accounts = self._billing_accounts(outlets)
        start = min(since, until - timedelta(days=_INVOICE_LOOKBACK_DAYS))
        reports: list[dict[str, Any]] = []
        page = 0
        for _ in range(_MAX_PAYOUT_PAGES):
            body = {
                "tenant": _TENANT,
                "billingAccounts": accounts,
                "startDate": start.strftime("%Y-%m-%dT00:00:00"),
                "endDate": until.strftime("%Y-%m-%dT23:59:59"),
                "entryType": "FOOD_ORDER",
                "reportType": "INVOICE",
                "pageNumber": page,
                "pageSize": _PAGE_SIZE,
            }
            data = await self.request_json(
                session,
                "POST",
                f"{_API}/v1/billing/billingReports/list",
                json_body=body,
            )
            rows = data.get("reports", []) or []
            reports.extend(r for r in rows if str(r.get("status")) == "SUCCESS")
            info = data.get("paginationInfo") or {}
            total = info.get("totalRecords", 0)
            page += 1
            if not rows or page * _PAGE_SIZE >= total:
                break
        return reports

    async def _download_invoice_pdf(
        self, session: LoadedSession, report: dict[str, Any]
    ) -> tuple[bytes, str] | None:
        """Resolve a report's pre-signed PDF URL and fetch the bytes.

        The download endpoint returns `{"fileUrl": "<S3 pre-signed URL>"}` (valid
        ~7 days); the S3 URL itself is public (the signature is the auth), so it is
        fetched with a plain client, not the Careem session.
        """
        rid = report.get("id")
        billable_id = report.get("billableId")
        billable_type = report.get("billableType")
        if rid is None or billable_id is None or not billable_type:
            return None
        meta = await self.request_json(
            session,
            "GET",
            f"{_API}/v1/billing/billingReports/{rid}/download",
            params={
                "billableId": str(billable_id),
                "billableType": str(billable_type),
                "tenant": _TENANT,
            },
        )
        file_url = meta.get("fileUrl") if isinstance(meta, dict) else None
        if not file_url:
            return None
        async with httpx.AsyncClient(timeout=_S3_TIMEOUT) as client:
            resp = await client.get(file_url)
        if resp.status_code != 200 or not resp.content:
            return None
        filename = file_url.split("?", 1)[0].rsplit("/", 1)[-1] or f"careem-{rid}.pdf"
        return resp.content, filename

    async def fetch_statements(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> StatementsResult:
        """Careem's monthly Tax Invoices — the VAT documents finance claims against.

        There is no per-order settlement statement (the order feed carries no
        fees), but Careem issues one Tax Invoice PDF per billable account per
        month with the platform / processing / CPlus fee breakdown. This
        enumerates them and archives each PDF, keyed on its invoice number, so a
        rerun re-links rather than duplicates. Best-effort per invoice: a download
        that fails still yields the statement metadata (period + invoice number),
        so the row exists and a later pass can attach the document.
        """
        try:
            reports = await self._invoice_reports(session, since=since, until=until)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the sweep
            return StatementsResult(
                statements=[], truncation_note=f"careem invoice list failed: {exc}"
            )
        statements: list[StandardStatement] = []
        for report in reports:
            ref = report.get("referenceId")
            rid = report.get("id")
            statement_id = str(ref or f"CAREEM_INVOICE_{rid}")
            stmt = StandardStatement(
                statement_id=statement_id,
                period_start=_date_str(report.get("startDate")),
                period_end=_date_str(report.get("endDate")),
                currency="AED",
                external_outlet_id=(
                    str(report.get("billableId"))
                    if report.get("billableId") is not None
                    else None
                ),
                raw=report,
            )
            try:
                pdf = await self._download_invoice_pdf(session, report)
            except Exception:  # noqa: BLE001 — one bad download must not drop the row
                pdf = None
                logger.exception("careem invoice %s download failed", statement_id)
            if pdf is not None:
                body, filename = pdf
                stored = store_statement_invoice(
                    channel=self.channel,
                    statement_id=statement_id,
                    filename=filename,
                    body=body,
                    content_type="application/pdf",
                )
                if stored is not None:
                    stmt = replace(
                        stmt,
                        invoice_object_key=stored.object_key,
                        invoice_content_type=stored.content_type,
                        invoice_original_filename=stored.original_filename,
                        invoice_fetched_at=stored.fetched_at,
                    )
            statements.append(stmt)
        return StatementsResult(statements=statements)

    async def fetch_payouts(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> PayoutsResult:
        outlets = await self.discover_outlets(session)
        accounts = self._billing_accounts(outlets)
        payouts: list[StandardPayout] = []
        page = 0
        for _ in range(_MAX_PAYOUT_PAGES):
            body = {
                "tenant": _TENANT,
                "billingAccounts": accounts,
                "startDate": since.strftime("%Y-%m-%dT00:00:00"),
                "endDate": until.strftime("%Y-%m-%dT23:59:59"),
                "pageNumber": page,
                "pageSize": _PAGE_SIZE,
            }
            data = await self.request_json(
                session,
                "POST",
                f"{_API}/v1/billing/payoutRequests/list",
                json_body=body,
            )
            rows = data.get("payoutRequests", []) or []
            for row in rows:
                payouts.append(self._payout_from(row))
            info = data.get("paginationInfo") or {}
            total = info.get("totalRecords", 0)
            page += 1
            if not rows or page * _PAGE_SIZE >= total:
                break
        else:
            # The loop exhausted the cap without a natural stop — a bad/absent
            # `totalRecords` that never satisfies the break. Stop rather than
            # hammer the console; the payouts gathered so far are still returned.
            logger.warning(
                "%s payout pagination hit the %d-page cap (%d rows so far); "
                "results may be truncated",
                self.channel,
                _MAX_PAYOUT_PAGES,
                len(payouts),
            )
        return PayoutsResult(payouts=payouts)

    def _payout_from(self, row: dict[str, Any]) -> StandardPayout:
        amount = _first(row, "amount", "payoutAmount", "transferAmount")
        return StandardPayout(
            transfer_id=str(_first(row, "id", "payoutId", "reference", "transferId")),
            transfer_date=_date_str(_first(row, "createdAt", "date", "payoutDate")),
            payment_due_date=_date_str(_first(row, "dueDate", "expectedDate")),
            transfer_amount=_num(amount),
            transfer_status=_first(row, "status", "state"),
            payment_reference=_first(row, "reference", "referenceNumber"),
            currency=(amount.get("currency") if isinstance(amount, dict) else None)
            or "AED",
        )


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dubai_date(dt: datetime | None) -> date | None:
    """The Dubai calendar date of an instant (assume UTC if naive) — the business
    day the order belongs to, matching the ingest's Dubai-aligned range window."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DUBAI).date()


def _date_str(value: Any) -> str | None:
    dt = _parse_dt(value)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return value if isinstance(value, str) and len(value) == 10 else None


#: The module-level singleton, matching the grubops/foodics providers — it is
#: stateless (the session is passed in per call), so sharing it is free.
provider = CareemClient()
