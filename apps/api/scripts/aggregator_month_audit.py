"""Month-end aggregator audit: our orders against the marketplaces' own figures.

Run on prod, in the api container (the marketplace sessions are live there):

  docker exec -w /app melting-moments-cakes-api-1 \\
    python -m scripts.aggregator_month_audit --month 2026-10

`--to` caps the last business date (default: the month's end, or yesterday if the
month is not over). Prints one line, `JSON{...}`, with:

* `coverage` — per channel, delivered orders with and without fees, and the
  business dates still fee-less past the channel's settle lag
  (`ingest.fee_gap_dates`, the nightly catch-up's own test).
* `live` — per channel, our orders and fees checked against the portal:
  Talabat `ListOrders` (every order's net), Careem `billingEntries`, Deliveroo's
  daily order lists and weekly statements, noon's weekly statements, and Keeta's
  ingested bills against its order feed (Keeta cannot be fetched from here).
* `cancellations` — every cancelled or refunded order with who was at fault and
  what it cost, read from each marketplace's own fields.
* `payouts` — what each marketplace paid, and Talabat's invoices tied to the
  sum of their orders' nets.
* `pnl` — the P&L page's figures for the window (`pnl_report.build`).

Read-only: it loads sessions the way a sweep does (which may refresh a token)
and writes nothing else. Fixing what it finds is a separate, deliberate step
(`ingest.run_range`, a code change, or a guarded `UPDATE` on the
orders it mis-booked).
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import dataclasses
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.core.database import SchedulerSessionFactory
from app.models.aggregator import (
    AggregatorBranchMap,
    AggregatorOrder,
    AggregatorPayout,
    AggregatorStatement,
    AggregatorStatementLine,
)
from app.services.aggregators import ingest
from app.services.orders import order_pnl, pnl_report
from app.services.providers import careem_provider
from app.services.providers import talabat_provider as tp

DXB = ZoneInfo("Asia/Dubai")
TOL = Decimal("0.02")
_CANCEL_WORDS = {"cancelled", "canceled", "rejected", "failed", "declined"}


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _cancelled(status: str | None) -> bool:
    return (status or "").strip().lower() in _CANCEL_WORDS or status == "50"


def _raw(raw: dict | None, name: str) -> str:
    """A CSV column from a Talabat raw row, whatever the export's header case."""
    for key, value in (raw or {}).items():
        if key.lower() == name.lower():
            return str(value or "").strip()
    return ""


async def _orders(db, channel: str, lo: str, hi: str) -> dict[str, AggregatorOrder]:
    rows = (
        await db.scalars(
            select(AggregatorOrder).where(
                AggregatorOrder.channel == channel,
                AggregatorOrder.business_date >= lo,
                AggregatorOrder.business_date <= hi,
            )
        )
    ).all()
    return {r.external_order_id: r for r in rows}


async def _session(channel: str):
    ingest._register_providers()
    provider = ingest.PROVIDERS[channel]
    async with SchedulerSessionFactory() as db:
        session = await ingest._session_for(db, channel, provider)
        await db.commit()
    return provider, session


def _window(lo: date, hi: date) -> tuple[datetime, datetime]:
    return (
        datetime(lo.year, lo.month, lo.day, tzinfo=DXB),
        datetime(hi.year, hi.month, hi.day, 23, 59, 59, tzinfo=DXB),
    )


# ── coverage ────────────────────────────────────────────────────────────────


async def coverage(lo: date, hi: date) -> dict:
    today = datetime.now(DXB).date()
    out: dict[str, Any] = {}
    async with SchedulerSessionFactory() as db:
        rows = (
            await db.execute(
                text(
                    """
                    select o.aggregator_channel ch, count(*) delivered,
                      count(*) filter (where o.aggregator_fee is not null) with_fee,
                      sum(o.total) gmv,
                      sum(o.total) filter (where o.aggregator_fee is null) gmv_pending,
                      min(o.business_date) filter (where o.aggregator_fee is null)
                        first_pending
                    from orders o
                    where o.source = 'aggregator' and o.status = 'delivered'
                      and o.business_date between :lo and :hi
                    group by 1 order by 1
                    """
                ),
                {"lo": lo.isoformat(), "hi": hi.isoformat()},
            )
        ).mappings()
        for r in rows:
            out[r["ch"]] = {k: str(v) if v is not None else None for k, v in r.items()}
        for channel in ingest._FEE_SETTLE_DAYS:
            gaps = await ingest.fee_gap_dates(db, channel, today)
            out.setdefault(channel, {})["gap_days_past_settle_lag"] = [
                d.isoformat() for d in gaps if lo <= d <= hi
            ]
    return out


