"""Layer A: the per-period sales↔statement↔payout rollup, read-only.

Where `reconcile.py` (Layer B) checks one aggregator order against the MM order
it became, this service reconciles the *settlement* leg for a whole period, on
one screen: for each published statement, the sales side (the orders that
settled on it) against the settlement side (its order-grain lines and its own
declared net payable), and then — one rung up — the payout side, because a
marketplace pays in batches and one transfer clears several statements at once
(a noon payout of 8328.29 is exactly 5046.48 + 3281.81 of the two statements due
before it). The chain read here is: payout ← statement ← line ← order.

Nothing here writes. It only reads what the ingest already coupled:
`aggregator_order.statement_id` (set when a statement publishes),
`aggregator_statement.payout_transfer_id` (set by
`ingest.link_statements_to_payouts`), and the order-grain statement lines.

Money is Decimal throughout; null means *unknown* and is never silently a zero.
A statement with no declared `net_payable` — talabat's per-invoice-file rows —
reports a null total and is flagged `no_statement_total`, so the "settled vs
statement" check reads as "cannot compare", not as a false variance. The heavy
lifting is grouped aggregates in SQL (`GROUP BY statement_id`), not per-row
Python; the pure builders below take those already-summed rows so the variance
arithmetic is unit-testable without a database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aggregator import (
    STATEMENT_GRAIN_ORDER,
    AggregatorOrder,
    AggregatorPayout,
    AggregatorStatement,
    AggregatorStatementLine,
)

#: Same 1-fil tolerance the per-order reconciler flags at, so a variance shown
#: here agrees with what Layer B would raise on the same figures.
_TOL = Decimal("0.01")


def _d(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _sum_or_none(values) -> Decimal | None:
    """Sum of the non-null values, or None when there is nothing to sum.

    Null is unknown: a group of all-null (or empty) contributes no total rather
    than a misleading 0, which would read as "settled to nothing"."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present, Decimal(0))


@dataclass(frozen=True)
class PayoutInfo:
    """The transfer that settled a statement, as much as we know of it."""

    transfer_id: str
    transfer_amount: Decimal | None
    transfer_date: str | None
    transfer_status: str | None


@dataclass(frozen=True)
class StatementRecon:
    """One statement reconciled across sales, settlement and payout."""

    channel: str
    statement_id: str
    period_start: str | None
    period_end: str | None
    payment_due_date: str | None
    currency: str | None
    #: Sales side — Σ aggregator_order.net_payable for orders on this statement.
    sales_total: Decimal | None
    #: Settlement side — Σ order-grain statement_line.amount for this statement.
    settled_total: Decimal | None
    #: The statement's own declared net payable (null for talabat file-rows).
    statement_net_payable: Decimal | None
    orders_count: int
    lines_count: int
    orders_promoted: int
    payout_transfer_id: str | None
    payout: PayoutInfo | None
    sales_vs_settled: Decimal | None
    sales_vs_settled_flag: bool
    settled_vs_statement: Decimal | None
    settled_vs_statement_flag: bool
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PayoutRollup:
    """One transfer against the statements it settled — the batch-payout check.

    `statements_net_total` is Σ of the member statements' declared net payable;
    `variance` is `transfer_amount − statements_net_total`, the number that is
    ~0 when a payout exactly clears its batch (8328.29 vs 5046.48 + 3281.81).
    """

    channel: str
    transfer_id: str
    transfer_amount: Decimal | None
    transfer_date: str | None
    transfer_status: str | None
    statement_ids: list[str]
    statements_count: int
    statements_net_total: Decimal | None
    variance: Decimal | None
    variance_flag: bool
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SettlementReconResult:
    """The whole read: per-statement rows plus the per-payout rollup."""

    channel: str
    from_date: str | None
    to_date: str | None
    statements: list[StatementRecon]
    payouts: list[PayoutRollup]


