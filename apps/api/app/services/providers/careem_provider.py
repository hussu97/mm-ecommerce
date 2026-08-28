"""Careem Now, as its partner console's private API answers.

The cleanest of the five: plain REST under `partners.careem.com/api/saturn-ext`,
a `Bearer` on every call, and no bot wall (Dynatrace RUM only), so a captured
session replays over `httpx` without impersonation. Endpoints and shapes here
were taken from the live console:

- Scope: `GET /v2/admin/merchants/user/scope?attributes[]=area` → the
  company/brand/merchant tree with each merchant's area, which is how a Careem
  outlet id is tied to one of our branches (Barsha 1067984, Silicon Oasis/DSO
  1069463, Al Majaz/Sharjah 1087801 — the last `statusId=3`, i.e. shut).
- Sales: `GET /v1/orders/list` and the per-outlet
  `/v1/careem/{city}/company/{c}/brand/{b}/outlet/{o}/partner-orders-minimal`.
- Payouts: `POST /v1/billing/payoutRequests/list` with a date window.
- Balances: `POST /v1/billing/billingAccounts/earnings` (a balance snapshot, not
  a per-order settlement — Careem exposes no per-period statement document, so
  the real commission per order is read from the order itself, not a statement).

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
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.aggregator import CHANNEL_CAREEM
from app.services.aggregators.normalized import (
    PayoutsResult,
    SalesResult,
    StandardOrder,
    StandardOrderItem,
    StandardPayout,
    StatementsResult,
)
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.aggregator_base import BaseAggregatorClient

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


def _num(value: Any) -> Decimal | None:
    """A money value as Decimal, or None for anything not a clean number.

    Careem wraps money as `{"amount": 357.525, "currency": "AED"}` in places and
    a bare number in others, so both are accepted.
    """
    if isinstance(value, dict):
        value = value.get("amount")
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    """The first present, non-null value among `keys` — for a field a payload
    spells more than one way across endpoints."""
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) is not None:
            return mapping[key]
    return None


class CareemClient(BaseAggregatorClient):
    channel = CHANNEL_CAREEM
    uses_tls_impersonation = False

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
        outlets = await self.discover_outlets(session)
        city_id = self._city_id(session)
        orders: list[StandardOrder] = []
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
                order = self._order_from(row, outlet["external_outlet_id"])
                if order is not None:
                    orders.append(order)
        return SalesResult(orders=orders)

    def _order_from(self, row: dict[str, Any], outlet_id: str) -> StandardOrder | None:
        external = _first(row, "id", "orderId", "order_id", "reference")
        if external is None:
            return None
        placed_raw = _first(row, "created_at", "createdAt", "placedAt", "date")
        placed_at = _parse_dt(placed_raw)
        totals = _first(row, "totals", "price", "amount") or {}
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
        return StandardOrder(
            external_order_id=str(external),
            external_outlet_id=str(outlet_id),
            business_date=placed_at.strftime("%Y-%m-%d") if placed_at else None,
            placed_at=placed_at,
            status=_first(row, "status", "state", "orderStatus"),
            currency=_first(row, "currency", "currencyCode") or "AED",
            gross_sales=_num(
                _first(row, "total", "grand_total", "totalAmount") or totals
            ),
            commission_amount=_num(_first(row, "commission", "commissionAmount")),
            delivery_fee=_num(_first(row, "delivery_fee", "deliveryFee")),
            vat_amount=_num(_first(row, "tax", "vat", "taxAmount")),
            items=items,
            raw=row,
        )

    # ── finance (payouts only; Careem has no statement document) ────────────
    async def fetch_statements(
        self, session: LoadedSession, *, since: datetime, until: datetime
    ) -> StatementsResult:
        return StatementsResult(statements=[])

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


def _date_str(value: Any) -> str | None:
    dt = _parse_dt(value)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return value if isinstance(value, str) and len(value) == 10 else None


#: The module-level singleton, matching the grubops/foodics providers — it is
#: stateless (the session is passed in per call), so sharing it is free.
provider = CareemClient()