# ── live: Talabat ───────────────────────────────────────────────────────────

_TALABAT_DETAIL_FIELDS = (
    "Cancellation owner",
    "Cancellation reason",
    "Complaint reason",
    "Operational charges",
    "Vendor refunds",
    "Amount owed back to Talabat",
    "Wait time fee",
    "Delivered at",
)


async def live_talabat(lo: date, hi: date) -> dict:
    provider, session = await _session("talabat")
    if session is None:
        return {"error": "session not live"}
    entity = provider._global_entity_id(session)
    async with SchedulerSessionFactory() as db:
        vendors = sorted(
            {
                v
                for v in (
                    await db.scalars(
                        select(AggregatorBranchMap.external_outlet_id).where(
                            AggregatorBranchMap.channel == "talabat"
                        )
                    )
                ).all()
                if v
            }
        )
        ours = await _orders(db, "talabat", lo.isoformat(), hi.isoformat())
    codes = [{"globalEntityId": entity, "vendorId": v} for v in vendors]
    # ListOrders rejects windows over a month; Dubai midnight is 20:00Z the day before.
    since = datetime(lo.year, lo.month, lo.day, tzinfo=DXB)
    until = datetime(hi.year, hi.month, hi.day, tzinfo=DXB) + timedelta(days=1)
    live, token = [], None
    for _ in range(80):
        pagination: dict[str, Any] = {"pageSize": 50}
        if token:
            pagination["pageToken"] = token
        data = await provider._graphql(
            session,
            endpoint=tp._FINANCE_GRAPHQL,
            query=tp._LIST_ORDERS_QUERY,
            variables={
                "params": {
                    "globalVendorCodes": codes,
                    "timeFrom": since.astimezone(ZoneInfo("UTC")).strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"
                    ),
                    "timeTo": until.astimezone(ZoneInfo("UTC")).strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"
                    ),
                    "pagination": pagination,
                }
            },
            operation_name="ListOrders",
        )
        listing = (data.get("orders") or {}).get("listOrders") or {}
        live += listing.get("orders") or []
        token = (listing.get("nextPageToken") or "").strip() or None
        if not token:
            break
    by_id = {str(r["orderId"]): r for r in live}
    report: dict[str, Any] = {
        "live_orders": len(by_id),
        "our_orders": len(ours),
        "missing_from_ours": sorted(set(by_id) - set(ours)),
        "missing_from_live": sorted(set(ours) - set(by_id)),
        "net_matches": 0,
        "net_differs": [],
        "billed_live_feeless_ours": [],
        "unbilled_live": 0,
    }
    for oid, row in by_id.items():
        agg = ours.get(oid)
        billing = row.get("billing") or {}
        live_net = _d(billing.get("netRevenue"))
        if agg is None:
            continue
        if live_net is None:
            report["unbilled_live"] += 1
            continue
        if agg.net_payable is None:
            report["billed_live_feeless_ours"].append(
                {"order": oid, "date": agg.business_date, "live_net": str(live_net)}
            )
        elif abs(live_net - agg.net_payable) > TOL:
            report["net_differs"].append(
                {
                    "order": oid,
                    "date": agg.business_date,
                    "live_net": str(live_net),
                    "our_net": str(agg.net_payable),
                    "live_subtotal": row.get("subtotal"),
                    "our_gross": str(agg.gross_sales),
                    "our_refund": str(agg.refund_amount),
                }
            )
        else:
            report["net_matches"] += 1
    return report


# ── live: Careem ────────────────────────────────────────────────────────────


