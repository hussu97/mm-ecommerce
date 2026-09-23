"""
Profit and loss, per order and summed over a window — one definition for both.

The shop asked for three contribution margins on every order and on any slice of
the book (a date range, a channel, a branch):

    GMV (pre-discount, incl. VAT)   the goods the customer was billed for
  − Refunds                         partial refunds on an order that still stood
  − VAT on sales                    the output VAT owed to the FTA
  = Net revenue
  − COGS (net of VAT)               the FIFO ingredient + packaging cost consumed
  = PC1
  + Delivery fees                   delivery + small-basket fees charged (no VAT)
  − Payment fees                    card processor / marketplace payment handling
  − Aggregator & delivery fees      commission, loyalty/Pro/Plus/subsidy, own courier
  − Misc fees                       cancellation charges (+ period charges, report only)
  + VAT reclaimed on fees
  = PC2
  − Discounts                       coupon / counter / marketplace merchant discounts
  = PC3

**Amounts as billed, VAT as its own lines.** Revenue and fees are shown
VAT-inclusive, so each matches a receipt, an invoice or a marketplace statement,
and their VAT comes off in lines of its own: output VAT from the order's frozen
`vat_amount` (less the VAT inside any refund), and input VAT reclaimed on fees —
both flows of the period the order falls in. Fee VAT is reclaimed only under a
VAT-registered entity (the Barsha counter is not, so there it stays a cost) —
the same split, at the same rate, that `services/vat_ledger` books.

**COGS is net cost, with no VAT line.** The VAT on the stock was reclaimed in
the return for the period it was bought (the VAT report's raw goods), not at
the sale, and recoverable VAT is never part of an inventory's cost — so COGS is
stated net of it and the P&L's VAT lines do not mention it. Every subtotal from
net revenue down is therefore net of VAT. The discount is shown at face value,
so the VAT inside it comes off at PC3 rather than earlier.

**Derived, never stored.** Every number is read at request time from the columns
that already own it — the order's fee columns, the courier's delivery row, the
FIFO projection in `inventory_line_costs` — so there is no cache to fall out of
step with the order screen, the dashboard or the VAT ledger. The per-order
expressions below are the *only* arithmetic: the orders list selects them per
row, the order breakdown selects them for one order, and the P&L page sums them
grouped by channel. Each is rounded per order before it is summed, so a P&L
total is exactly the sum of the rows it stands for, to the fils.

**Which orders count.** A sale that stands (`pos_reports._COMPLETED_SALE`, the
predicate the sales reports and the VAT ledger share), plus a terminal order
the shop was nonetheless charged for — a marketplace cancellation fee, or a
marketplace statement that bills the shop for a cancelled order (net payable
below zero). A cancelled or fully refunded order with no charge is not in the
P&L at all. On a charged cancellation there is no revenue, and everything the
marketplace billed is booked as a cancellation charge rather than scattered
over commission and payment lines, because that is what it is.

**COGS is blank, not zero, when no stock was drawn.** Consumption only exists
from each branch's inventory go-live, and an order that never posted a
movement has an unknown cost of goods. Coalescing it to zero would put a 100%
food margin on every such order, so the value stays null and the report counts
how many orders carry one instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import (
    and_,
    case,
    false,
    func,
    literal,
    or_,
    select,
    text,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.exceptions import NotFoundError
from app.core.money import money
from app.models.aggregator import AggregatorOrder
from app.models.inventory import (
    InventoryItem,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionTypeEnum,
)
from app.models.inventory_v2 import InventoryItemKindEnum
from app.models.legal_entity import LegalEntity
from app.models.order import DeliveryMethodEnum, Order
from app.models.order_delivery import OrderDelivery
from app.models.pos_order import OrderSourceEnum
from app.services.orders.order_pricing import VAT_RATE
from app.services.orders.order_query import AGGREGATOR_CHANNEL_PREFIX, TERMINAL_STATUSES
from app.services.pos.pos_reports._base import _COMPLETED_SALE

__all__ = [
    "CHANNELS",
    "OrderPnl",
    "PnlTotals",
    "channel_expression",
    "for_order",
    "in_pnl_clause",
    "line_columns",
    "order_pnl_from_mapping",
    "pnl_margin",
    "statement_fields",
    "totals_by_channel",
    "with_cogs",
    "without_jit",
]

_ZERO = Decimal("0")

#: The sales channels the P&L groups and filters by, in display order. Coarser
#: than the dashboard's courier codes on purpose: which van carried a website
#: order is a delivery-cost question, not a channel, so every dispatched website
#: order is one `website_delivery` column.
CHANNELS: tuple[str, ...] = (
    "counter",
    "website_delivery",
    "website_pickup",
    *AGGREGATOR_CHANNEL_PREFIX.keys(),
)

#: How COGS is broken down: the inventory item kinds each line sums. Produced
#: goods include semi-finished ones (both made in-house in production); raw
#: materials are ingredients a sale drew directly (a recipe made to order).
COGS_KINDS: dict[str, tuple[str, ...]] = {
    "cogs_produced": (
        InventoryItemKindEnum.PRODUCED_GOOD.value,
        InventoryItemKindEnum.SEMI_FINISHED.value,
    ),
    "cogs_raw": (InventoryItemKindEnum.RAW_MATERIAL.value,),
    "cogs_packaging": (InventoryItemKindEnum.PACKAGING.value,),
    "cogs_resale": (InventoryItemKindEnum.RESALE_GOOD.value,),
}


#: The money lines each order carries, in statement order. The subtotals (net
#: revenue, PC1–PC3) are derived from these in `_Lines`, never selected.
LINE_KEYS: tuple[str, ...] = (
    "gmv",
    "refunds",
    "output_vat",
    "cogs",
    *COGS_KINDS,
    "delivery_fees",
    "payment_fees",
    "commission",
    "marketplace_fees",
    "delivery_cost",
    "cancellation_charges",
    "fees_vat",
    "discounts",
)


# ── SQL building blocks ──────────────────────────────────────────────────────


def channel_expression():
    """The P&L channel code of an order, as SQL (see `CHANNELS`)."""
    whens = [
        (Order.source == OrderSourceEnum.CASHIER.value, literal("counter")),
        (
            and_(
                Order.source == OrderSourceEnum.ONLINE.value,
                Order.delivery_method == DeliveryMethodEnum.PICKUP,
            ),
            literal("website_pickup"),
        ),
        (Order.source == OrderSourceEnum.ONLINE.value, literal("website_delivery")),
    ]
    for code, prefix in AGGREGATOR_CHANNEL_PREFIX.items():
        whens.append(
            (
                and_(
                    Order.source == OrderSourceEnum.AGGREGATOR.value,
                    Order.aggregator_channel.ilike(f"{prefix}%"),
                ),
                literal(code),
            )
        )
    return case(*whens, else_=literal("other"))


def _is_sale():
    # `case`, not the bare clause: the counter arm compares a nullable
    # `pos_status`, and a NULL there must read as "not a sale", not propagate.
    return case((_COMPLETED_SALE, true()), else_=false())


_billing_row = aliased(AggregatorOrder)


def _billed_after_cancel():
    """A terminal order the shop was still charged for."""
    return and_(
        Order.status.in_(TERMINAL_STATUSES),
        or_(
            func.coalesce(Order.cancellation_fee, 0) > 0,
            # The marketplace's own settlement bills the shop for the order: noon
            # and Talabat keep commission on a merchant-side cancellation, and the
            # statement says so with a negative net payable.
            # Aliased and correlated to `Order` only, so a caller that already
            # selects from `aggregator_order` (the period-charge true-up) does
            # not auto-correlate this away.
            select(_billing_row.id)
            .where(
                _billing_row.mm_order_id == Order.id,
                _billing_row.net_payable < 0,
            )
            .correlate(Order)
            .exists(),
        ),
    )


def in_pnl_clause():
    """The orders the P&L counts: a sale that stands, or a charged cancellation."""
    return or_(_COMPLETED_SALE, _billed_after_cancel())


def _cogs_lateral():
    """
    The order's cost of goods, per item kind, in one pass over its movements.

    `inventory_line_costs.total_cost` is the engine's *current* cost of each
    ledger line — re-priced when stock it drew later learns its price — rather
    than the booked figure frozen at posting (`inventory_transaction_items`),
    which for most lines is the zero the stock carried before its purchase order
    arrived. Returns come back off the cost. Stored as a magnitude on both.

    A LATERAL rather than a scalar subquery per figure: COGS, its four kinds and
    its provisional part all come from the same rows, and Postgres re-runs a
    correlated subquery once per *reference*. It is an ungrouped aggregate, so
    it yields exactly one row per order — `lines = 0` when the order drew no
    stock, which is how COGS stays NULL (unknown) rather than zero.
    """
    line = InventoryLineCost
    txn = InventoryTransaction
    signed = case(
        (
            txn.type == InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value,
            -line.total_cost,
        ),
        else_=line.total_cost,
    )

    def of_kinds(kinds):
        return func.coalesce(
            func.sum(case((InventoryItem.kind.in_(kinds), signed), else_=0)), 0
        )

    return (
        select(
            *(of_kinds(kinds).label(key) for key, kinds in COGS_KINDS.items()),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                txn.type
                                == InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
                                line.is_provisional.is_(True),
                            ),
                            line.total_cost,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("provisional"),
            func.count(line.line_id).label("lines"),
        )
        .select_from(line)
        .join(txn, txn.id == line.transaction_id)
        .join(InventoryItem, InventoryItem.id == line.item_id)
        .where(
            txn.order_id == Order.id,
            txn.status == "closed",
            txn.type.in_(
                [
                    InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
                    InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value,
                    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value,
                ]
            ),
            line.superseded.is_(False),
        )
        .correlate(Order)
        .lateral("pnl_cogs")
    )


#: One shared alias: `line_columns()` reads its columns, and `with_cogs()` joins
#: it into the statement that selects them.
_COGS = _cogs_lateral()


def with_cogs(stmt):
    """Join the per-order COGS LATERAL into a statement selecting from `Order`
    — required by any statement that selects `line_columns()`."""
    return stmt.outerjoin(_COGS, true())


def _courier_cost_subquery():
    """
    What our own courier charged: the invoice (`cost_total`), else the quote.

    Null where no delivery row exists (a counter sale, a pickup) and where a
    third-party zone bills nothing per order.
    """
    return (
        select(func.coalesce(OrderDelivery.cost_total, OrderDelivery.quoted_cost))
        .where(OrderDelivery.order_id == Order.id)
        .correlate(Order)
        .limit(1)
        .scalar_subquery()
    )


def _reclaims_input_vat():
    """
    Whether the order's entity reclaims input VAT — i.e. is not one of the
    non-registered entities. No entity ⇒ the default, which is registered (the
    same fallback `vat_ledger` applies).

    An uncorrelated `NOT IN`, not a per-row lookup: it is read by several
    columns of every row, and Postgres hashes an uncorrelated subquery once
    where it would re-run a correlated one per row per column.
    """
    unregistered = select(LegalEntity.id).where(LegalEntity.vat_registered.is_(False))
    return or_(
        Order.legal_entity_id.is_(None),
        Order.legal_entity_id.notin_(unregistered),
    )


def line_columns() -> dict[str, object]:
    """
    Every P&L line of one order as a labelled SQL expression, rounded per order.

    Select them beside `Order` for per-row figures, or `func.sum()` them for a
    total; `_Lines` turns a row of them back into subtotals. Also carries three
    bookkeeping columns: whether the fees are still unknown, the provisional part
    of COGS, and whether the row is a sale (vs a charged cancellation).
    """
    sale = _is_sale()
    # Output VAT is the order's own: `vat_rate` on an order that actually charged
    # VAT, zero on one booked under a non-registered entity.
    out_rate = case((Order.vat_amount > 0, Order.vat_rate), else_=0)
    out_div = 1 + out_rate
    # Input VAT on a cost is reclaimed only by a registered entity; otherwise the
    # whole VAT-inclusive figure is the cost.
    in_div = case((_reclaims_input_vat(), 1 + VAT_RATE), else_=1)

    refunded = func.coalesce(Order.refunded_amount, 0)
    payment = func.coalesce(Order.payment_fee, 0)
    commission = func.coalesce(Order.aggregator_fee, 0)
    marketing = func.coalesce(Order.marketing_fee, 0)
    cancellation = func.coalesce(Order.cancellation_fee, 0)
    courier = case(
        # A marketplace carries its own orders and invoices no van; its cost of
        # sale is the commission. Never both.
        (Order.source == OrderSourceEnum.AGGREGATOR.value, 0),
        else_=func.coalesce(_courier_cost_subquery(), 0),
    )

    def r(expr):
        return func.round(expr, 2)

    def on_sale(expr):
        return case((sale, expr), else_=0)

    # Every cost as billed (VAT-inclusive), so the VAT reclaimed is exactly the
    # gross less what it would be net.
    gross_fees = case(
        (sale, payment + commission + marketing + courier + cancellation),
        else_=payment + commission + marketing + cancellation,
    )
    # FIFO unit costs are the purchase price VAT-inclusive (a PO line's
    # `unit_cost` is its gross), but that VAT was reclaimed in the VAT return of
    # the period the stock was *bought* — it was never part of the goods' cost,
    # and it is not a flow at the sale. So COGS is stated net of it, with no VAT
    # line of its own. Assumed at the standard rate on all of it: every supplier
    # is VAT-deductible today and every purchase is booked by the registered
    # entity; tracing each consumed layer back to its receipt through
    # production and transfers is a costing-engine change, not a report one.
    cogs_net = 1 + VAT_RATE
    kind_parts = {key: func.round(_COGS.c[key] / cogs_net, 2) for key in COGS_KINDS}
    # What the customer paid us for delivery: the delivery fee and the
    # small-basket fee. Outside the VAT base (VAT is charged on the goods only —
    # `order_pricing` computes it on the discounted subtotal), so no VAT line;
    # the card fee on it is already in `payment_fee`, VAT and all. Zero on a
    # marketplace order, whose delivery fee the customer pays the marketplace.
    delivery_fees = func.coalesce(Order.delivery_fee, 0) + func.coalesce(
        Order.low_order_fee, 0
    )
    return {
        # The goods the customer was billed for before discounts, VAT included:
        # the charged total with the discount put back and the delivery fees
        # taken out (they are their own line below).
        "gmv": r(on_sale(Order.total + Order.discount_amount - delivery_fees)).label(
            "gmv"
        ),
        "refunds": r(on_sale(refunded)).label("refunds"),
        # The VAT actually charged, less the VAT handed back with a refund —
        # the order's own frozen figures, the same the VAT ledger books.
        "output_vat": r(
            on_sale(Order.vat_amount - refunded * out_rate / out_div)
        ).label("output_vat"),
        # COGS is the sum of its kinds, each rounded per order, so the kind
        # lines always add up to it exactly; NULL when no stock was drawn.
        "cogs": case((_COGS.c.lines > 0, sum(kind_parts.values())), else_=None).label(
            "cogs"
        ),
        **{key: part.label(key) for key, part in kind_parts.items()},
        "delivery_fees": r(on_sale(delivery_fees)).label("delivery_fees"),
        "payment_fees": r(on_sale(payment)).label("payment_fees"),
        "commission": r(on_sale(commission)).label("commission"),
        "marketplace_fees": r(on_sale(marketing)).label("marketplace_fees"),
        "delivery_cost": r(on_sale(courier)).label("delivery_cost"),
        "cancellation_charges": r(
            case(
                (sale, cancellation),
                # A charged cancellation: everything billed is the charge.
                else_=payment + commission + marketing + cancellation,
            )
        ).label("cancellation_charges"),
        "fees_vat": r(gross_fees - gross_fees / in_div).label("fees_vat"),
        "discounts": r(on_sale(Order.discount_amount)).label("discounts"),
        # ── bookkeeping ──
        "cogs_provisional": r(func.coalesce(_COGS.c.provisional, 0) / cogs_net).label(
            "cogs_provisional"
        ),
        "fees_pending": case(
            # A marketplace order whose commission has not landed yet (its
            # statement arrives days to a month later), or a dispatched website
            # order whose courier has neither quoted nor invoiced.
            (
                and_(
                    Order.source == OrderSourceEnum.AGGREGATOR.value,
                    Order.aggregator_fee.is_(None),
                ),
                true(),
            ),
            (
                and_(
                    Order.source == OrderSourceEnum.ONLINE.value,
                    Order.delivery_method == DeliveryMethodEnum.DELIVERY,
                    _courier_cost_subquery().is_(None),
                ),
                true(),
            ),
            else_=false(),
        ).label("fees_pending"),
        "is_sale": sale.label("is_sale"),
    }


# ── turning rows back into statements ────────────────────────────────────────


@dataclass
class _Lines:
    """
    The P&L lines of one order or one group of orders.

    Revenue and fees are as billed — VAT included — and their VAT comes off as
    lines of its own: the output VAT owed on sales and the input VAT reclaimed
    on fees. COGS is net cost: its VAT was reclaimed at purchase, not at sale.
    So every subtotal from net revenue down is net of VAT.
    """

    gmv: Decimal = _ZERO
    refunds: Decimal = _ZERO
    output_vat: Decimal = _ZERO
    #: Net of the VAT reclaimed when the stock was bought. Null only on a single
    #: order that drew no stock; a group sums what it has.
    cogs: Decimal | None = None
    #: COGS by inventory item kind (net of VAT) — they sum to `cogs`.
    cogs_produced: Decimal = _ZERO
    cogs_raw: Decimal = _ZERO
    cogs_packaging: Decimal = _ZERO
    cogs_resale: Decimal = _ZERO
    #: Delivery + small-basket fees the customer paid us. No VAT on them.
    delivery_fees: Decimal = _ZERO
    payment_fees: Decimal = _ZERO
    commission: Decimal = _ZERO
    marketplace_fees: Decimal = _ZERO
    delivery_cost: Decimal = _ZERO
    cancellation_charges: Decimal = _ZERO
    #: Non-order marketplace charges (monthly platform fees…), as billed.
    #: Report-level only.
    period_charges: Decimal = _ZERO
    #: Input VAT reclaimed on the fee lines (and on the period charges).
    fees_vat: Decimal = _ZERO
    discounts: Decimal = _ZERO

    @property
    def net_revenue(self) -> Decimal:
        return money(self.gmv - self.refunds - self.output_vat)

    @property
    def pc1(self) -> Decimal:
        return money(self.net_revenue - (self.cogs or _ZERO))

    @property
    def aggregator_and_delivery_fees(self) -> Decimal:
        return money(self.commission + self.marketplace_fees + self.delivery_cost)

    @property
    def misc_fees(self) -> Decimal:
        return money(self.cancellation_charges + self.period_charges)

    @property
    def pc2(self) -> Decimal:
        return money(
            self.pc1
            + self.delivery_fees
            - self.payment_fees
            - self.aggregator_and_delivery_fees
            - self.misc_fees
            + self.fees_vat
        )

    @property
    def pc3(self) -> Decimal:
        return money(self.pc2 - self.discounts)

    @property
    def net_vat(self) -> Decimal:
        """Output VAT less the fee VAT reclaimed against these sales. VAT on the
        stock sold is not here: it was reclaimed when the stock was bought."""
        return money(self.output_vat - self.fees_vat)

    def share(self, value: Decimal) -> Decimal | None:
        """`value` as a percentage of GMV; null when there is no GMV."""
        if self.gmv <= 0:
            return None
        return money(value / self.gmv * 100)


@dataclass
class OrderPnl(_Lines):
    """One order's P&L, plus what is still unknown about it."""

    order_id: uuid.UUID | None = None
    channel: str = "other"
    #: A sale, or a cancellation the shop was charged for.
    is_sale: bool = True
    cogs_provisional: Decimal = _ZERO
    fees_pending: bool = False


