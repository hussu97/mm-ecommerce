"""
Stock bought on purchase orders that the P&L has not expensed yet.

The P&L's COGS line is the FIFO cost of what orders drew (`order_pnl`). A
purchase order's stock reaches it only when it is sold, so the page's second
question — what did we buy in this window that is not in that line yet? — is

    Received   the stock lines of the window's closed POs, net of recoverable VAT
  − Consumed   the part of that stock the P&L's orders have already drawn, to date
  = Net        in no P&L's COGS, and in two parts:
      used elsewhere   gone without passing through a P&L order — a count
                       shortfall, internal use, an adjustment, an order the P&L
                       does not count (cancelled, not sold yet)
      in stock         still on the shelf, raw or inside goods not sold yet

The split matters because Net is not inventory. In the first month after
go-live, four-fifths of September's Net was stock that later counts took off
the books — a real cost, and exactly the one no P&L line carries.

Misc lines (rent, salaries, supplies) are not stock and are on the P&L already
at their level (`misc_expenses`), so they are not here at all.

**Consumed is traced, not estimated.** Flour is rarely sold as flour: it goes
into a batch, the batch into a transfer, the transfer into a sale. The costing
engine's projection keeps every link — each draw names the layer it took
(`inventory_cost_layer_consumptions`), each layer the line that made it — so
the receipt's stock is followed forward through every batch, transfer and
return, in proportion to the quantities, to the order lines the P&L's COGS sums
(`order_pnl._cogs_lateral`: consumption, waste and returns of the orders
`in_pnl_clause` counts). A sale of 2 cakes from a batch of 10 that used 5 kg of
a PO's flour has consumed 1 kg of it.

**The PO's own units, not its price.** FIFO decides which stock a draw took;
the trace follows exactly that. Stock issued on credit (negative stock, a sale
keyed before the delivery) belongs to whatever paid that debt back — the
engine's rule 2: the next stock in settles the shortfall before it lays a
layer — oldest debt first. What the trace does *not* follow is the engine's
pricing link (`priced_by_line_id`): when a PO lands, the engine also prices
stock a count found earlier (go-live's opening stock, mostly) at the PO's
price, but that stock was paid for before the PO and its sale is not the PO's
money; following the price instead credited one PO with ninety times what it
delivered. Where a batch was re-costed by a production restatement, its
ingredients are still followed into what it made: the restated cost replaces
the recorded one, it does not remove the flour.

**Valued at the PO's own price.** Each (PO, item)'s consumed share is
`traced quantity ÷ received quantity`, applied to that line's net value, so
Received − Consumed = Net holds to the fils for every row. Physical flow
conserves quantity, so a share cannot pass the whole; it is clamped to 0–1 all
the same, so a projection out of step with its ledger cannot show more consumed
than was bought.

**To date, not in the window.** A September PO whose flour sold in October is
consumed: it is in October's COGS, and counting it in September's net as well
would put it in two places. Read month by month, every fils of PO stock lands
exactly once — in some month's COGS, or in its receipt month's net. The channel
filter does not apply (stock is not bought per channel); branch and entity pick
the POs, by where they were bought.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.money import ZERO, money
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCostLayer,
    InventoryCostLayerConsumption,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatusEnum,
    Supplier,
)
from app.models.inventory import InventoryTransactionTypeEnum as T
from app.models.order import Order
from app.services.orders.order_pnl import in_pnl_clause

__all__ = ["PoStockReport", "PoStockRow", "build", "trace"]

logger = logging.getLogger(__name__)

#: A (purchase order, item) pair: the unit a received value and a consumed
#: share are kept at.
Key = tuple[uuid.UUID, uuid.UUID]
#: Quantity of each key's stock per unit of whatever carries it.
Vector = dict[Key, Decimal]

#: The engine's transfer key: either spelling of a transfer source is one.
_TRANSFER_SOURCES = ("transfer", "transfer_order")
_BATCH_INPUTS = (T.CONSUMPTION_FROM_PRODUCTION.value, T.WASTE_FROM_PRODUCTION.value)
#: The order movements the P&L's COGS sums (`order_pnl._cogs_lateral`).
_ORDER_ISSUES = (T.CONSUMPTION_FROM_ORDERS.value, T.WASTE_FROM_ORDERS.value)
#: A recipe graph is shallow (ingredient → semi-finished → cake → transfer →
#: sale). The one loop the ledger has is an order drawing back the stock it
#: returned, which carries a shrinking fraction round each time: content below
#: this, per unit, is dropped, and so the loop ends. Hitting the round limit
#: instead means a loop that does not shrink, and is logged.
_NEGLIGIBLE = Decimal("1e-12")
_MAX_ROUNDS = 256


@dataclass
class PoStockRow:
    purchase_order_id: uuid.UUID
    reference: str
    business_date: str
    supplier: str
    branch: str
    #: Stock lines received, net of recoverable VAT.
    received: Decimal = ZERO
    #: Drawn by orders the P&L counts: inside its COGS already.
    consumed: Decimal = ZERO
    #: Gone without passing through a P&L order: count shortfalls, internal
    #: use, extra production use, adjustments, orders the P&L does not count.
    used_elsewhere: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        """Received − consumed: in no P&L's COGS."""
        return money(self.received - self.consumed)

    @property
    def in_stock(self) -> Decimal:
        """The part of net still on the shelf, raw or inside unsold goods."""
        return money(self.net - self.used_elsewhere)


