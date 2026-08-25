"""The daily sales email: one spreadsheet, every branch, every channel.

A branch owner does not open the manager app each morning; a mail with the
numbers already in it is read. This builds that mail — a single `.xlsx` whose
five stacked blocks (Sales Revenue, Order Count, Sales Discount, Charges, Net
Revenue) each carry one row per branch and one column per channel — and sends it
after the last branch has closed for the day.

Money basis, decided with the owner: **what the customer paid, VAT included.**
Sales Revenue is the order total; Net Revenue is that less our costs and any
refund — the same figure `order_economics.net` computes per order. Charges is
those costs summed with no breakdown: the marketplace's commission, the payment
fee, and — on a website order — what the courier cost us. Discount is shown for
information, since it is already inside the total.

Only **delivered** trade counts: a delivered marketplace or website order, or a
closed counter check. Anything cancelled or still in progress is left out — a
sales report is a record of money taken, not orders opened.

The loop belongs to the app because this stack has no cron, and it holds an
advisory lock so a second copy achieves nothing — the same shape as
`log_retention` and the batch dispatcher next to it in the lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock
from app.core.database import AsyncSessionFactory
from app.models.branch import Branch
from app.models.email_log import EmailLog
from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.pos_order import PosOrderStatusEnum
from app.services import email_service
from app.services.couriers import courier_catalog
from app.services.pos import business_day_service

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 5.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4805

#: How long after the last branch closes before the mail goes out — enough for a
#: straggler delivery webhook to land without making anyone wait for it.
_CLOSE_BUFFER = timedelta(minutes=15)

_TICK_SECONDS = 600

#: Where the automatic send goes. The manual admin trigger passes its own list.
DEFAULT_RECIPIENTS = ["h_abbasi97@hotmail.com", "fahimakhtarabbasi@gmail.com"]

_TEMPLATE = "daily_sales_report"

#: The channel columns, in report order. The five aggregators are the courier
#: catalogue's own codes; website folds every courier into one column and
#: counter is the till. A marketplace nobody has mapped yet still gets a column
#: (appended) rather than having its money silently dropped.
_AGGREGATOR_COLUMNS = ["keeta", "noon_food", "talabat", "careem", "deliveroo"]
_FIXED_COLUMNS = _AGGREGATOR_COLUMNS + ["website", "counter"]
_COLUMN_LABELS = {
    "keeta": "keeta",
    "noon_food": "noon food",
    "talabat": "talabat",
    "careem": "careem",
    "deliveroo": "deliveroo",
    "website": "website",
    "counter": "counter",
}


def _label(column: str) -> str:
    return _COLUMN_LABELS.get(column, column)


#: Delivered trade only. A counter check is done when the till closes it; a
#: marketplace or website order when it is delivered. Cancelled, refunded and
#: in-progress orders never match either arm.
_DELIVERED = or_(
    and_(
        Order.source == "cashier", Order.pos_status == PosOrderStatusEnum.CLOSED.value
    ),
    and_(
        Order.source.in_(["aggregator", "online"]),
        Order.status == OrderStatusEnum.DELIVERED.value,
    ),
)


@dataclass
class Cell:
    """One (branch, channel) intersection, before the sections read it."""

    revenue: Decimal = _ZERO  # order total, VAT included
    discount: Decimal = _ZERO
    charges: Decimal = _ZERO  # commission + payment fee + courier cost, our costs
    refunds: Decimal = _ZERO
    count: int = 0

    @property
    def net(self) -> Decimal:
        return self.revenue - self.charges - self.refunds


@dataclass
class ReportRow:
    business_date: str
    branch_name: str
    cells: dict[str, Cell]


@dataclass
class DailySalesReport:
    date_from: str
    date_to: str
    columns: list[str]
    rows: list[ReportRow] = field(default_factory=list)


#: Each block in the sheet, and how it reads a cell. Discount and Charges are
#: shown negative, the way the owner's mock-up does — money leaving the till.
_SECTIONS: list[tuple[str, "callable[[Cell], Decimal | int]"]] = [
    ("Sales Revenue", lambda c: c.revenue),
    ("Order Count", lambda c: c.count),
    ("Sales Discount", lambda c: -c.discount),
    ("Charges", lambda c: -c.charges),
    ("Net Revenue", lambda c: c.net),
]


def _column_for(source: str | None, aggregator_channel: str | None) -> str | None:
    if source == "cashier":
        return "counter"
    if source == "online":
        return "website"
    if source == "aggregator":
        code = courier_catalog.code_for_channel(aggregator_channel or "")
        return code or (aggregator_channel or "unknown")
    return None


async def _fetch(
    db: AsyncSession, date_from: str, date_to: str, branch_id=None
) -> list:
    # `cost_total` is what the courier actually billed; `quoted_cost` stands in
    # until it lands — the same order `order_economics` prefers them in. The
    # delivery join cannot fan out: `order_deliveries` is unique on order_id.
    courier_cost = func.coalesce(OrderDelivery.cost_total, OrderDelivery.quoted_cost)
    stmt = (
        select(
            Order.business_date,
            Order.branch_id,
            Order.source,
            Order.aggregator_channel,
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
            func.coalesce(func.sum(Order.discount_amount), 0),
            func.coalesce(func.sum(func.coalesce(Order.aggregator_fee, 0)), 0),
            func.coalesce(func.sum(func.coalesce(Order.payment_fee, 0)), 0),
            func.coalesce(func.sum(func.coalesce(courier_cost, 0)), 0),
            func.coalesce(func.sum(func.coalesce(Order.refunded_amount, 0)), 0),
        )
        .select_from(Order)
        .outerjoin(OrderDelivery, OrderDelivery.order_id == Order.id)
        .where(Order.is_pos.is_(True))
        .where(Order.business_date >= date_from, Order.business_date <= date_to)
        .where(_DELIVERED)
        .group_by(
            Order.business_date, Order.branch_id, Order.source, Order.aggregator_channel
        )
    )
    if branch_id is not None:
        stmt = stmt.where(Order.branch_id == branch_id)
    return (await db.execute(stmt)).all()


async def build(
    db: AsyncSession, *, date_from: str, date_to: str, branch_id=None
) -> DailySalesReport:
    """Assemble the matrix: one row per (trading day, branch), zero-filled."""
    raw = await _fetch(db, date_from, date_to, branch_id)

    branches = (
        (await db.execute(select(Branch).where(Branch.is_active.is_(True))))
        .scalars()
        .all()
    )
    if branch_id is not None:
        branches = [b for b in branches if b.id == branch_id]
    branch_name: dict = {b.id: b.name for b in branches}

    matrix: dict[tuple[str, object], dict[str, Cell]] = {}
    extra_columns: list[str] = []
    dates_present: set[str] = set()

    for (
        bdate,
        bid,
        source,
        channel,
        cnt,
        revenue,
        discount,
        agg_fee,
        pay_fee,
        cour_cost,
        refunds,
    ) in raw:
        col = _column_for(source, channel)
        if col is None:
            continue
        if col not in _FIXED_COLUMNS and col not in extra_columns:
            extra_columns.append(col)
        # A branch that traded but is no longer active still owns its money.
        branch_name.setdefault(bid, "(inactive branch)")
        dates_present.add(bdate)
        cell = matrix.setdefault((bdate, bid), {}).setdefault(col, Cell())
        cell.revenue += revenue
        cell.discount += discount
        cell.charges += agg_fee + pay_fee + cour_cost
        cell.refunds += refunds
        cell.count += int(cnt or 0)

    columns = _FIXED_COLUMNS + extra_columns

    # Days that traded, oldest first; the requested date itself if none did, so
    # a quiet day still produces a report that says so rather than an empty file.
    dates = sorted(dates_present) or [date_from]
    ordered_bids = sorted(branch_name, key=lambda b: branch_name[b].lower())

    rows: list[ReportRow] = []
    for d in dates:
        for bid in ordered_bids:
            cells = matrix.get((d, bid), {})
            rows.append(
                ReportRow(
                    business_date=d,
                    branch_name=branch_name[bid],
                    cells={c: cells.get(c, Cell()) for c in columns},
                )
            )

    return DailySalesReport(
        date_from=date_from, date_to=date_to, columns=columns, rows=rows
    )


def _num(value: Decimal | int) -> float | int:
    if isinstance(value, int):
        return value
    return float(round(value, 2))


def to_xlsx(report: DailySalesReport) -> bytes:
    """Render the report to a single-sheet workbook, sections stacked."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Sales"
    bold = Font(bold=True)

    r = 1
    for title, metric in _SECTIONS:
        ws.cell(row=r, column=1, value=title).font = bold
        r += 1
        header = ["date", "branch"] + [_label(c) for c in report.columns]
        for ci, text in enumerate(header, start=1):
            ws.cell(row=r, column=ci, value=text).font = bold
        r += 1
        for row in report.rows:
            ws.cell(row=r, column=1, value=row.business_date)
            ws.cell(row=r, column=2, value=row.branch_name)
            for ci, col in enumerate(report.columns, start=3):
                ws.cell(row=r, column=ci, value=_num(metric(row.cells[col])))
            r += 1
        r += 1  # a blank row between blocks, as in the owner's layout

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 16

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _body_html(label: str, row_count: int) -> str:
    return (
        f"<p>Daily sales for <strong>{label}</strong> is attached as a "
        f"spreadsheet.</p><p>{row_count} branch-day rows across five sections: "
        f"Sales Revenue, Order Count, Sales Discount, Charges, and Net Revenue. "
        f"Delivered trade only.</p>"
    )