@dataclass
class PnlTotals(_Lines):
    """A group of orders' P&L, plus the counts that say how complete it is."""

    orders: int = 0
    charged_cancellations: int = 0
    orders_with_cogs: int = 0
    orders_fees_pending: int = 0
    cogs_provisional: Decimal = _ZERO

    def add(self, other: "PnlTotals") -> None:
        for key in LINE_KEYS:
            if key == "cogs":
                continue
            setattr(self, key, money(getattr(self, key) + getattr(other, key)))
        if other.cogs is not None:
            self.cogs = money((self.cogs or _ZERO) + other.cogs)
        self.period_charges = money(self.period_charges + other.period_charges)
        self.orders += other.orders
        self.charged_cancellations += other.charged_cancellations
        self.orders_with_cogs += other.orders_with_cogs
        self.orders_fees_pending += other.orders_fees_pending
        self.cogs_provisional = money(self.cogs_provisional + other.cogs_provisional)


def _money_or_none(value) -> Decimal | None:
    return None if value is None else money(value)


def order_pnl_from_mapping(m: Mapping) -> OrderPnl | None:
    """
    An `OrderPnl` from a row that selected `in_pnl_clause()` (as `in_pnl`) and
    `line_columns()` beside the order — or None when the order is not in the P&L.
    """
    if not m.get("in_pnl"):
        return None
    return OrderPnl(
        order_id=m.get("order_id"),
        channel=m.get("channel") or "other",
        gmv=money(m["gmv"]),
        refunds=money(m["refunds"]),
        cogs=_money_or_none(m["cogs"]),
        **{key: money(m[key]) for key in COGS_KINDS},
        delivery_fees=money(m["delivery_fees"]),
        payment_fees=money(m["payment_fees"]),
        commission=money(m["commission"]),
        marketplace_fees=money(m["marketplace_fees"]),
        delivery_cost=money(m["delivery_cost"]),
        cancellation_charges=money(m["cancellation_charges"]),
        fees_vat=money(m["fees_vat"]),
        discounts=money(m["discounts"]),
        output_vat=money(m["output_vat"]),
        cogs_provisional=money(m["cogs_provisional"]),
        fees_pending=bool(m["fees_pending"]),
        is_sale=bool(m["is_sale"]),
    )