async def live_careem(lo: date, hi: date) -> dict:
    provider, session = await _session("careem")
    if session is None:
        return {"error": "session not live"}
    since, until = _window(lo, hi)
    entries = await provider._billing_entries(session, since=since, until=until)
    _, fee_updates, note = provider._finance_from_entries(entries)
    async with SchedulerSessionFactory() as db:
        ours = await _orders(db, "careem", lo.isoformat(), hi.isoformat())
    billed = {u.external_order_id: u for u in fee_updates}
    diffs, feeless = [], []
    for oid, upd in billed.items():
        agg = ours.get(oid)
        if agg is None:
            continue
        if agg.net_payable is None:
            feeless.append({"order": oid, "date": agg.business_date})
            continue
        for name in (
            "commission_amount",
            "payment_fee",
            "marketing_fee",
            "net_payable",
        ):
            live_v, our_v = _d(getattr(upd, name)), getattr(agg, name)
            if live_v is not None and (our_v is None or abs(live_v - our_v) > TOL):
                diffs.append(
                    {
                        "order": oid,
                        "field": name,
                        "live": str(live_v),
                        "ours": str(our_v),
                    }
                )
    balance = None
    try:
        outlets = await provider.discover_outlets(session)
        balance = await provider.request_json(
            session,
            "POST",
            f"{careem_provider._API}/v1/billing/billingAccounts/earnings",
            json_body={
                "tenant": careem_provider._TENANT,
                "billingAccounts": provider._billing_accounts(outlets),
            },
        )
    except Exception as exc:  # noqa: BLE001 — the balance is a nice-to-have
        balance = {"error": repr(exc)[:200]}
    return {
        "billed_live": len(billed),
        "our_orders": len(ours),
        "ours_not_billed": sorted(
            (oid, a.business_date, a.status)
            for oid, a in ours.items()
            if oid not in billed
        ),
        "billed_live_feeless_ours": feeless,
        "fee_differs": diffs,
        "note": note,
        "balance_unpaid": (balance or {}).get("totalBalance"),
        "refunds_and_complaints": "not exposed by billingEntries (FOOD_ORDER only); "
        "check the portal's Refunds & complaints page by hand",
    }


# ── live: Deliveroo, noon (statement channels) ─────────────────────────────


async def _statement_check(channel: str, lo: date, hi: date) -> dict:
    provider, session = await _session(channel)
    if session is None:
        return {"error": "session not live"}
    since, until = _window(lo - timedelta(days=7), datetime.now(DXB).date())
    statements = [
        st
        for st in (
            await provider.fetch_statements(session, since=since, until=until)
        ).statements
        if (st.period_end or "") >= lo.isoformat()
    ]
    async with SchedulerSessionFactory() as db:
        ours = await _orders(
            db, channel, (lo - timedelta(days=7)).isoformat(), hi.isoformat()
        )
    # A statement line names the order by any of its ids: Deliveroo's invoice
    # uses the `drn_id` the sales feed keeps under `raw.detail`
    # (`ingest._order_matches_line_ids`).
    for agg in list(ours.values()):
        for alias in (
            agg.display_ref,
            ((agg.raw or {}).get("detail") or {}).get("drn_id"),
        ):
            if alias:
                ours.setdefault(str(alias), agg)
    per_order: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for st in statements:
        for line in st.lines or []:
            if line.external_order_id:
                key = (line.fee_category or line.line_type or "").lower()
                per_order[line.external_order_id][key] += _d(line.amount) or Decimal(0)
    net_keys = ("net_payable", "settlement", "payout")
    diffs, missing = [], []
    for oid, legs in per_order.items():
        agg = ours.get(oid)
        if agg is None:
            missing.append(oid)
            continue
        live_net = sum((v for k, v in legs.items() if k in net_keys), Decimal(0))
        if agg.net_payable is None or abs(live_net - agg.net_payable) > TOL:
            diffs.append(
                {
                    "order": oid,
                    "date": agg.business_date,
                    "status": agg.status,
                    "statement_net": str(live_net),
                    "our_net": str(agg.net_payable),
                }
            )
    return {
        "statements": [
            {
                "id": st.statement_id,
                "period": f"{st.period_start}..{st.period_end}",
                "net": str(st.net_payable),
                "lines": len(st.lines or []),
            }
            for st in statements
        ],
        "statement_orders": len(per_order),
        "statement_orders_not_ours": missing,
        "net_differs": diffs,
        "period_lines": [
            {
                "statement": st.statement_id,
                "category": line.fee_category,
                "amount": str(line.amount),
                "description": (line.description or "")[:160],
            }
            for st in statements
            for line in st.lines or []
            if not line.external_order_id
        ],
    }