async def send(
    db: AsyncSession, *, date_from: str, date_to: str, recipients: list[str]
) -> dict:
    """Build the report and mail it, once per recipient, journalled."""
    report = await build(db, date_from=date_from, date_to=date_to)
    xlsx = to_xlsx(report)

    single = date_from == date_to
    label = date_from if single else f"{date_from} to {date_to}"
    subject = f"Melting Moments — Daily sales {label}"
    filename = f"daily-sales-{date_from}{'' if single else '_' + date_to}.xlsx"
    html = _body_html(label, len(report.rows))

    outcomes = []
    for recipient in recipients:
        result = await email_service.send_with_attachment(
            recipient,
            subject,
            html,
            filename=filename,
            content=xlsx,
            template=_TEMPLATE,
        )
        outcomes.append(
            {
                "recipient": recipient,
                "status": result["status"],
                "error": result.get("error"),
            }
        )
    return {"subject": subject, "rows": len(report.rows), "sent": outcomes}


# ── The daily loop ───────────────────────────────────────────────────────────


#: How many recently-ended business days a tick will still send if they were
#: missed. The report used to be sendable only in the ~45-minute slice between the
#: last branch's close and local midnight, with no catch-up: a deploy, a restart,
#: or a single slow tick in that window lost the night permanently, and a branch
#: closing at or past midnight poisoned the window for the whole estate. Now a
#: tick sends any *completed* business day still unsent within this look-back, so
#: a missed night is caught up on the next tick rather than lost. Three days
#: covers a weekend outage without ever backfilling ancient history (the
#: `_already_sent` guard skips anything already mailed).
_CATCHUP_DAYS = 3