def build_statement_recon(
    *,
    channel: str,
    statement,
    sales_total,
    orders_count: int,
    orders_promoted: int,
    settled_total,
    lines_count: int,
    payout: PayoutInfo | None,
) -> StatementRecon:
    """Pure: assemble one statement's row and its two variance flags.

    Takes already-summed sales/settled totals (Decimal or None) so it can be
    exercised without a DB. Every comparison is guarded on both sides being
    known — an unknown side yields a null variance and no flag, never a 0."""
    sales = _d(sales_total)
    settled = _d(settled_total)
    stmt_net = _d(statement.net_payable)
    flags: list[str] = []

    if sales is not None and settled is not None:
        sales_vs_settled = sales - settled
        sales_flag = abs(sales_vs_settled) > _TOL
    else:
        sales_vs_settled = None
        sales_flag = False
    if sales_flag:
        flags.append("sales_vs_settled_variance")

    if stmt_net is None:
        # No declared statement total to compare against — a fact, not a variance.
        settled_vs_statement = None
        settled_flag = False
        flags.append("no_statement_total")
    elif settled is not None:
        settled_vs_statement = settled - stmt_net
        settled_flag = abs(settled_vs_statement) > _TOL
        if settled_flag:
            flags.append("settled_vs_statement_variance")
    else:
        settled_vs_statement = None
        settled_flag = False

    if statement.payout_transfer_id is None:
        flags.append("no_payout_linked")

    return StatementRecon(
        channel=channel,
        statement_id=statement.statement_id,
        period_start=statement.period_start,
        period_end=statement.period_end,
        payment_due_date=statement.payment_due_date,
        currency=statement.currency,
        sales_total=sales,
        settled_total=settled,
        statement_net_payable=stmt_net,
        orders_count=orders_count,
        lines_count=lines_count,
        orders_promoted=orders_promoted,
        payout_transfer_id=statement.payout_transfer_id,
        payout=payout,
        sales_vs_settled=sales_vs_settled,
        sales_vs_settled_flag=sales_flag,
        settled_vs_statement=settled_vs_statement,
        settled_vs_statement_flag=settled_flag,
        flags=flags,
    )


def build_payout_rollups(
    channel: str,
    statement_recons: list[StatementRecon],
    payouts_by_id: dict[str, PayoutInfo],
) -> list[PayoutRollup]:
    """Pure: group statements by the payout that settled them and check the sum.

    This is the "one transfer clears several statements" check: for each
    `transfer_id`, sum the member statements' declared net payable and compare
    to the transfer amount. A missing statement total or a missing payout makes
    the comparison unknown (null variance, no false flag) rather than a 0."""
    groups: dict[str, list[StatementRecon]] = defaultdict(list)
    for s in statement_recons:
        if s.payout_transfer_id:
            groups[s.payout_transfer_id].append(s)

    rollups: list[PayoutRollup] = []
    for transfer_id, members in groups.items():
        payout = payouts_by_id.get(transfer_id)
        nets = [m.statement_net_payable for m in members]
        stmt_net_total = _sum_or_none(nets)
        transfer_amount = payout.transfer_amount if payout else None

        flags: list[str] = []
        if payout is None:
            flags.append("payout_missing")
        if any(n is None for n in nets):
            flags.append("statement_total_missing")

        if transfer_amount is not None and stmt_net_total is not None:
            variance = transfer_amount - stmt_net_total
            variance_flag = abs(variance) > _TOL
            if variance_flag:
                flags.append("payout_vs_statements_variance")
        else:
            variance = None
            variance_flag = False

        rollups.append(
            PayoutRollup(
                channel=channel,
                transfer_id=transfer_id,
                transfer_amount=transfer_amount,
                transfer_date=payout.transfer_date if payout else None,
                transfer_status=payout.transfer_status if payout else None,
                statement_ids=sorted(m.statement_id for m in members),
                statements_count=len(members),
                statements_net_total=stmt_net_total,
                variance=variance,
                variance_flag=variance_flag,
                flags=flags,
            )
        )
    return sorted(rollups, key=lambda r: (r.transfer_date or "", r.transfer_id))