@dataclass
class PoStockReport:
    rows: list[PoStockRow] = field(default_factory=list)

    def _sum(self, attr: str) -> Decimal:
        return money(sum((getattr(r, attr) for r in self.rows), ZERO))

    @property
    def received(self) -> Decimal:
        return self._sum("received")

    @property
    def consumed(self) -> Decimal:
        return self._sum("consumed")

    @property
    def used_elsewhere(self) -> Decimal:
        return self._sum("used_elsewhere")

    @property
    def net(self) -> Decimal:
        return self._sum("net")

    @property
    def in_stock(self) -> Decimal:
        return self._sum("in_stock")


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _add(into: Vector, vector: Vector, scale: Decimal) -> None:
    for key, amount in vector.items():
        into[key] = into.get(key, Decimal(0)) + amount * scale


async def build(
    db: AsyncSession,
    *,
    date_from: str,
    date_to: str,
    branch_ids: list[uuid.UUID] | None = None,
    legal_entity_ids: list[uuid.UUID] | None = None,
) -> PoStockReport:
    """The window's closed purchase orders, each with where its stock has gone
    to date. Dated by the PO's shop day — the day it was received, and the day
    the VAT ledger books its input VAT."""
    stmt = (
        select(
            PurchaseOrder.id,
            PurchaseOrder.reference,
            PurchaseOrder.business_date,
            Supplier.name,
            Branch.name,
            PurchaseOrderItem.item_id,
            PurchaseOrderItem.quantity,
            PurchaseOrderItem.received_quantity,
            PurchaseOrderItem.unit_cost,
            PurchaseOrderItem.vat_amount,
        )
        .join(
            PurchaseOrderItem, PurchaseOrderItem.purchase_order_id == PurchaseOrder.id
        )
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .join(Branch, Branch.id == PurchaseOrder.branch_id)
        .where(
            PurchaseOrder.status == PurchaseOrderStatusEnum.CLOSED.value,
            PurchaseOrder.business_date >= date_from,
            PurchaseOrder.business_date <= date_to,
        )
        .order_by(PurchaseOrder.business_date, PurchaseOrder.reference)
    )
    if branch_ids:
        stmt = stmt.where(PurchaseOrder.branch_id.in_(branch_ids))
    if legal_entity_ids:
        stmt = stmt.where(PurchaseOrder.legal_entity_id.in_(legal_entity_ids))

    rows: dict[uuid.UUID, PoStockRow] = {}
    value: dict[Key, Decimal] = defaultdict(Decimal)
    for po_id, ref, day, supplier, branch, item_id, ordered, received, unit, vat in (
        await db.execute(stmt)
    ).all():
        row = rows.get(po_id)
        if row is None:
            row = rows[po_id] = PoStockRow(po_id, ref, day, supplier, branch)
        # What arrived, as `receive_purchase_order` books it: the gross at the
        # line's unit price, less the recoverable VAT on it pro rata, capped at
        # the ordered quantity (the invoice's VAT does not grow with an excess).
        received, ordered = _d(received), _d(ordered)
        gross = received * _d(unit)
        reclaimable = _d(vat) * min(received, ordered) / ordered if ordered > 0 else 0
        net = money(gross - reclaimable)
        row.received = money(row.received + net)
        value[(po_id, item_id)] += net

    shares = await trace(db, list(rows))
    for key, net in value.items():
        consumed, elsewhere = shares.get(key, (Decimal(0), Decimal(0)))
        row = rows[key[0]]
        row.consumed = money(row.consumed + money(net * consumed))
        row.used_elsewhere = money(row.used_elsewhere + money(net * elsewhere))
    return PoStockReport(rows=list(rows.values()))


