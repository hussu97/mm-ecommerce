"""
The FIFO costing engine — a pure function of the immutable ledger.

Every figure the shop reads about what stock is worth (a layer's unit cost, a
sale's cost of goods, a batch's cost per piece, an item's average) is computed
here from the closed ledger lines, and nowhere else. There is one rule set and
two ways in:

* :meth:`CostingEngine.apply` over every line in ``posting_sequence`` order —
  the **replay**. A rebuild, a warehouse restatement after a void, and the
  estate sweep all run this, so they can never disagree with each other.
* The same :meth:`CostingEngine.apply` over one new posting, starting from the
  current projection instead of from nothing — the **fast path**
  (``fast=True``). It handles the everyday cases — any issue (a sale, a
  production draw), a priced receipt with nothing waiting for a price — and
  raises :class:`NeedsReplay` for anything that can change a cost already
  booked (a receipt that prices waiting stock, a void, a count overage, a cost
  adjustment). Known costs always match a replay exactly; the one thing the fast
  path may leave stale is an *estimate* (stock still waiting on a price), which
  the next replay of that warehouse — or the nightly one — refreshes.

The rules, per (item, warehouse):

1. Issues draw the oldest layers first. What no layer covers becomes a
   shortfall **debt**; Σ remaining layers is always ``max(on hand, 0)`` and the
   debt is always ``max(-on hand, 0)``.
2. Any stock-adding line first settles the debt; only the remainder becomes a
   layer. (Before this engine, count overages skipped that step and left phantom
   layers behind — Cream Cheese at Sharjah carried 1000 g more in layers than on
   the shelf.)
3. A **priced** inflow — a purchase order receipt that was never voided, a
   production batch, a transfer in, a costed opening balance — carries a cost.
   Anything else that adds stock with no cost of its own (a count overage over an
   empty queue, a zero-cost opening balance, a shift-report receipt with no PO)
   is **provisional**, and so is every shortfall: its cost is "whatever the next
   priced inflow of this item at this warehouse costs". When that inflow lands,
   every provisional cost waiting on it — layers, what was already consumed from
   them, shortfall debt — takes its price, and the change carries on into the
   batches, sales and transfers that used them.
4. Until then a provisional cost is *estimated* at the last priced cost at the
   warehouse, else (for a made item) its current recipe cost there, else the
   latest purchase-order cost of the item anywhere, else 0 — and flagged so
   screens can say so.
5. A voided receipt is never a pricing source, not even for what was used
   from it before the void: its stock is provisional like a shortfall, so a
   mis-keyed PO (2000 g entered for 20000 g) that was consumed and then
   re-received correctly costs those uses at the corrected price. Reversing it
   removes its own layer first, then draws FIFO like any issue.
6. A cost adjustment rescales what survives. Those posted before the v3
   cutover were workarounds for the bugs this engine replaced, and are skipped.

Costs are built as small lazy expressions (:class:`Fixed`, :class:`Pending`,
:class:`Batch`, …) and evaluated once at the end, which is what makes the
retroactive re-costing a single pass instead of a fixed-point loop.
"""

from __future__ import annotations

import bisect
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.core.money import COST, QUANTITY

__all__ = [
    "LedgerLine",
    "SeedLayer",
    "SeedState",
    "CostingEngine",
    "NeedsReplay",
    "Projection",
    "LayerOut",
    "ConsumptionOut",
    "LineCostOut",
    "LevelOut",
    "layer_id",
    "consumption_id",
]

ZERO = Decimal("0")

#: Namespace for the projection's deterministic ids, so a replay that reaches the
#: same answer rewrites nothing.
_ID_NAMESPACE = uuid.UUID("7b0f4d8e-5c2a-4f55-9d5e-3f1f6c0a9b21")

# Ledger vocabulary, spelled as strings so this module stays free of the ORM.
PURCHASING = "purchasing"
TRANSFER_SEND = "transfer_send"
TRANSFER_RECEIVE = "transfer_receive"
PRODUCTION = "production"
CONSUMPTION_FROM_PRODUCTION = "consumption_from_production"
WASTE_FROM_PRODUCTION = "waste_from_production"
CONSUMPTION_FROM_ORDERS = "consumption_from_orders"
RETURN_FROM_ORDERS = "return_from_orders"
COST_ADJUSTMENT = "cost_adjustment"
INVENTORY_COUNT = "inventory_count"
OPENING_BALANCE = "opening_balance"

#: The report's "Received" column posts purchasing with no purchase order and no
#: price of its own: stock of unknown cost, not a price signal.
SHIFT_REPORT_SOURCE = "shift_inventory_report"
_TRANSFER_SOURCES = {"transfer": "transfer", "transfer_order": "transfer"}


def layer_id(line_id: uuid.UUID, index: int) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, f"layer:{line_id}:{index}")


def consumption_id(line_id: uuid.UUID, ordinal: int) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, f"consumption:{line_id}:{ordinal}")


def _cost(value: Decimal) -> Decimal:
    return value.quantize(COST)


class NeedsReplay(Exception):
    """The fast path met a case only a replay can cost correctly."""