async def live_deliveroo(lo: date, hi: date) -> dict:
    out = await _statement_check("deliveroo", lo, hi)
    if "error" in out:
        return out
    provider, session = await _session("deliveroo")
    async with SchedulerSessionFactory() as db:
        ours = await _orders(db, "deliveroo", lo.isoformat(), hi.isoformat())
    live: dict[str, Any] = {}
    day = lo
    # Deliveroo's order list is not paginated: one day at a time stays complete.
    while day <= hi:
        since, until = _window(day, day)
        for order in (
            await provider.fetch_sales(session, since=since, until=until)
        ).orders:
            live[order.external_order_id] = order
        day += timedelta(days=1)
    out["orders"] = {
        "live": len(live),
        "ours": len(ours),
        "missing_from_ours": sorted(set(live) - set(ours)),
        "missing_from_live": sorted(set(ours) - set(live)),
        "status_or_gross_differs": [
            {
                "order": oid,
                "live": [o.status, str(o.gross_sales)],
                "ours": [ours[oid].status, str(ours[oid].gross_sales)],
            }
            for oid, o in live.items()
            if oid in ours
            and (
                (o.status or "") != (ours[oid].status or "")
                or (o.gross_sales or 0) != (ours[oid].gross_sales or 0)
            )
        ],
    }
    return out


async def live_noon(lo: date, hi: date) -> dict:
    return await _statement_check("noon", lo, hi)


# ── Keeta: bills against the order feed (no httpx session) ─────────────────


async def keeta_bills(lo: date, hi: date) -> dict:
    async with SchedulerSessionFactory() as db:
        ours = await _orders(db, "keeta", lo.isoformat(), hi.isoformat())
        lines = (
            await db.scalars(
                select(AggregatorStatementLine).where(
                    AggregatorStatementLine.channel == "keeta",
                    AggregatorStatementLine.external_order_id.in_(list(ours)),
                )
            )
        ).all()
        bills = (
            await db.scalars(
                select(AggregatorStatement).where(
                    AggregatorStatement.channel == "keeta",
                    AggregatorStatement.period_end >= lo.isoformat(),
                    AggregatorStatement.period_start <= hi.isoformat(),
                )
            )
        ).all()
    legs: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for line in lines:
        legs[line.external_order_id][(line.line_type or "").lower()] += line.amount or 0
    diffs = []
    for oid, leg in legs.items():
        agg = ours[oid]
        if _cancelled(agg.status):
            continue
        bill_gross, bill_comm = (
            leg.get("sales", Decimal(0)),
            -leg.get("commission", Decimal(0)),
        )
        if (agg.gross_sales or 0) - (agg.refund_amount or 0) != bill_gross or abs(
            (agg.commission_amount or 0) - bill_comm
        ) > TOL:
            diffs.append(
                {
                    "order": oid,
                    "date": agg.business_date,
                    "ours": [
                        str(agg.gross_sales),
                        str(agg.refund_amount),
                        str(agg.commission_amount),
                    ],
                    "bill": [str(bill_gross), str(bill_comm)],
                }
            )
    covered_to = max((b.period_end for b in bills), default=None)
    return {
        "bills": len(bills),
        "billed_through": covered_to,
        "orders_on_bills": len(legs),
        "sold_orders_not_on_bills_through_cover": sorted(
            oid
            for oid, a in ours.items()
            if oid not in legs
            and not _cancelled(a.status)
            and covered_to
            and a.business_date <= covered_to
        ),
        "gross_or_commission_differs": diffs,
        "note": "Keeta bills exist only for ranges someone requested in the portal; "
        "a cycle with no bill needs a request (Finance > Download) first.",
    }


# ── cancellations & refunds, with fault ─────────────────────────────────────