async def trace(
    db: AsyncSession, purchase_order_ids: list[uuid.UUID]
) -> dict[Key, tuple[Decimal, Decimal]]:
    """Per (PO, item): the shares of its received stock (each 0–1, together at
    most 1) that orders the P&L counts have drawn, and that left some other
    way — directly or inside what it was made into. The rest is on hand."""
    if not purchase_order_ids:
        return {}
    receipts = (
        await db.execute(
            select(
                InventoryLineCost.line_id,
                InventoryTransaction.purchase_order_id,
                InventoryLineCost.item_id,
                InventoryLineCost.quantity,
            )
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryLineCost.transaction_id,
            )
            .where(
                InventoryTransaction.type == T.PURCHASING.value,
                InventoryTransaction.purchase_order_id.in_(purchase_order_ids),
            )
        )
    ).all()
    received: dict[Key, Decimal] = defaultdict(Decimal)
    for _line, po_id, item_id, qty in receipts:
        received[(po_id, item_id)] += _d(qty)

    tracer = _Tracer(
        db, {line: (po_id, item_id) for line, po_id, item_id, _ in receipts}
    )
    await tracer.run()

    def share(traced: Vector, key: Key, qty: Decimal, room: Decimal) -> Decimal:
        value = traced.get(key, Decimal(0)) / qty
        if value > room + _NEGLIGIBLE:
            logger.warning(
                "PO stock %s traced past what was received (%s of %s); clamped",
                key,
                traced[key],
                qty,
            )
        return min(max(value, Decimal(0)), room)

    shares: dict[Key, tuple[Decimal, Decimal]] = {}
    for key, qty in received.items():
        if qty <= 0:
            continue
        consumed = share(tracer.drawn, key, qty, Decimal(1))
        shares[key] = (consumed, share(tracer.elsewhere, key, qty, 1 - consumed))
    return shares


# ─── The trace ────────────────────────────────────────────────────────────────
#
# A *node* is a source of stock other stock is drawn from: a PO receipt (a
# leaf), a production batch, a transfer, or an order's sale (which a return puts
# back on the shelf). Each has the ledger lines that brought its stock in. Every
# node has a per-unit content — the quantity of each PO line inside one unit of
# its stock — and the trace pushes that content forward along the draws, in
# rounds, until it lands on an order or leaves the books some other way.
# It is linear, so content arriving at a node from several inputs is simply
# pushed on as it arrives.

#: ("leaf", line) | ("batch", group) | ("transfer", source_id, item)
#: | ("sale", order, item, warehouse)
Node = tuple


@dataclass
class _Consumer:
    """The ledger line a draw fed, and what that makes the draw."""

    type: str
    group: uuid.UUID | None
    transfer: tuple[str, uuid.UUID] | None
    order_id: uuid.UUID | None
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    in_pnl: bool