# ─── Inputs ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LedgerLine:
    """One closed (or being-posted) ledger line, in storage units."""

    line_id: uuid.UUID
    transaction_id: uuid.UUID
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    branch_id: uuid.UUID
    sequence: int
    type: str
    #: Signed storage quantity (``signed_quantity``).
    delta: Decimal
    #: The cost the line was booked at, per storage unit.
    unit_cost: Decimal
    posted_at: datetime | None = None
    purchase_order_id: uuid.UUID | None = None
    source_type: str | None = None
    source_id: str | None = None
    correction_group_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    reverses_transaction_id: uuid.UUID | None = None
    reverses_line_id: uuid.UUID | None = None

    @property
    def sort_key(self) -> tuple[int, str]:
        return (self.sequence, str(self.line_id))


@dataclass(slots=True)
class SeedLayer:
    """A surviving layer read back from the projection, for the fast path."""

    line_id: uuid.UUID
    index: int
    transaction_id: uuid.UUID
    purchase_order_id: uuid.UUID | None
    source_kind: str
    sequence: int
    original: Decimal
    remaining: Decimal
    unit_cost: Decimal
    priced_by: uuid.UUID | None
    received_at: datetime | None
    provisional: bool = False


@dataclass(slots=True)
class SeedState:
    """The fast path's starting point for one (item, warehouse)."""

    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    branch_id: uuid.UUID
    quantity: Decimal
    layers: list[SeedLayer]
    #: Stock of this item here is still waiting on its next priced inflow — a
    #: provisional layer or consumption that a priced receipt would re-cost.
    has_open_pending: bool
    #: Level average, for an issue that exhausts every layer.
    last_cost: Decimal


# ─── Cost expressions ────────────────────────────────────────────────────────


@dataclass(slots=True)
class Resolved:
    unit_cost: Decimal
    provisional: bool
    priced_by: uuid.UUID | None


class Cost:
    """A per-storage-unit cost that may not be knowable until the replay ends."""

    __slots__ = ("_memo", "_busy")

    def __init__(self) -> None:
        self._memo: Resolved | None = None
        self._busy = False

    def resolve(self) -> Resolved:
        if self._memo is not None:
            return self._memo
        if self._busy:
            # A cycle cannot arise from an acyclic recipe graph; if one ever
            # does, value it at zero and say so rather than recurse forever.
            return Resolved(ZERO, True, None)
        self._busy = True
        try:
            self._memo = self._resolve()
        finally:
            self._busy = False
        return self._memo

    def _resolve(self) -> Resolved:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def is_pending(self) -> bool:
        return False


class Fixed(Cost):
    __slots__ = ("value", "priced_by", "provisional")

    def __init__(
        self,
        value: Decimal,
        priced_by: uuid.UUID | None = None,
        provisional: bool = False,
    ) -> None:
        super().__init__()
        self.value = _cost(Decimal(value))
        self.priced_by = priced_by
        self.provisional = provisional

    def _resolve(self) -> Resolved:
        return Resolved(self.value, self.provisional, self.priced_by)


class Pending(Cost):
    """Cost of the next priced inflow of this item at this warehouse."""

    __slots__ = ("target", "estimate", "estimate_value")

    def __init__(self, estimate: Cost | None, estimate_value: Decimal) -> None:
        super().__init__()
        self.target: Cost | None = None
        self.estimate = estimate
        self.estimate_value = estimate_value

    @property
    def is_pending(self) -> bool:
        return self.target is None

    def _resolve(self) -> Resolved:
        if self.target is not None:
            return self.target.resolve()
        if self.estimate is not None:
            guess = self.estimate.resolve()
            return Resolved(guess.unit_cost, True, guess.priced_by)
        return Resolved(_cost(self.estimate_value), True, None)


@dataclass(slots=True)
class Draw:
    """Quantity taken at a cost — one consumption, or one restored slice."""

    quantity: Decimal
    cost: Cost
    layer_key: tuple[uuid.UUID, int] | None
    is_shortfall: bool = False

    def value(self) -> Decimal:
        return self.quantity * self.cost.resolve().unit_cost


class Derived(Cost):
    """Σ value of some draws ÷ a quantity — a batch, a transfer, a return."""

    __slots__ = ("draws", "quantity", "priced_by")

    def __init__(self, priced_by: uuid.UUID | None) -> None:
        super().__init__()
        self.draws: list[Draw] = []
        self.quantity = ZERO
        self.priced_by = priced_by

    def _resolve(self) -> Resolved:
        if self.quantity <= 0:
            return Resolved(ZERO, True, self.priced_by)
        total = ZERO
        provisional = False
        for draw in self.draws:
            resolved = draw.cost.resolve()
            total += draw.quantity * resolved.unit_cost
            provisional = provisional or resolved.provisional
        return Resolved(_cost(total / self.quantity), provisional, self.priced_by)