def _keeta_responsible(raw: dict) -> str | None:
    for trace in (raw or {}).get("merchantOrderTraces") or []:
        if isinstance(trace, dict) and str(trace.get("opType")) == "30":
            ext = trace.get("commonExt")
            if isinstance(ext, dict) and ext.get("responsible") is not None:
                return str(ext["responsible"])
    return None


def _fault(agg: AggregatorOrder, reason: str | None) -> str:
    raw = agg.raw or {}
    if agg.channel == "talabat":
        owner = _raw(raw, "Cancellation owner").lower()
        if owner:
            return {
                "vendor": "restaurant",
                "rider": "platform (rider)",
                "transport": "platform (rider)",
                "customer": "customer",
                "platform": "platform",
            }.get(owner, owner)
        if _raw(raw, "Operational charges") not in ("", "0.00", "0"):
            return "restaurant (complaint refund)"
        if _raw(raw, "Vendor refunds") not in ("", "0.00", "0"):
            return "platform-funded refund"
    if agg.channel == "keeta":
        resp = _keeta_responsible(raw)
        if resp in ("50", "60"):
            return "platform (Keeta desk, responsible " + resp + ")"
        if resp == "20":
            return "restaurant/customer (Keeta desk, responsible 20)"
        return {"Merchant": "restaurant", "User": "customer"}.get(
            reason or "", reason or "unknown"
        )
    if agg.channel == "noon":
        if agg.refund_amount:
            return "restaurant (outlet adjustment)"
        # noon charges its 2% cancellation fee only on an outlet-caused cancel.
        if agg.cancellation_fee:
            return "restaurant (noon charged the cancellation fee)"
        return "not charged (noon/customer)" if agg.statement_id else "unbilled yet"
    if agg.channel == "deliveroo":
        return "restaurant" if (agg.net_payable or 0) < 0 else "not charged"
    return "unknown (no fault data)"


async def cancellations(lo: date, hi: date) -> list[dict]:
    async with SchedulerSessionFactory() as db:
        rows = (
            (
                await db.execute(
                    text(
                        """
                    select a.id, o.order_number, o.aggregator_cancel_reason,
                           o.marketplace_cancellation_net, o.marketplace_cancellation_provisional,
                           o.refunded_amount, o.status::text ostatus
                    from aggregator_order a left join orders o on o.id = a.mm_order_id
                    where a.business_date between :lo and :hi
                      and (lower(a.status) in ('cancelled','canceled','rejected','failed','declined')
                           or a.status = '50' or coalesce(a.refund_amount, 0) <> 0
                           or o.status = 'cancelled' or coalesce(o.refunded_amount, 0) <> 0)
                    """
                    ),
                    {"lo": lo.isoformat(), "hi": hi.isoformat()},
                )
            )
            .mappings()
            .all()
        )
        out = []
        for r in rows:
            agg = await db.get(AggregatorOrder, r["id"])
            reason = r["aggregator_cancel_reason"]
            if agg.channel == "talabat":
                reason = (
                    f"{_raw(agg.raw, 'Cancellation reason')} "
                    f"{_raw(agg.raw, 'Complaint reason')}"
                ).strip() or reason
            out.append(
                {
                    "channel": agg.channel,
                    "order": agg.external_order_id,
                    "mm": r["order_number"],
                    "date": agg.business_date,
                    "status": [agg.status, r["ostatus"]],
                    "gross": str(agg.gross_sales),
                    "refund": str(agg.refund_amount) if agg.refund_amount else None,
                    "net": str(agg.net_payable)
                    if agg.net_payable is not None
                    else None,
                    "settled": bool(agg.statement_id),
                    "mm_cancellation_net": str(r["marketplace_cancellation_net"])
                    if r["marketplace_cancellation_net"] is not None
                    else None,
                    "provisional": r["marketplace_cancellation_provisional"],
                    "reason": reason,
                    "fault": _fault(agg, reason),
                    "talabat_detail": {
                        f: _raw(agg.raw, f)
                        for f in _TALABAT_DETAIL_FIELDS
                        if _raw(agg.raw, f) not in ("", "0.00", "0")
                    }
                    if agg.channel == "talabat"
                    else None,
                }
            )
    return sorted(out, key=lambda x: (x["channel"], x["date"] or ""))