async def _already_sent(db: AsyncSession, business_date: str) -> bool:
    """Whether a report for this date already went — the subject carries it."""
    count = (
        await db.execute(
            select(func.count(EmailLog.id)).where(
                EmailLog.template == _TEMPLATE,
                EmailLog.subject.like(f"%{business_date}%"),
                EmailLog.status == "sent",
            )
        )
    ).scalar_one()
    return count > 0


async def _tick(db: AsyncSession, now: datetime | None = None) -> None:
    tz = await business_day_service.resolve_timezone(db)
    now = now or datetime.now(tz)

    branches = (
        (await db.execute(select(Branch).where(Branch.is_active.is_(True))))
        .scalars()
        .all()
    )
    if not branches:
        return

    # The earliest business day still in progress across all branches, measured
    # `_CLOSE_BUFFER` in the past so a day only counts as ended once its rollover
    # is comfortably behind us. Any date strictly before this has closed for every
    # branch — no `opening_to`/midnight arithmetic, so a past-midnight closer no
    # longer poisons the run. `business_date_for` already applies each branch's own
    # cut-off, which is the same boundary orders book under.
    ref = now - _CLOSE_BUFFER
    frontier = min(business_day_service.business_date_for(b, ref, tz) for b in branches)
    frontier_date = date.fromisoformat(frontier)

    # Every completed day in the look-back, oldest first, that has not been mailed.
    targets = [
        (frontier_date - timedelta(days=offset)).isoformat()
        for offset in range(_CATCHUP_DAYS, 0, -1)
    ]
    pending = [t for t in targets if not await _already_sent(db, t)]
    if not pending:
        return

    async with advisory_lock.held(
        _ADVISORY_LOCK_KEY, name="daily sales report"
    ) as mine:
        if not mine:
            return
        for business_date in pending:
            # Re-check under the lock: another worker may have sent it in the gap.
            if await _already_sent(db, business_date):
                continue
            summary = await send(
                db,
                date_from=business_date,
                date_to=business_date,
                recipients=DEFAULT_RECIPIENTS,
            )
            logger.info(
                "Daily sales report sent for %s: %s", business_date, summary["sent"]
            )


async def run_forever() -> None:
    """Check every ten minutes; send each completed business day once, catching
    up any recent day a restart or outage caused to be missed."""
    logger.info("Daily sales report loop started")
    while True:
        try:
            async with AsyncSessionFactory() as db:
                await _tick(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Daily sales report tick failed")
        await asyncio.sleep(_TICK_SECONDS)