class RescaleGroup:
    """One cost adjustment: the layers it saw and the average it asked for."""

    __slots__ = ("target", "members", "_factor")

    def __init__(self, target: Decimal, members: list[tuple[Decimal, Cost]]):
        self.target = _cost(target)
        self.members = members
        self._factor: tuple[Decimal, bool] | None = None

    def old_value(self) -> Decimal:
        return sum((qty * cost.resolve().unit_cost for qty, cost in self.members), ZERO)

    def total_quantity(self) -> Decimal:
        return sum((qty for qty, _ in self.members), ZERO)

    def factor(self) -> tuple[Decimal, bool]:
        """(multiplier, absolute): absolute when there was no value to scale."""
        if self._factor is None:
            old = self.old_value()
            if old > 0:
                self._factor = (self.target * self.total_quantity() / old, False)
            else:
                self._factor = (ZERO, True)
        return self._factor


class Scaled(Cost):
    __slots__ = ("base", "group", "priced_by")

    def __init__(self, base: Cost, group: RescaleGroup, priced_by: uuid.UUID):
        super().__init__()
        self.base = base
        self.group = group
        self.priced_by = priced_by

    def _resolve(self) -> Resolved:
        factor, absolute = self.group.factor()
        if absolute:
            return Resolved(self.group.target, False, self.priced_by)
        base = self.base.resolve()
        return Resolved(_cost(base.unit_cost * factor), False, self.priced_by)


class FinalAverage(Cost):
    """An item's average at a warehouse once the whole replay has run."""

    __slots__ = ("engine", "key")

    def __init__(self, engine: "CostingEngine", key: tuple[uuid.UUID, uuid.UUID]):
        super().__init__()
        self.engine = engine
        self.key = key

    def _resolve(self) -> Resolved:
        state = self.engine.states.get(self.key)
        if state is None:
            return Resolved(ZERO, True, None)
        qty = ZERO
        value = ZERO
        provisional = False
        for layer in state.live():
            resolved = layer.cost.resolve()
            qty += layer.remaining
            value += layer.remaining * resolved.unit_cost
            provisional = provisional or resolved.provisional
        if qty > 0:
            return Resolved(_cost(value / qty), provisional, None)
        if state.last_cost is not None:
            return state.last_cost.resolve()
        return Resolved(ZERO, True, None)


class RecipeEstimate(Cost):
    """What one unit of a made item costs from its current recipe, here.

    The estimate for made stock that no production batch accounts for — a count
    found some, or it arrived before production was recorded. Built from the
    replay's own final ingredient costs, so it is as deterministic as the rest.
    """

    __slots__ = ("components",)

    def __init__(self, components: list[tuple[Decimal, Cost]]):
        super().__init__()
        self.components = components

    def _resolve(self) -> Resolved:
        total = sum(
            (qty * cost.resolve().unit_cost for qty, cost in self.components), ZERO
        )
        return Resolved(_cost(total), True, None)


# ─── State ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Layer:
    line_id: uuid.UUID
    index: int
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    branch_id: uuid.UUID
    transaction_id: uuid.UUID
    purchase_order_id: uuid.UUID | None
    source_kind: str
    sequence: int
    original: Decimal
    remaining: Decimal
    cost: Cost
    received_at: datetime | None
    exhausted_at: datetime | None = None
    #: Seeded from the projection (fast path) rather than born in this run.
    seeded: bool = False
    touched: bool = False
    #: A seeded layer whose cost is still an estimate (fast path only).
    seeded_provisional: bool = False

    @property
    def key(self) -> tuple[uuid.UUID, int]:
        return (self.line_id, self.index)


@dataclass(slots=True)
class ItemState:
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    branch_id: uuid.UUID
    quantity: Decimal = ZERO
    debt: Decimal = ZERO
    layers: list[Layer] = field(default_factory=list)
    head: int = 0
    open_pendings: list[Pending] = field(default_factory=list)
    last_priced: Cost | None = None
    last_cost: Cost | None = None
    #: Seeded state that is still waiting on a price (fast path only).
    seeded_provisional: bool = False
    seeded_inconsistent: bool = False
    last_sequence: int | None = None
    #: Running value of the surviving layers, as lazy terms.
    value_terms: list[tuple[Decimal, Cost]] = field(default_factory=list)

    def live(self):
        for layer in self.layers[self.head :]:
            if layer.remaining > 0:
                yield layer

    def advance_head(self) -> None:
        while self.head < len(self.layers) and self.layers[self.head].remaining <= 0:
            self.head += 1

    def reopen(self, layer: Layer) -> None:
        position = self.layers.index(layer)
        if position < self.head:
            self.head = position


@dataclass(slots=True)
class LineRecord:
    line: LedgerLine
    draws: list[Draw] = field(default_factory=list)
    #: Value the line added (inflow) as lazy terms; issues use ``draws``.
    value_terms: list[tuple[Decimal, Cost]] = field(default_factory=list)
    unit: Cost | None = None
    running_quantity: Decimal = ZERO
    #: Snapshot of the item's value-term count after this line, for the running
    #: value — evaluated at the end.
    value_mark: int = 0
    rescale: RescaleGroup | None = None
    superseded: bool = False