async def settlement_reconciliation(
    db: AsyncSession,
    channel: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> SettlementReconResult:
    """Read-only settlement rollup for one channel over a date window.

    The window is applied to the statement period (ISO strings compare
    lexicographically): a statement is included when its period overlaps
    `[from_date, to_date]`. Order and line totals are grouped in SQL, one query
    each, then joined to the statements in Python. Writes nothing.
    """
    stmt_tbl = AggregatorStatement
    stmt_q = select(stmt_tbl).where(stmt_tbl.channel == channel)
    if from_date:
        stmt_q = stmt_q.where(
            or_(stmt_tbl.period_end >= from_date, stmt_tbl.period_end.is_(None))
        )
    if to_date:
        stmt_q = stmt_q.where(
            or_(stmt_tbl.period_start <= to_date, stmt_tbl.period_start.is_(None))
        )
    stmt_q = stmt_q.order_by(stmt_tbl.period_start.nullslast(), stmt_tbl.statement_id)
    statements = list(await db.scalars(stmt_q))
    if not statements:
        return SettlementReconResult(channel, from_date, to_date, [], [])

    statement_ids = [s.statement_id for s in statements]

    # Sales side, grouped: Σ net_payable, order count, promoted count per statement.
    order_tbl = AggregatorOrder
    order_rows = (
        await db.execute(
            select(
                order_tbl.statement_id,
                func.sum(order_tbl.net_payable),
                func.count(),
                func.count(order_tbl.mm_order_id),
            )
            .where(
                order_tbl.channel == channel,
                order_tbl.statement_id.in_(statement_ids),
            )
            .group_by(order_tbl.statement_id)
        )
    ).all()
    order_agg = {sid: (net, cnt, promoted) for sid, net, cnt, promoted in order_rows}

    # Settlement side, grouped: Σ amount and line count over order-grain lines.
    line_tbl = AggregatorStatementLine
    line_rows = (
        await db.execute(
            select(line_tbl.statement_id, func.sum(line_tbl.amount), func.count())
            .where(
                line_tbl.channel == channel,
                line_tbl.statement_id.in_(statement_ids),
                line_tbl.grain == STATEMENT_GRAIN_ORDER,
            )
            .group_by(line_tbl.statement_id)
        )
    ).all()
    line_agg = {sid: (amt, cnt) for sid, amt, cnt in line_rows}

    # Payout side: only the transfers these statements name.
    transfer_ids = {s.payout_transfer_id for s in statements if s.payout_transfer_id}
    payouts_by_id: dict[str, PayoutInfo] = {}
    if transfer_ids:
        payout_tbl = AggregatorPayout
        for p in await db.scalars(
            select(payout_tbl).where(
                payout_tbl.channel == channel,
                payout_tbl.transfer_id.in_(transfer_ids),
            )
        ):
            payouts_by_id[p.transfer_id] = PayoutInfo(
                transfer_id=p.transfer_id,
                transfer_amount=_d(p.transfer_amount),
                transfer_date=p.transfer_date,
                transfer_status=p.transfer_status,
            )

    recons = []
    for s in statements:
        net, cnt, promoted = order_agg.get(s.statement_id, (None, 0, 0))
        amt, lcnt = line_agg.get(s.statement_id, (None, 0))
        payout = (
            payouts_by_id.get(s.payout_transfer_id) if s.payout_transfer_id else None
        )
        recons.append(
            build_statement_recon(
                channel=channel,
                statement=s,
                sales_total=net,
                orders_count=cnt,
                orders_promoted=promoted,
                settled_total=amt,
                lines_count=lcnt,
                payout=payout,
            )
        )

    rollups = build_payout_rollups(channel, recons, payouts_by_id)
    return SettlementReconResult(channel, from_date, to_date, recons, rollups)