# ── reads ────────────────────────────────────────────────────────────────────


async def without_jit(db: AsyncSession) -> None:
    """
    Turn Postgres JIT off for the rest of this transaction.

    These reads are a few thousand rows under a wide target list of correlated
    lookups, and the planner's cost estimate for that tips it over
    `jit_above_cost`: on production (Sept 2026, ~2.2k orders) the September
    P&L spent 6.2 s of its 7.3 s *compiling* 189 JIT functions and 1 s running.
    `SET LOCAL` scopes the setting to the request's own transaction, so nothing
    else on the pooled connection inherits it.
    """
    await db.execute(text("SET LOCAL jit = off"))


async def for_order(db: AsyncSession, order_id: uuid.UUID) -> OrderPnl | None:
    """
    One order's P&L, or None when the order is not in the P&L — still in flight,
    or cancelled/refunded without a charge.
    """
    await without_jit(db)
    cols = line_columns()
    row = (
        await db.execute(
            with_cogs(
                select(
                    Order.id.label("order_id"),
                    channel_expression().label("channel"),
                    in_pnl_clause().label("in_pnl"),
                    *cols.values(),
                ).select_from(Order)
            ).where(Order.id == order_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Order not found")
    return order_pnl_from_mapping(row._mapping)


def totals_statement(*where):
    """
    The grouped-by-channel SELECT behind `totals_by_channel`.

    Two steps on purpose: the per-order lines are computed once per order in a
    materialised CTE, then summed. Summing the expressions directly makes
    Postgres evaluate each correlated subquery (COGS above all) once per
    *reference* — the COGS lookup alone three times per order.
    """
    cols = line_columns()
    per_order = (
        with_cogs(
            select(channel_expression().label("channel"), *cols.values()).select_from(
                Order
            )
        )
        .where(in_pnl_clause(), *where)
        .cte("pnl_orders")
        .prefix_with("MATERIALIZED")
    )
    c = per_order.c
    sums = [
        func.sum(c[key]).label(key)
        for key in cols
        if key not in ("fees_pending", "is_sale")
    ]
    return select(
        c.channel,
        func.count().label("orders"),
        func.count(case((c.is_sale, None), else_=1)).label("charged_cancellations"),
        func.count(c.cogs).label("orders_with_cogs"),
        func.count(case((c.fees_pending, 1))).label("orders_fees_pending"),
        *sums,
    ).group_by(c.channel)


async def totals_by_channel(db: AsyncSession, *where) -> dict[str, PnlTotals]:
    """
    Every P&L line summed per channel, over the orders `where` selects (the
    caller's date / channel / branch filters). Order-level only — the report
    adds non-order period charges on top.
    """
    await without_jit(db)
    stmt = totals_statement(*where)
    out: dict[str, PnlTotals] = {}
    for row in (await db.execute(stmt)).all():
        m = row._mapping
        out[m["channel"]] = PnlTotals(
            gmv=money(m["gmv"]),
            refunds=money(m["refunds"]),
            cogs=_money_or_none(m["cogs"]),
            **{key: money(m[key]) for key in COGS_KINDS},
            delivery_fees=money(m["delivery_fees"]),
            payment_fees=money(m["payment_fees"]),
            commission=money(m["commission"]),
            marketplace_fees=money(m["marketplace_fees"]),
            delivery_cost=money(m["delivery_cost"]),
            cancellation_charges=money(m["cancellation_charges"]),
            fees_vat=money(m["fees_vat"]),
            discounts=money(m["discounts"]),
            output_vat=money(m["output_vat"]),
            cogs_provisional=money(m["cogs_provisional"]),
            orders=int(m["orders"]),
            charged_cancellations=int(m["charged_cancellations"]),
            orders_with_cogs=int(m["orders_with_cogs"]),
            orders_fees_pending=int(m["orders_fees_pending"]),
        )
    return out


def pnl_margin(pnl: OrderPnl | None) -> Decimal | None:
    """PC3 as a % of GMV — the orders list's one-number summary."""
    return None if pnl is None else pnl.share(pnl.pc3)


def statement_fields(lines: _Lines) -> dict[str, Decimal | None]:
    """Every line and subtotal of a P&L, keyed as `schemas.pnl.PnlStatement`."""
    return {
        "gmv": lines.gmv,
        "refunds": lines.refunds,
        "output_vat": lines.output_vat,
        "net_revenue": lines.net_revenue,
        "cogs": lines.cogs,
        **{key: getattr(lines, key) for key in COGS_KINDS},
        "pc1": lines.pc1,
        "delivery_fees": lines.delivery_fees,
        "payment_fees": lines.payment_fees,
        "commission": lines.commission,
        "marketplace_fees": lines.marketplace_fees,
        "delivery_cost": lines.delivery_cost,
        "aggregator_and_delivery_fees": lines.aggregator_and_delivery_fees,
        "cancellation_charges": lines.cancellation_charges,
        "period_charges": lines.period_charges,
        "misc_fees": lines.misc_fees,
        "fees_vat": lines.fees_vat,
        "pc2": lines.pc2,
        "discounts": lines.discounts,
        "pc3": lines.pc3,
        "net_vat": lines.net_vat,
        "pc1_pct": lines.share(lines.pc1),
        "pc2_pct": lines.share(lines.pc2),
        "pc3_pct": lines.share(lines.pc3),
    }