# ─── Outputs ─────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LayerOut:
    id: uuid.UUID
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    branch_id: uuid.UUID
    source_transaction_id: uuid.UUID
    source_line_id: uuid.UUID
    purchase_order_id: uuid.UUID | None
    source_kind: str
    posting_sequence: int
    layer_index: int
    original_quantity: Decimal
    remaining_quantity: Decimal
    unit_cost: Decimal
    cost_is_provisional: bool
    priced_by_line_id: uuid.UUID | None
    received_at: datetime | None
    exhausted_at: datetime | None
    seeded: bool
    touched: bool


@dataclass(slots=True)
class ConsumptionOut:
    id: uuid.UUID
    consuming_line_id: uuid.UUID
    layer_id: uuid.UUID | None
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    posting_sequence: int
    is_shortfall: bool
    cost_is_provisional: bool
    priced_by_line_id: uuid.UUID | None


@dataclass(slots=True)
class LineCostOut:
    line_id: uuid.UUID
    transaction_id: uuid.UUID
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    branch_id: uuid.UUID
    posting_sequence: int
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    booked_unit_cost: Decimal
    is_provisional: bool
    priced_by_line_id: uuid.UUID | None
    running_quantity: Decimal
    running_value: Decimal
    superseded: bool


@dataclass(slots=True)
class LevelOut:
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    average_cost: Decimal
    through_sequence: int | None


@dataclass(slots=True)
class Projection:
    layers: list[LayerOut]
    consumptions: list[ConsumptionOut]
    line_costs: dict[uuid.UUID, LineCostOut]
    levels: dict[tuple[uuid.UUID, uuid.UUID], LevelOut]


# ─── Engine ──────────────────────────────────────────────────────────────────