class _Tracer:
    def __init__(self, db: AsyncSession, receipts: dict[uuid.UUID, Key]) -> None:
        self.db = db
        self.receipts = receipts
        #: The inflow lines that brought each node's stock in.
        self.lines: dict[Node, list[uuid.UUID]] = {
            ("leaf", line): [line] for line in receipts
        }
        self.node_of_line: dict[uuid.UUID, Node] = {
            line: ("leaf", line) for line in receipts
        }
        #: Units a node's content is spread over: a batch's output, a
        #: transfer's quantity sent, a sale's quantity sold.
        self.size: dict[Node, Decimal] = {}
        #: A sale's returned quantity, and whether the P&L counts its order.
        self.returned: dict[Node, Decimal] = {}
        self.sale_in_pnl: dict[Node, bool] = {}
        self.draws: dict[Node, list[tuple[Decimal, _Consumer]]] = {}
        #: Content drawn by orders the P&L counts, and taken some other way.
        self.drawn: Vector = {}
        self.elsewhere: Vector = {}
        #: Orders with a return: the only sales whose content goes anywhere.
        self.returning: set[uuid.UUID] = set()

    async def run(self) -> None:
        self.returning = set(
            (
                await self.db.execute(
                    select(InventoryTransaction.order_id)
                    .join(
                        InventoryLineCost,
                        InventoryLineCost.transaction_id == InventoryTransaction.id,
                    )
                    .where(
                        InventoryTransaction.type == T.RETURN_FROM_ORDERS.value,
                        InventoryTransaction.order_id.is_not(None),
                    )
                    .distinct()
                )
            ).scalars()
        )
        frontier: dict[Node, Vector] = {
            ("leaf", line): {key: Decimal(1)} for line, key in self.receipts.items()
        }
        for _ in range(_MAX_ROUNDS):
            if not frontier:
                return
            await self._load_draws([n for n in frontier if n not in self.draws])
            arriving: dict[Node, Vector] = defaultdict(dict)  # total content per node
            for node, per_unit in frontier.items():
                for qty, consumer in self.draws[node]:
                    self._route(consumer, per_unit, qty, arriving)
            await self._describe([n for n in arriving if n not in self.size])
            frontier = {}
            for node, total in arriving.items():
                size = self.size.get(node, Decimal(0))
                if size <= 0:
                    continue
                per_unit = {
                    k: v / size for k, v in total.items() if abs(v / size) > _NEGLIGIBLE
                }
                if not per_unit:
                    continue
                if node[0] == "sale":
                    # A return takes this content back off the order (and so
                    # off the P&L's COGS, which nets it), and back on the shelf.
                    back = self.returned.get(node, Decimal(0))
                    if back <= 0:
                        continue
                    sink = self.drawn if self.sale_in_pnl.get(node) else self.elsewhere
                    _add(sink, per_unit, -back)
                if self.lines.get(node):
                    frontier[node] = per_unit
        logger.warning("PO stock trace stopped after %s rounds: a cycle?", _MAX_ROUNDS)

    def _route(
        self,
        consumer: _Consumer,
        per_unit: Vector,
        qty: Decimal,
        arriving: dict[Node, Vector],
    ) -> None:
        if consumer.type in _ORDER_ISSUES and consumer.order_id is not None:
            # An order the P&L does not count (cancelled, not yet sold) is
            # stock gone without reaching COGS — elsewhere, like a write-off.
            _add(self.drawn if consumer.in_pnl else self.elsewhere, per_unit, qty)
            if (
                consumer.type == T.CONSUMPTION_FROM_ORDERS.value
                and consumer.order_id in self.returning
            ):
                node = (
                    "sale",
                    consumer.order_id,
                    consumer.item_id,
                    consumer.warehouse_id,
                )
                self.sale_in_pnl[node] = consumer.in_pnl
                _add(arriving[node], per_unit, qty)
        elif consumer.type in _BATCH_INPUTS and consumer.group is not None:
            _add(arriving[("batch", consumer.group)], per_unit, qty)
        elif consumer.type == T.TRANSFER_SEND.value and consumer.transfer is not None:
            _add(arriving[("transfer", *consumer.transfer)], per_unit, qty)
        else:
            # A count shortfall, internal use, an adjustment: the stock left the
            # books without passing through any P&L. It stays in Net.
            _add(self.elsewhere, per_unit, qty)

    async def _describe(self, nodes: list[Node]) -> None:
        """Each new node's size and the lines its stock is drawn through. A query
        by transfer or order also returns that transfer's or order's other items,
        which may be nodes described already: only `new` ones are counted."""
        new = set(nodes)
        for node in nodes:
            self.size[node] = Decimal(0)
            self.lines.setdefault(node, [])
        groups = [n[1] for n in nodes if n[0] == "batch"]
        transfers = {n[1] for n in nodes if n[0] == "transfer"}
        sales = {n[1] for n in nodes if n[0] == "sale"}
        line = InventoryLineCost
        txn = InventoryTransaction
        base = select(
            line.line_id,
            line.item_id,
            line.warehouse_id,
            line.quantity,
            txn.type,
            txn.correction_group_id,
            txn.source_id,
            txn.order_id,
        ).join(txn, txn.id == line.transaction_id)
        if groups:
            for row in (
                await self.db.execute(
                    base.where(
                        txn.correction_group_id.in_(groups),
                        txn.type == T.PRODUCTION.value,
                    )
                )
            ).all():
                node = ("batch", row.correction_group_id)
                if node not in new:
                    continue
                self._own(node, row.line_id)
                self.size[node] += _d(row.quantity)
        if transfers:
            for row in (
                await self.db.execute(
                    base.where(
                        txn.source_type.in_(_TRANSFER_SOURCES),
                        txn.source_id.in_(transfers),
                        txn.type.in_([T.TRANSFER_SEND.value, T.TRANSFER_RECEIVE.value]),
                    )
                )
            ).all():
                node = ("transfer", row.source_id, row.item_id)
                if node not in new:
                    continue
                if row.type == T.TRANSFER_SEND.value:
                    self.size[node] -= _d(row.quantity)
                else:
                    self._own(node, row.line_id)
        if sales:
            for row in (
                await self.db.execute(
                    base.where(
                        txn.order_id.in_(sales),
                        txn.type.in_(
                            [
                                T.CONSUMPTION_FROM_ORDERS.value,
                                T.RETURN_FROM_ORDERS.value,
                            ]
                        ),
                    )
                )
            ).all():
                node = ("sale", row.order_id, row.item_id, row.warehouse_id)
                if node not in new:
                    continue
                if row.type == T.CONSUMPTION_FROM_ORDERS.value:
                    self.size[node] -= _d(row.quantity)
                else:
                    self.returned[node] = self.returned.get(node, Decimal(0)) + _d(
                        row.quantity
                    )
                    self._own(node, row.line_id)

    def _own(self, node: Node, line_id: uuid.UUID) -> None:
        self.lines[node].append(line_id)
        self.node_of_line[line_id] = node

    async def _load_draws(self, nodes: list[Node]) -> None:
        """Every draw on these nodes' stock, with the line it fed: what was
        taken from the layers their lines laid down, and the stock issued on
        credit before they landed that their lines paid back."""
        for node in nodes:
            self.draws[node] = []
        lines = [line for node in nodes for line in self.lines.get(node, [])]
        if not lines:
            return
        draw = InventoryCostLayerConsumption
        layer = InventoryCostLayer
        consumer_line = aliased(InventoryLineCost)
        consumer_txn = aliased(InventoryTransaction)
        pnl_orders = select(Order.id).where(in_pnl_clause())
        # An issue that was reversed (a voided order's restock) put its stock
        # back on the layers it drew from, where a later draw takes it again:
        # count it once, there.
        undone = (
            select(InventoryTransactionItem.id)
            .where(InventoryTransactionItem.reverses_line_id == draw.consuming_line_id)
            .exists()
        )

        def consumed(stmt):
            return (
                stmt.join(
                    consumer_line, consumer_line.line_id == draw.consuming_line_id
                )
                .join(consumer_txn, consumer_txn.id == consumer_line.transaction_id)
                .add_columns(
                    draw.quantity,
                    consumer_txn.type,
                    consumer_txn.correction_group_id,
                    consumer_txn.source_type,
                    consumer_txn.source_id,
                    consumer_txn.order_id,
                    consumer_line.item_id,
                    consumer_line.warehouse_id,
                    case(
                        (consumer_txn.order_id.in_(pnl_orders), True), else_=False
                    ).label("in_pnl"),
                )
                .where(~undone)
            )

        found = (
            await self.db.execute(
                consumed(
                    select(layer.source_line_id.label("via"))
                    .select_from(draw)
                    .join(layer, layer.id == draw.layer_id)
                ).where(layer.source_line_id.in_(lines))
            )
        ).all()
        for row in found:
            self._keep(row.via, _d(row.quantity), row)

        paid_back = await self._settled(lines)
        if paid_back:
            for row in (
                await self.db.execute(
                    consumed(select(draw.id.label("draw_id")).select_from(draw)).where(
                        draw.id.in_(list(paid_back))
                    )
                )
            ).all():
                for via, qty in paid_back[row.draw_id]:
                    self._keep(via, qty, row)

    def _keep(self, via: uuid.UUID, qty: Decimal, row) -> None:
        transfer = (
            (row.source_id, row.item_id)
            if row.source_type in _TRANSFER_SOURCES and row.source_id
            else None
        )
        self.draws[self.node_of_line[via]].append(
            (
                qty,
                _Consumer(
                    type=row.type,
                    group=row.correction_group_id,
                    transfer=transfer,
                    order_id=row.order_id,
                    item_id=row.item_id,
                    warehouse_id=row.warehouse_id,
                    in_pnl=bool(row.in_pnl),
                ),
            )
        )

    async def _settled(
        self, lines: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]]:
        """Which earlier shortfalls each of these inflow lines paid back.

        Stock issued with none on the shelf is a shortfall: a draw with no
        layer, owed by the item at that warehouse. The next stock in pays the
        debt before it lays a layer (the engine's rule 2), so that part of a
        receipt or a batch went to whoever drew on credit. The engine keeps the
        debt as one number — always max(−on hand, 0) — so each line's payment is
        read off its running quantity, and the debt is paid oldest-first.

        Returns ``{shortfall draw id: [(paying line, quantity)]}``.
        """
        line = InventoryLineCost
        before = line.running_quantity - line.quantity
        paying = (
            await self.db.execute(
                select(line.item_id, line.warehouse_id).where(
                    line.line_id.in_(lines), line.quantity > 0, before < 0
                )
            )
        ).all()
        if not paying:
            return {}
        pairs = list({(item, warehouse) for item, warehouse in paying})
        wanted = set(lines)
        draw = InventoryCostLayerConsumption
        events: dict[tuple, list[tuple]] = defaultdict(list)
        for line_id, item, warehouse, sequence, amount in (
            await self.db.execute(
                select(
                    line.line_id,
                    line.item_id,
                    line.warehouse_id,
                    line.posting_sequence,
                    func.least(line.quantity, -before),
                ).where(
                    tuple_(line.item_id, line.warehouse_id).in_(pairs),
                    line.quantity > 0,
                    before < 0,
                )
            )
        ).all():
            events[(item, warehouse)].append(
                ((sequence, str(line_id), 1), line_id, _d(amount))
            )
        for draw_id, consuming, item, warehouse, sequence, qty in (
            await self.db.execute(
                select(
                    draw.id,
                    draw.consuming_line_id,
                    draw.item_id,
                    draw.warehouse_id,
                    draw.posting_sequence,
                    draw.quantity,
                ).where(
                    draw.layer_id.is_(None),
                    tuple_(draw.item_id, draw.warehouse_id).in_(pairs),
                )
            )
        ).all():
            events[(item, warehouse)].append(
                ((sequence, str(consuming), 0), draw_id, -_d(qty))
            )
        out: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]] = defaultdict(list)
        for stream in events.values():
            owed: deque[list] = deque()
            for _key, ref, amount in sorted(stream, key=lambda e: e[0]):
                if amount < 0:
                    owed.append([ref, -amount])
                    continue
                while amount > 0 and owed:
                    head = owed[0]
                    take = min(head[1], amount)
                    if ref in wanted:
                        out[head[0]].append((ref, take))
                    head[1] -= take
                    amount -= take
                    if head[1] <= 0:
                        owed.popleft()
        return out