# ── payouts ────────────────────────────────────────────────────────────────


async def payouts(lo: date, hi: date) -> dict:
    async with SchedulerSessionFactory() as db:
        paid = (
            await db.scalars(
                select(AggregatorPayout).where(
                    AggregatorPayout.transfer_date >= lo.isoformat()
                )
            )
        ).all()
        talabat_invoices = (
            await db.scalars(
                select(AggregatorStatement).where(
                    AggregatorStatement.channel == "talabat",
                    AggregatorStatement.statement_id.op("~")("^[0-9]+$"),
                    AggregatorStatement.period_end >= lo.isoformat(),
                    AggregatorStatement.period_start <= hi.isoformat(),
                )
            )
        ).all()
        talabat = await _orders(db, "talabat", lo.isoformat(), hi.isoformat())
    periods: dict[tuple, Decimal] = defaultdict(Decimal)
    for inv in talabat_invoices:
        periods[(inv.period_start, inv.period_end)] += inv.net_payable or 0
    tie = []
    for (start, end), invoiced in sorted(periods.items()):
        orders_net = sum(
            (
                a.net_payable or 0
                for a in talabat.values()
                if start <= (a.business_date or "") <= end
            ),
            Decimal(0),
        )
        tie.append(
            {
                "period": f"{start}..{end}",
                "invoiced": str(invoiced),
                "orders_net": str(orders_net),
                "gap": str(invoiced - orders_net),
            }
        )
    by_channel: dict[str, list] = defaultdict(list)
    for p in paid:
        by_channel[p.channel].append(
            {
                "date": p.transfer_date,
                "amount": str(p.transfer_amount),
                "status": p.transfer_status,
            }
        )
    last = {ch: max(x["date"] for x in v) for ch, v in by_channel.items()}
    return {
        "received": by_channel,
        "last_payout": last,
        "talabat_invoice_tie_out": tie,
    }


# ── P&L ─────────────────────────────────────────────────────────────────────


async def pnl(lo: date, hi: date) -> dict:
    async with SchedulerSessionFactory() as db:
        await order_pnl.without_jit(db)
        report = await pnl_report.build(
            db, date_from=lo.isoformat(), date_to=hi.isoformat()
        )
        await db.rollback()
    out: dict[str, Any] = {}
    for code, totals in report.channels + [("TOTAL", report.total)]:
        row = {k: str(v) for k, v in dataclasses.asdict(totals).items()}
        for prop in ("net_revenue", "pc1", "pc2", "pc3", "pc4"):
            row[prop] = str(getattr(totals, prop))
        out[code] = row
    out["_period_charges"] = [
        {k: str(v) for k, v in dataclasses.asdict(c).items()}
        for c in report.period_charges
    ]
    return out


# ── main ────────────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--to", help="last business date, YYYY-MM-DD")
    parser.add_argument(
        "--skip",
        default="",
        help="comma-separated sections to skip (live_talabat,live_deliveroo,...)",
    )
    args = parser.parse_args()
    year, month = (int(x) for x in args.month.split("-"))
    lo = date(year, month, 1)
    hi = date(year, month, calendar.monthrange(year, month)[1])
    yesterday = datetime.now(DXB).date() - timedelta(days=1)
    hi = min(hi, yesterday, date.fromisoformat(args.to) if args.to else hi)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    sections = {
        "coverage": coverage,
        "live_talabat": live_talabat,
        "live_careem": live_careem,
        "live_deliveroo": live_deliveroo,
        "live_noon": live_noon,
        "keeta_bills": keeta_bills,
        "cancellations": cancellations,
        "payouts": payouts,
        "pnl": pnl,
    }
    report: dict[str, Any] = {"window": [lo.isoformat(), hi.isoformat()]}
    for name, fn in sections.items():
        if name in skip:
            continue
        try:
            report[name] = await fn(lo, hi)
        except Exception as exc:  # noqa: BLE001 — one section must not sink the rest
            report[name] = {"error": repr(exc)[:500]}
        print(f"done {name}", file=sys.stderr, flush=True)
    print("JSON" + json.dumps(report, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