class CostingEngine:
    """Replays ledger lines into FIFO layers, consumptions and line costs.

    ``reversed_transactions`` is every transaction a closed reversal undoes —
    a voided receipt must not price anything. ``po_prices`` is
    ``{item_id: [(sequence, unit_cost), …]}`` for priced purchase-order receipts
    across the whole estate, the fallback estimate for stock still waiting on a
    price. ``external_sends`` carries the projected cost of transfer sends posted
    in a warehouse outside this replay's scope, keyed like
    :meth:`_transfer_key`, as ``(quantity, total_cost, provisional)``.
    """

    def __init__(
        self,
        *,
        cutover_sequence: int | None,
        reversed_transactions: set[uuid.UUID] | None = None,
        po_prices: dict[uuid.UUID, list[tuple[int, Decimal]]] | None = None,
        external_sends: dict[tuple, tuple[Decimal, Decimal, bool]] | None = None,
        external_group_inputs: dict[uuid.UUID, list[tuple[Decimal, Decimal, bool]]]
        | None = None,
        fast: bool = False,
        seeds: list[SeedState] | None = None,
        recipes: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]] | None = None,
    ) -> None:
        self.cutover_sequence = cutover_sequence
        self.reversed = reversed_transactions or set()
        self.po_prices = {
            item: sorted(prices) for item, prices in (po_prices or {}).items()
        }
        self.external_sends = external_sends or {}
        self.external_group_inputs = external_group_inputs or {}
        self.fast = fast
        #: {made item: [(ingredient, storage qty per storage unit made), …]} —
        #: its current recipe, for estimating made stock no batch accounts for.
        self.recipes = recipes or {}
        self.states: dict[tuple[uuid.UUID, uuid.UUID], ItemState] = {}
        self.records: dict[uuid.UUID, LineRecord] = {}
        self.order: list[uuid.UUID] = []
        self.layers_by_line: dict[uuid.UUID, list[Layer]] = defaultdict(list)
        self.batches: dict[uuid.UUID, Derived] = {}
        self.group_draws: dict[uuid.UUID, list[Draw]] = defaultdict(list)
        self.send_draws: dict[tuple, tuple[Decimal, list[Draw]]] = {}
        self.sale_draws: dict[tuple, tuple[Decimal, list[Draw]]] = {}
        self._last_sequence: tuple[int, str] | None = None
        for seed in seeds or []:
            self._seed(seed)

    # ── seeding (fast path) ──

    def _seed(self, seed: SeedState) -> None:
        state = ItemState(seed.item_id, seed.warehouse_id, seed.branch_id)
        state.quantity = Decimal(seed.quantity)
        state.debt = max(-state.quantity, ZERO)
        state.seeded_provisional = seed.has_open_pending
        state.last_cost = Fixed(seed.last_cost)
        for row in sorted(seed.layers, key=lambda r: (r.sequence, r.index)):
            layer = Layer(
                line_id=row.line_id,
                index=row.index,
                item_id=seed.item_id,
                warehouse_id=seed.warehouse_id,
                branch_id=seed.branch_id,
                transaction_id=row.transaction_id,
                purchase_order_id=row.purchase_order_id,
                source_kind=row.source_kind,
                sequence=row.sequence,
                original=Decimal(row.original),
                remaining=Decimal(row.remaining),
                cost=Fixed(row.unit_cost, row.priced_by, row.provisional),
                received_at=row.received_at,
                seeded=True,
                seeded_provisional=row.provisional,
            )
            state.layers.append(layer)
            self.layers_by_line[row.line_id].append(layer)
            state.value_terms.append((layer.remaining, layer.cost))
        layered = sum((layer.remaining for layer in state.layers), ZERO)
        # The stored projection no longer agrees with the level (drift from
        # before v3, or a level edited by hand): only a replay can say which.
        state.seeded_inconsistent = layered != max(state.quantity, ZERO)
        self.states[(seed.item_id, seed.warehouse_id)] = state

    def _state(self, line: LedgerLine) -> ItemState:
        key = (line.item_id, line.warehouse_id)
        state = self.states.get(key)
        if state is None:
            if self.fast:
                raise NeedsReplay(f"no seeded state for {key}")
            state = ItemState(line.item_id, line.warehouse_id, line.branch_id)
            self.states[key] = state
        return state

    # ── helpers ──

    def _estimate(self, state: ItemState, line: LedgerLine) -> Pending:
        prices = self.po_prices.get(line.item_id, [])
        # Latest purchase-order price posted before this line, anywhere.
        at = bisect.bisect_left(prices, (line.sequence, Decimal("-1")))
        fallback = prices[at - 1][1] if at > 0 else ZERO
        estimate = state.last_priced
        if estimate is None and line.item_id in self.recipes:
            estimate = RecipeEstimate(
                [
                    (qty, FinalAverage(self, (ingredient, line.warehouse_id)))
                    for ingredient, qty in self.recipes[line.item_id]
                ]
            )
        return Pending(estimate, fallback)

    @staticmethod
    def _transfer_key(line: LedgerLine) -> tuple | None:
        source = _TRANSFER_SOURCES.get(line.source_type or "")
        if source is None or not line.source_id:
            return None
        return (source, line.source_id, line.item_id)

    def _add_layer(
        self,
        state: ItemState,
        line: LedgerLine,
        quantity: Decimal,
        cost: Cost,
        kind: str,
        index: int = 0,
    ) -> None:
        layer = Layer(
            line_id=line.line_id,
            index=index,
            item_id=line.item_id,
            warehouse_id=line.warehouse_id,
            branch_id=line.branch_id,
            transaction_id=line.transaction_id,
            purchase_order_id=line.purchase_order_id,
            source_kind=kind,
            sequence=line.sequence,
            original=quantity,
            remaining=quantity,
            cost=cost,
            received_at=line.posted_at,
            touched=True,
        )
        state.layers.append(layer)
        self.layers_by_line[line.line_id].append(layer)
        state.value_terms.append((quantity, cost))

    def _settle_then_layer(
        self,
        state: ItemState,
        line: LedgerLine,
        quantity: Decimal,
        cost: Cost,
        kind: str,
        index: int = 0,
    ) -> None:
        """Rule 2: an inflow pays down the shortfall debt before it stacks."""
        settle = min(quantity, state.debt)
        state.debt -= settle
        remainder = quantity - settle
        if remainder > 0:
            self._add_layer(state, line, remainder, cost, kind, index)

    def _price_pending(self, state: ItemState, cost: Cost) -> None:
        """Rule 3: a priced inflow prices everything waiting on this item."""
        if self.fast and (state.seeded_provisional or state.open_pendings):
            raise NeedsReplay("priced inflow with stock waiting on a price")
        for pending in state.open_pendings:
            pending.target = cost
        state.open_pendings.clear()
        state.last_priced = cost

    def _draw(
        self, state: ItemState, record: LineRecord, quantity: Decimal
    ) -> list[Draw]:
        """Rule 1: take *quantity* oldest-first, then book the rest as debt."""
        line = record.line
        outstanding = quantity
        draws: list[Draw] = []
        for layer in state.live():
            if outstanding <= 0:
                break
            take = min(layer.remaining, outstanding)
            layer.remaining -= take
            layer.touched = True
            if layer.remaining <= 0:
                layer.exhausted_at = line.posted_at
            state.value_terms.append((-take, layer.cost))
            state.last_cost = layer.cost
            draws.append(Draw(take, layer.cost, layer.key))
            outstanding -= take
        state.advance_head()
        if outstanding > 0:
            # A shortfall waits on the next priced receipt. On the fast path it
            # is estimated at the level's current cost; the receipt that prices
            # it forces a replay, which re-costs it properly.
            pending = (
                Pending(state.last_cost, ZERO)
                if self.fast
                else self._estimate(state, line)
            )
            state.open_pendings.append(pending)
            state.debt += outstanding
            draws.append(Draw(outstanding, pending, None, is_shortfall=True))
        record.draws.extend(draws)
        return draws

    # ── the rule set ──

    def apply(self, line: LedgerLine) -> None:
        key = line.sort_key
        if self._last_sequence is not None and key < self._last_sequence:
            raise ValueError("ledger lines must be applied in posting order")
        self._last_sequence = key
        record = LineRecord(line)
        self.records[line.line_id] = record
        self.order.append(line.line_id)
        state = self._state(line)
        if self.fast and state.seeded_inconsistent:
            raise NeedsReplay("stored layers disagree with the level")
        delta = Decimal(line.delta)

        if line.type == COST_ADJUSTMENT:
            self._cost_adjustment(state, record)
        elif line.reverses_transaction_id is not None and line.reverses_line_id:
            if self.fast:
                raise NeedsReplay("reversal")
            if delta > 0:
                self._undo_issue(state, record, delta)
            elif delta < 0:
                self._undo_receipt(state, record, -delta)
        elif delta > 0:
            self._inflow(state, record, delta)
        elif delta < 0:
            self._outflow(state, record, -delta)

        state.quantity += delta
        state.last_sequence = line.sequence
        record.running_quantity = state.quantity
        record.value_mark = len(state.value_terms)

    def _cost_adjustment(self, state: ItemState, record: LineRecord) -> None:
        line = record.line
        if self.cutover_sequence is not None and line.sequence <= self.cutover_sequence:
            # Rule 6: a pre-v3 cost adjustment patched a bug this engine fixed.
            record.superseded = True
            return
        if self.fast:
            raise NeedsReplay("cost adjustment")
        live = list(state.live())
        group = RescaleGroup(
            Decimal(line.unit_cost), [(layer.remaining, layer.cost) for layer in live]
        )
        for layer in live:
            scaled = Scaled(layer.cost, group, line.line_id)
            state.value_terms.append((-layer.remaining, layer.cost))
            state.value_terms.append((layer.remaining, scaled))
            layer.cost = scaled
            layer.touched = True
        record.rescale = group
        target = Fixed(line.unit_cost, line.line_id)
        state.last_cost = target
        record.unit = target

    def _inflow(self, state: ItemState, record: LineRecord, quantity: Decimal) -> None:
        line = record.line
        cost, kind, priced = self._inflow_cost(state, line)
        if priced:
            self._price_pending(state, cost)
        elif cost.is_pending:
            # Only a cost nobody has priced yet waits on the next receipt; an
            # overage priced *from* an already-resolved layer keeps that price.
            if self.fast:
                raise NeedsReplay("stock of unknown cost")
            state.open_pendings.append(cost)
        if self.fast and state.debt > 0:
            raise NeedsReplay("inflow onto negative stock")
        self._settle_then_layer(state, line, quantity, cost, kind)
        record.unit = cost
        record.value_terms.append((quantity, cost))
        state.last_cost = cost

    def _inflow_cost(
        self, state: ItemState, line: LedgerLine
    ) -> tuple[Cost, str, bool]:
        """(cost, layer kind, is a pricing source) for one stock-adding line."""
        booked = Decimal(line.unit_cost)
        kind = line.type
        if line.type == PURCHASING:
            if (
                line.source_type == SHIFT_REPORT_SOURCE
                and line.purchase_order_id is None
            ):
                return self._estimate(state, line), PURCHASING, False
            if booked <= 0:
                return self._estimate(state, line), PURCHASING, False
            if line.transaction_id in self.reversed:
                # Rule 5: its booked price was wrong or never real, so what was
                # drawn from it before the void waits on the next priced inflow.
                return self._estimate(state, line), PURCHASING, False
            return Fixed(booked, line.line_id), PURCHASING, True
        if line.type == OPENING_BALANCE:
            if booked > 0:
                return Fixed(booked, line.line_id), OPENING_BALANCE, True
            return self._estimate(state, line), OPENING_BALANCE, False
        if line.type == PRODUCTION:
            batch = self._batch(line)
            if batch is not None:
                return batch, PRODUCTION, True
            return self._estimate(state, line), PRODUCTION, False
        if line.type == TRANSFER_RECEIVE:
            derived = self._transfer_in(line)
            if derived is not None:
                return derived, TRANSFER_RECEIVE, True
            if booked > 0:
                return Fixed(booked, line.line_id), TRANSFER_RECEIVE, True
            return self._estimate(state, line), TRANSFER_RECEIVE, False
        if line.type == RETURN_FROM_ORDERS:
            if self.fast:
                # Priced from the sale it undoes, which the fast path never sees.
                raise NeedsReplay("return from orders")
            derived = self._sale_return(line)
            if derived is not None:
                return derived, RETURN_FROM_ORDERS, False
            if booked > 0:
                return Fixed(booked, line.line_id), RETURN_FROM_ORDERS, False
            return self._estimate(state, line), RETURN_FROM_ORDERS, False
        # Count overage, positive adjustment, a legacy reversal with no line
        # link: found stock is priced at the oldest cost still on the shelf.
        kind = (
            "count_overage" if line.type == INVENTORY_COUNT else "positive_adjustment"
        )
        for layer in state.live():
            if not layer.cost.is_pending and not layer.seeded_provisional:
                return layer.cost, kind, False
            if self.fast:
                raise NeedsReplay("overage priced from provisional stock")
        return self._estimate(state, line), kind, False

    def _batch(self, line: LedgerLine) -> Derived | None:
        group = line.correction_group_id
        if group is None:
            return None
        draws = self.group_draws.get(group)
        external = self.external_group_inputs.get(group)
        if not draws and not external:
            return None
        if self.fast and any(provisional for _, _, provisional in external or []):
            raise NeedsReplay("batch made from stock still waiting on a price")
        batch = self.batches.get(group)
        if batch is None:
            batch = Derived(line.line_id)
            batch.draws.extend(draws or [])
            for qty, total, provisional in external or []:
                if qty > 0:
                    batch.draws.append(
                        Draw(qty, Fixed(total / qty, None, provisional), None)
                    )
            self.batches[group] = batch
        batch.quantity += Decimal(line.delta)
        return batch

    def _transfer_in(self, line: LedgerLine) -> Derived | None:
        key = self._transfer_key(line)
        if key is None:
            return None
        derived = Derived(line.line_id)
        local = self.send_draws.get(key)
        if local is not None:
            sent, draws = local
            derived.draws.extend(draws)
            derived.quantity = sent
            return derived
        external = self.external_sends.get(key)
        if external is not None:
            sent, total, provisional = external
            if sent <= 0:
                return None
            derived.draws.append(
                Draw(sent, Fixed(total / sent, None, provisional), None)
            )
            derived.quantity = sent
            return derived
        return None

    def _sale_return(self, line: LedgerLine) -> Derived | None:
        if line.order_id is None:
            return None
        sale = self.sale_draws.get((line.order_id, line.item_id, line.warehouse_id))
        if sale is None:
            return None
        sold, draws = sale
        derived = Derived(line.line_id)
        derived.draws.extend(draws)
        derived.quantity = sold
        return derived

    def _outflow(self, state: ItemState, record: LineRecord, quantity: Decimal) -> None:
        line = record.line
        draws = self._draw(state, record, quantity)
        if line.type in (CONSUMPTION_FROM_PRODUCTION, WASTE_FROM_PRODUCTION):
            if line.correction_group_id is not None:
                self.group_draws[line.correction_group_id].extend(draws)
        elif line.type == TRANSFER_SEND:
            key = self._transfer_key(line)
            if key is not None:
                sent, previous = self.send_draws.get(key, (ZERO, []))
                self.send_draws[key] = (sent + quantity, previous + draws)
        elif line.type == CONSUMPTION_FROM_ORDERS and line.order_id is not None:
            self.sale_draws[(line.order_id, line.item_id, line.warehouse_id)] = (
                quantity,
                draws,
            )

    def _undo_issue(
        self, state: ItemState, record: LineRecord, quantity: Decimal
    ) -> None:
        """Put an issue back on the exact layers it drew from.

        Each restored slice first pays down any debt that has built up since
        (rule 2); a slice that was a shortfall comes back as a fresh layer at
        the shortfall's own (possibly still pending) cost.
        """
        line = record.line
        original = self.records.get(line.reverses_line_id)
        outstanding = quantity
        slices = list(original.draws) if original is not None else []
        for draw in slices:
            if outstanding <= 0:
                break
            amount = min(draw.quantity, outstanding)
            outstanding -= amount
            record.value_terms.append((amount, draw.cost))
            settle = min(amount, state.debt)
            state.debt -= settle
            remainder = amount - settle
            if remainder <= 0:
                continue
            layer = None
            if draw.layer_key is not None:
                layer = next(
                    (
                        candidate
                        for candidate in self.layers_by_line.get(draw.layer_key[0], [])
                        if candidate.index == draw.layer_key[1]
                    ),
                    None,
                )
            if layer is not None and layer.warehouse_id == line.warehouse_id:
                layer.remaining += remainder
                layer.exhausted_at = None
                layer.touched = True
                state.reopen(layer)
                state.value_terms.append((remainder, layer.cost))
            else:
                self._add_layer(
                    state, line, remainder, draw.cost, "positive_adjustment", index=1
                )
        if outstanding > 0:
            # Nothing left to put back exactly: treat the rest as found stock.
            cost, kind, _ = self._inflow_cost(state, line)
            if cost.is_pending:
                state.open_pendings.append(cost)
            record.value_terms.append((outstanding, cost))
            self._settle_then_layer(state, line, outstanding, cost, kind, index=2)

    def _undo_receipt(
        self, state: ItemState, record: LineRecord, quantity: Decimal
    ) -> None:
        """Take a receipt back out: its own layer first, then FIFO, then debt."""
        line = record.line
        outstanding = quantity
        for layer in self.layers_by_line.get(line.reverses_line_id, []):
            if outstanding <= 0:
                break
            if layer.warehouse_id != line.warehouse_id or layer.remaining <= 0:
                continue
            take = min(layer.remaining, outstanding)
            layer.remaining -= take
            layer.touched = True
            if layer.remaining <= 0:
                layer.exhausted_at = line.posted_at
            state.value_terms.append((-take, layer.cost))
            record.draws.append(Draw(take, layer.cost, layer.key))
            outstanding -= take
        state.advance_head()
        if outstanding > 0:
            self._draw(state, record, outstanding)

    # ── evaluation ──

    def project(self) -> Projection:
        """Evaluate every lazy cost into the projection rows."""
        layers: list[LayerOut] = []
        for state in self.states.values():
            for layer in state.layers:
                resolved = layer.cost.resolve()
                layers.append(
                    LayerOut(
                        id=layer_id(layer.line_id, layer.index),
                        item_id=layer.item_id,
                        warehouse_id=layer.warehouse_id,
                        branch_id=layer.branch_id,
                        source_transaction_id=layer.transaction_id,
                        source_line_id=layer.line_id,
                        purchase_order_id=layer.purchase_order_id,
                        source_kind=layer.source_kind,
                        posting_sequence=layer.sequence,
                        layer_index=layer.index,
                        original_quantity=layer.original.quantize(Decimal("0.000001")),
                        remaining_quantity=max(layer.remaining, ZERO).quantize(
                            Decimal("0.000001")
                        ),
                        unit_cost=resolved.unit_cost,
                        cost_is_provisional=resolved.provisional,
                        priced_by_line_id=resolved.priced_by,
                        received_at=layer.received_at,
                        exhausted_at=layer.exhausted_at
                        if layer.remaining <= 0
                        else None,
                        seeded=layer.seeded,
                        touched=layer.touched,
                    )
                )

        consumptions: list[ConsumptionOut] = []
        line_costs: dict[uuid.UUID, LineCostOut] = {}
        running_value: dict[tuple[uuid.UUID, uuid.UUID], tuple[int, Decimal]] = {}
        for line_id_ in self.order:
            record = self.records[line_id_]
            line = record.line
            state = self.states[(line.item_id, line.warehouse_id)]
            provisional = False
            priced_by: uuid.UUID | None = None
            if record.draws:
                total = ZERO
                for ordinal, draw in enumerate(record.draws):
                    resolved = draw.cost.resolve()
                    value = draw.quantity * resolved.unit_cost
                    total += value
                    provisional = provisional or resolved.provisional
                    priced_by = priced_by or resolved.priced_by
                    consumptions.append(
                        ConsumptionOut(
                            id=consumption_id(line.line_id, ordinal),
                            consuming_line_id=line.line_id,
                            layer_id=layer_id(*draw.layer_key)
                            if draw.layer_key
                            else None,
                            item_id=line.item_id,
                            warehouse_id=line.warehouse_id,
                            quantity=draw.quantity.quantize(Decimal("0.000001")),
                            unit_cost=resolved.unit_cost,
                            total_cost=value.quantize(Decimal("0.0001")),
                            posting_sequence=line.sequence,
                            is_shortfall=draw.is_shortfall,
                            cost_is_provisional=resolved.provisional,
                            priced_by_line_id=resolved.priced_by,
                        )
                    )
                quantity = sum((draw.quantity for draw in record.draws), ZERO)
                unit = _cost(total / quantity) if quantity > 0 else ZERO
            elif record.rescale is not None:
                group = record.rescale
                qty = group.total_quantity()
                total = (group.target * qty if qty > 0 else ZERO) - group.old_value()
                unit = group.target
                priced_by = line.line_id
            elif record.value_terms:
                total = ZERO
                for qty, cost in record.value_terms:
                    resolved = cost.resolve()
                    total += qty * resolved.unit_cost
                    provisional = provisional or resolved.provisional
                    priced_by = priced_by or resolved.priced_by
                unit = record.unit.resolve().unit_cost if record.unit else ZERO
            else:
                total = ZERO
                unit = record.unit.resolve().unit_cost if record.unit else ZERO

            key = (line.item_id, line.warehouse_id)
            mark, value = running_value.get(key, (0, ZERO))
            for qty, cost in state.value_terms[mark : record.value_mark]:
                value += qty * cost.resolve().unit_cost
            running_value[key] = (record.value_mark, value)

            line_costs[line.line_id] = LineCostOut(
                line_id=line.line_id,
                transaction_id=line.transaction_id,
                item_id=line.item_id,
                warehouse_id=line.warehouse_id,
                branch_id=line.branch_id,
                posting_sequence=line.sequence,
                quantity=Decimal(line.delta),
                unit_cost=unit,
                total_cost=total.quantize(Decimal("0.0001")),
                booked_unit_cost=_cost(Decimal(line.unit_cost)),
                is_provisional=provisional,
                priced_by_line_id=priced_by,
                running_quantity=record.running_quantity,
                running_value=max(value, ZERO).quantize(Decimal("0.0001")),
                superseded=record.superseded,
            )

        levels: dict[tuple[uuid.UUID, uuid.UUID], LevelOut] = {}
        for key, state in self.states.items():
            qty = ZERO
            value = ZERO
            for layer in state.live():
                qty += layer.remaining
                value += layer.remaining * layer.cost.resolve().unit_cost
            if qty > 0:
                average = _cost(value / qty)
            elif state.last_cost is not None:
                average = state.last_cost.resolve().unit_cost
            else:
                average = ZERO
            levels[key] = LevelOut(
                item_id=state.item_id,
                warehouse_id=state.warehouse_id,
                quantity=state.quantity.quantize(QUANTITY),
                average_cost=average,
                through_sequence=state.last_sequence,
            )
        return Projection(layers, consumptions, line_costs, levels)
