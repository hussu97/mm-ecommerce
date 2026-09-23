"""The v3 FIFO costing engine, as a pure function of the ledger.

The two ledgers at the top are the real Sharjah Kitchen histories that exposed
the old engine's bugs (the Cream Cheese and Mascarpone cost-layer audit of
2026-09-23); every other test isolates one rule.
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest

from app.services.inventory.costing_engine import (
    CostingEngine,
    LedgerLine,
    NeedsReplay,
    Projection,
    SeedLayer,
    SeedState,
)

D = Decimal
WH = uuid.UUID("c800efa5-af54-46a3-9d14-753e4bff9441")
WH2 = uuid.UUID("f4d30e5f-0211-4634-9596-a1f64f9a15c8")
BRANCH = uuid.UUID("00000000-0000-0000-0000-00000000000b")
BRANCH2 = uuid.UUID("00000000-0000-0000-0000-00000000000c")


class Ledger:
    """Builds ledger lines with the links the engine reads."""

    def __init__(self) -> None:
        self.lines: list[LedgerLine] = []
        self.seq = 0
        self.by_ref: dict[str, LedgerLine] = {}

    def add(
        self,
        type_: str,
        qty: str,
        cost: str = "0",
        *,
        item: uuid.UUID,
        wh: uuid.UUID = WH,
        ref: str | None = None,
        seq: int | None = None,
        po: bool = False,
        source_type: str | None = None,
        source_id: str | None = None,
        group: uuid.UUID | None = None,
        order: uuid.UUID | None = None,
        reverses: str | None = None,
    ) -> LedgerLine:
        self.seq = seq if seq is not None else self.seq + 1
        original = self.by_ref[reverses] if reverses else None
        line = LedgerLine(
            line_id=uuid.uuid4(),
            transaction_id=uuid.uuid4(),
            item_id=item,
            warehouse_id=wh,
            branch_id=BRANCH if wh == WH else BRANCH2,
            sequence=self.seq,
            type=type_,
            delta=D(qty),
            unit_cost=D(cost),
            purchase_order_id=uuid.uuid4() if po else None,
            source_type=source_type,
            source_id=source_id,
            correction_group_id=group,
            order_id=order,
            reverses_transaction_id=original.transaction_id if original else None,
            reverses_line_id=original.line_id if original else None,
        )
        self.lines.append(line)
        if ref:
            self.by_ref[ref] = line
        return line

    def reversed(self) -> set[uuid.UUID]:
        return {
            line.reverses_transaction_id
            for line in self.lines
            if line.reverses_transaction_id is not None
        }

    def po_prices(self) -> dict[uuid.UUID, list[tuple[int, Decimal]]]:
        prices: dict[uuid.UUID, list[tuple[int, Decimal]]] = {}
        reversed_ = self.reversed()
        for line in self.lines:
            if (
                line.type == "purchasing"
                and line.purchase_order_id is not None
                and line.unit_cost > 0
                and line.transaction_id not in reversed_
            ):
                prices.setdefault(line.item_id, []).append(
                    (line.sequence, line.unit_cost)
                )
        return prices


def replay(ledger: Ledger, lines=None, cutover: int | None = None) -> Projection:
    engine = CostingEngine(
        cutover_sequence=cutover,
        reversed_transactions=ledger.reversed(),
        po_prices=ledger.po_prices(),
    )
    for line in sorted(
        ledger.lines if lines is None else lines, key=lambda row: row.sort_key
    ):
        engine.apply(line)
    return engine.project()


def live_layers(projection: Projection, item, wh=WH):
    return [
        layer
        for layer in projection.layers
        if layer.item_id == item
        and layer.warehouse_id == wh
        and layer.remaining_quantity > 0
    ]


def assert_invariant(projection: Projection) -> None:
    """Σ remaining layers == max(on hand, 0), for every item."""
    for (item, wh), level in projection.levels.items():
        layered = sum(
            (layer.remaining_quantity for layer in live_layers(projection, item, wh)),
            D(0),
        )
        assert layered == max(level.quantity, D(0)), (item, layered, level.quantity)


# ─── The real ledgers ─────────────────────────────────────────────────────────

CREAM_CHEESE_LEDGER = [
    # (seq, ref, type, source_type, is_reversal_of, po, signed qty, unit cost)
    (
        521,
        "PUR-000525",
        "purchasing",
        "shift_inventory_report",
        None,
        False,
        "140",
        "0",
    ),
    (
        530,
        "CFP-000534",
        "consumption_from_production",
        "production",
        None,
        False,
        "-116.25",
        "0",
    ),
    (
        1825,
        "CFP-001829",
        "consumption_from_production",
        "production",
        None,
        False,
        "-930",
        "0",
    ),
    (
        1833,
        "CNT-001837",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "1046.25",
        "0",
    ),
    (
        1916,
        "PUR-001920",
        "purchasing",
        "shift_inventory_report",
        None,
        False,
        "2000",
        "0",
    ),
    (
        1944,
        "INT-001948",
        "internal_use",
        "shift_inventory_report",
        None,
        False,
        "-400",
        "0",
    ),
    (
        2068,
        "INT-002075",
        "internal_use",
        "shift_inventory_report",
        None,
        False,
        "-500",
        "0",
    ),
    (
        2237,
        "INT-002248",
        "internal_use",
        "shift_inventory_report",
        None,
        False,
        "-930",
        "0",
    ),
    (
        2487,
        "CFP-002498",
        "consumption_from_production",
        "production",
        None,
        False,
        "-930",
        "0",
    ),
    (
        2530,
        "PUR-002541",
        "purchasing",
        "shift_inventory_report",
        None,
        False,
        "2000",
        "0",
    ),
    (
        2532,
        "CNT-002543",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "930",
        "0",
    ),
    (
        2703,
        "CFP-002724",
        "consumption_from_production",
        "production",
        None,
        False,
        "-930",
        "0",
    ),
    (
        2721,
        "CFP-002742",
        "consumption_from_production",
        "production",
        None,
        False,
        "-500",
        "0",
    ),
    (
        2735,
        "CNT-002756",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "500",
        "0",
    ),
    (
        3013,
        "CFP-003040",
        "consumption_from_production",
        "production",
        None,
        False,
        "-1000",
        "0",
    ),
    (
        3021,
        "PUR-003048",
        "purchasing",
        "shift_inventory_report",
        None,
        False,
        "3000",
        "0",
    ),
    (
        3208,
        "CNT-003241",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "-2430",
        "0",
    ),
    (
        3378,
        "CFP-003414",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-930",
        "0",
    ),
    (
        3471,
        "CNT-003507",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "930",
        "0",
    ),
    (
        3518,
        "CFP-003560",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-930",
        "0",
    ),
    (
        3538,
        "CFP-003580",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-250",
        "0",
    ),
    (
        3559,
        "CNT-003602",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "230",
        "0",
    ),
    (
        3677,
        "CFP-003733",
        "consumption_from_production",
        "production",
        None,
        False,
        "-1000",
        "0",
    ),
    (
        3683,
        "CNT-003739",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "1000",
        "0",
    ),
    (
        3765,
        "CFP-003828",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-930",
        "0",
    ),
    (
        3791,
        "CNT-003854",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "950",
        "0",
    ),
    # PO-003938 keyed in packs, then voided and re-keyed as PO-003941.
    (3870, "PUR-003939", "purchasing", None, None, True, "1000", "0.0630000000"),
    (
        3871,
        "ADJ-003940",
        "quantity_adjustment",
        "correction",
        "PUR-003939",
        False,
        "-1000",
        "0.0630000000",
    ),
    (3872, "PUR-003942", "purchasing", None, None, True, "1360", "0.0463235294"),
]

MASCARPONE_LEDGER = [
    (521, "PUR-000525", "purchasing", "shift_inventory_report", None, False, "6", "0"),
    (
        523,
        "CNT-000527",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "-6",
        "0",
    ),
    (
        546,
        "CFP-000550",
        "consumption_from_production",
        "production",
        None,
        False,
        "-679.6875",
        "0",
    ),
    (
        576,
        "CFP-000580",
        "consumption_from_production",
        "production",
        None,
        False,
        "-581",
        "0",
    ),
    (
        1833,
        "CNT-001837",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "1266.6875",
        "0",
    ),
    (
        1891,
        "CFP-001895",
        "consumption_from_production",
        "production",
        None,
        False,
        "-830",
        "0",
    ),
    (
        1907,
        "CFP-001911",
        "consumption_from_production",
        "production",
        None,
        False,
        "-375",
        "0",
    ),
    (1916, "PUR-001920", "purchasing", "shift_inventory_report", None, False, "3", "0"),
    (
        1917,
        "INT-001921",
        "internal_use",
        "shift_inventory_report",
        None,
        False,
        "-1165",
        "0",
    ),
    (
        1918,
        "CNT-001922",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "2368",
        "0",
    ),
    (
        2218,
        "CFP-002229",
        "consumption_from_production",
        "production",
        None,
        False,
        "-375",
        "0",
    ),
    (
        2238,
        "CNT-002249",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "375",
        "0",
    ),
    (
        2715,
        "CFP-002736",
        "consumption_from_production",
        "production",
        None,
        False,
        "-750",
        "0",
    ),
    (
        2717,
        "CFP-002738",
        "consumption_from_production",
        "production",
        None,
        False,
        "-415",
        "0",
    ),
    (
        2735,
        "CNT-002756",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "1162",
        "0",
    ),
    (
        3015,
        "CFP-003042",
        "consumption_from_production",
        "production",
        None,
        False,
        "-415",
        "0",
    ),
    (
        3023,
        "CNT-003050",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "418",
        "0",
    ),
    (
        3185,
        "CFP-003217",
        "consumption_from_production",
        "production",
        None,
        False,
        "-375",
        "0",
    ),
    (
        3208,
        "CNT-003241",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "373",
        "0",
    ),
    (
        3370,
        "CFP-003406",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-375",
        "0",
    ),
    (
        3380,
        "CFP-003416",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-415",
        "0",
    ),
    (
        3471,
        "CNT-003507",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "790",
        "0",
    ),
    (
        3512,
        "CFP-003554",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-415",
        "0",
    ),
    (
        3530,
        "CFP-003572",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-375",
        "0",
    ),
    (
        3559,
        "CNT-003602",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "785",
        "0",
    ),
    (
        3683,
        "CNT-003739",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "4",
        "0",
    ),
    (
        3746,
        "CFP-003809",
        "consumption_from_production",
        "production_line",
        None,
        False,
        "-375",
        "0",
    ),
    (
        3791,
        "CNT-003854",
        "inventory_count",
        "shift_inventory_report",
        None,
        False,
        "375",
        "0",
    ),
    (3870, "PUR-003939", "purchasing", None, None, True, "4", "29"),
    (
        3871,
        "ADJ-003940",
        "quantity_adjustment",
        "correction",
        "PUR-003939",
        False,
        "-4",
        "29",
    ),
    (3872, "PUR-003942", "purchasing", None, None, True, "2000", "0.058"),
]


def build(rows, item) -> Ledger:
    ledger = Ledger()
    for seq, ref, type_, source, reverses, po, qty, cost in rows:
        ledger.add(
            type_,
            qty,
            cost,
            item=item,
            ref=ref,
            seq=seq,
            po=po,
            source_type=source,
            reverses=reverses,
        )
    return ledger


def test_cream_cheese_every_layer_carries_the_real_po_cost():
    item = uuid.uuid4()
    projection = replay(build(CREAM_CHEESE_LEDGER, item), cutover=0)

    level = projection.levels[(item, WH)]
    assert level.quantity == D("1380")
    # No phantom stock: the layers add up to what is on the shelf.
    layers = live_layers(projection, item)
    assert sum(layer.remaining_quantity for layer in layers) == D("1380")
    # The 20 g left over from the counts and the 1360 g PO: one cost throughout.
    assert {layer.unit_cost for layer in layers} == {D("0.0463235294")}
    assert level.average_cost == D("0.0463235294")
    assert not any(layer.cost_is_provisional for layer in layers)
    # The voided PO's 0.063 prices nothing that survives.
    assert all(layer.unit_cost != D("0.063") for layer in layers)


def test_cream_cheese_history_is_trued_up_to_the_first_real_price():
    item = uuid.uuid4()
    ledger = build(CREAM_CHEESE_LEDGER, item)
    projection = replay(ledger, cutover=0)
    first_use = projection.line_costs[ledger.by_ref["CFP-000534"].line_id]
    # Consumed on 09-07 at "zero", re-costed at the price learned on 09-23.
    assert first_use.unit_cost == D("0.0463235294")
    assert first_use.total_cost == (D("116.25") * D("0.0463235294")).quantize(
        D("0.0001")
    )
    assert not first_use.is_provisional
    assert_invariant(projection)


def test_mascarpone_voided_pack_price_does_not_survive():
    item = uuid.uuid4()
    projection = replay(build(MASCARPONE_LEDGER, item), cutover=0)
    level = projection.levels[(item, WH)]
    assert level.quantity == D("2004")
    assert level.average_cost == D("0.058")
    assert {layer.unit_cost for layer in live_layers(projection, item)} == {D("0.058")}
    assert_invariant(projection)


# ─── One rule at a time ──────────────────────────────────────────────────────


def test_count_overage_onto_negative_stock_settles_the_debt_first():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "10", "2", item=item, po=True)
    ledger.add("consumption_from_orders", "-15", item=item)  # 5 short
    ledger.add("inventory_count", "8", item=item)  # counted 3 on the shelf
    projection = replay(ledger)
    assert projection.levels[(item, WH)].quantity == D("3")
    assert sum(r.remaining_quantity for r in live_layers(projection, item)) == D("3")


def test_shortfall_is_trued_up_by_the_next_priced_receipt():
    item = uuid.uuid4()
    ledger = Ledger()
    sale = ledger.add("consumption_from_orders", "-4", item=item)
    ledger.add("purchasing", "10", "3", item=item, po=True)
    projection = replay(ledger)
    cost = projection.line_costs[sale.line_id]
    assert cost.unit_cost == D("3")
    assert cost.total_cost == D("12")
    assert not cost.is_provisional
    level = projection.levels[(item, WH)]
    assert level.quantity == D("6")
    assert level.average_cost == D("3")


def test_unpriced_shortfall_is_estimated_and_flagged():
    item = uuid.uuid4()
    other = Ledger()
    # A PO price for the item exists elsewhere, before the shortfall.
    other.add("purchasing", "1", "5", item=item, wh=WH2, po=True)
    sale = other.add("consumption_from_orders", "-2", item=item)
    projection = replay(other)
    cost = projection.line_costs[sale.line_id]
    assert cost.is_provisional
    assert cost.unit_cost == D("5")


def test_void_of_a_pricing_po_undoes_its_price():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("inventory_count", "20", item=item)  # found stock, cost unknown
    ledger.add("purchasing", "4", "29", item=item, po=True, ref="bad")
    ledger.add("quantity_adjustment", "-4", "29", item=item, reverses="bad")
    ledger.add("purchasing", "2000", "0.058", item=item, po=True)
    projection = replay(ledger)
    assert {r.unit_cost for r in live_layers(projection, item)} == {D("0.058")}


def test_reversing_a_partly_consumed_receipt_draws_the_rest_fifo():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "100", "1", item=item, po=True, ref="a")
    ledger.add("consumption_from_orders", "-60", item=item)
    ledger.add("purchasing", "50", "2", item=item, po=True)
    ledger.add("quantity_adjustment", "-100", "1", item=item, reverses="a")
    projection = replay(ledger)
    level = projection.levels[(item, WH)]
    assert level.quantity == D("-10")
    assert live_layers(projection, item) == []
    assert_invariant(projection)


def test_undoing_an_issue_restores_its_exact_layers():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "10", "1", item=item, po=True)
    ledger.add("purchasing", "10", "2", item=item, po=True)
    ledger.add("consumption_from_orders", "-15", item=item, ref="sale")
    ledger.add("quantity_adjustment", "15", item=item, reverses="sale")
    projection = replay(ledger)
    layers = sorted(live_layers(projection, item), key=lambda r: r.posting_sequence)
    assert [(r.remaining_quantity, r.unit_cost) for r in layers] == [
        (D("10"), D("1")),
        (D("10"), D("2")),
    ]


def test_production_cost_is_the_actual_fifo_cost_of_its_inputs():
    flour, cake = uuid.uuid4(), uuid.uuid4()
    group = uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "100", "1", item=flour, po=True)
    ledger.add("purchasing", "100", "3", item=flour, po=True)
    # Average is 2, but FIFO takes 100 @1 then 20 @3 = 160 for 4 cakes.
    ledger.add("consumption_from_production", "-120", "2", item=flour, group=group)
    ledger.add("production", "4", "60", item=cake, group=group)
    projection = replay(ledger)
    assert projection.levels[(cake, WH)].average_cost == D("40")


def test_retroactive_price_cascades_into_the_finished_good_and_its_sales():
    flour, cake = uuid.uuid4(), uuid.uuid4()
    group = uuid.uuid4()
    ledger = Ledger()
    ledger.add("consumption_from_production", "-100", item=flour, group=group)
    ledger.add("production", "10", item=cake, group=group)
    sale = ledger.add("consumption_from_orders", "-2", item=cake)
    ledger.add("purchasing", "500", "0.5", item=flour, po=True)
    projection = replay(ledger)
    # Flour learned its price after the bake: 100 × 0.5 = 50 → 5 per cake.
    assert projection.levels[(cake, WH)].average_cost == D("5")
    assert projection.line_costs[sale.line_id].total_cost == D("10")
    assert not projection.line_costs[sale.line_id].is_provisional


def test_transfer_in_carries_the_senders_fifo_cost():
    item = uuid.uuid4()
    transfer = str(uuid.uuid4())
    ledger = Ledger()
    ledger.add("purchasing", "10", "1", item=item, po=True)
    ledger.add("purchasing", "10", "3", item=item, po=True)
    ledger.add(
        "transfer_send", "-15", item=item, source_type="transfer", source_id=transfer
    )
    ledger.add(
        "transfer_receive",
        "15",
        item=item,
        wh=WH2,
        source_type="transfer",
        source_id=transfer,
    )
    projection = replay(ledger)
    # 10 @1 + 5 @3 = 25 over 15.
    assert projection.levels[(item, WH2)].average_cost == (D(25) / D(15)).quantize(
        D("0.0000000001")
    )


def test_pre_cutover_cost_adjustment_is_superseded():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "10", "1", item=item, po=True)
    adjustment = ledger.add("cost_adjustment", "0", "9", item=item)
    projection = replay(ledger, cutover=adjustment.sequence)
    assert projection.levels[(item, WH)].average_cost == D("1")
    assert projection.line_costs[adjustment.line_id].superseded
    # The same adjustment after the cutover is honoured.
    projection = replay(ledger, cutover=0)
    assert projection.levels[(item, WH)].average_cost == D("9")


def test_shift_report_receipt_is_not_a_price_signal():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("inventory_count", "5", item=item)
    # Received on the report at the stale average — not a purchase price.
    ledger.add("purchasing", "10", "7", item=item, source_type="shift_inventory_report")
    ledger.add("purchasing", "10", "2", item=item, po=True)
    projection = replay(ledger)
    assert {r.unit_cost for r in live_layers(projection, item)} == {D("2")}


def test_lines_must_arrive_in_posting_order():
    item = uuid.uuid4()
    ledger = Ledger()
    first = ledger.add("purchasing", "1", "1", item=item, po=True)
    second = ledger.add("purchasing", "1", "1", item=item, po=True)
    engine = CostingEngine(cutover_sequence=None)
    engine.apply(second)
    with pytest.raises(ValueError):
        engine.apply(first)


# ─── Fast path ≡ replay ──────────────────────────────────────────────────────


def seeds_from(projection: Projection, keys) -> list[SeedState]:
    seeds = []
    for item, wh in keys:
        level = projection.levels.get((item, wh))
        layers = [
            SeedLayer(
                line_id=layer.source_line_id,
                index=layer.layer_index,
                transaction_id=layer.source_transaction_id,
                purchase_order_id=layer.purchase_order_id,
                source_kind=layer.source_kind,
                sequence=layer.posting_sequence,
                original=layer.original_quantity,
                remaining=layer.remaining_quantity,
                unit_cost=layer.unit_cost,
                priced_by=layer.priced_by_line_id,
                received_at=layer.received_at,
                provisional=layer.cost_is_provisional,
            )
            for layer in live_layers(projection, item, wh)
        ]
        provisional = any(
            layer.cost_is_provisional
            for layer in projection.layers
            if layer.item_id == item and layer.warehouse_id == wh
        ) or any(
            row.cost_is_provisional
            for row in projection.consumptions
            if row.item_id == item and row.warehouse_id == wh
        )
        seeds.append(
            SeedState(
                item_id=item,
                warehouse_id=wh,
                branch_id=BRANCH if wh == WH else BRANCH2,
                quantity=level.quantity if level else D(0),
                layers=layers,
                has_open_pending=provisional,
                last_cost=level.average_cost if level else D(0),
            )
        )
    return seeds


def external_groups(projection: Projection, ledger: Ledger, line: LedgerLine):
    if line.correction_group_id is None:
        return {}
    inputs = []
    group_lines = {
        row.line_id
        for row in ledger.lines
        if row.correction_group_id == line.correction_group_id
        and row.line_id != line.line_id
    }
    for row in projection.consumptions:
        if row.consuming_line_id in group_lines:
            inputs.append((row.quantity, row.total_cost, row.cost_is_provisional))
    return {line.correction_group_id: inputs} if inputs else {}


@pytest.mark.parametrize("seed", range(40))
def test_fast_path_matches_replay_on_random_ledgers(seed):
    rng = random.Random(seed)
    items = [uuid.uuid4() for _ in range(3)]
    ledger = Ledger()
    fast_hits = 0
    for _ in range(40):
        item = rng.choice(items)
        roll = rng.random()
        if roll < 0.3:
            line = ledger.add(
                "purchasing",
                str(rng.randint(1, 20)),
                str(rng.randint(1, 9)),
                item=item,
                po=True,
            )
        elif roll < 0.75:
            line = ledger.add(
                "consumption_from_orders", str(-rng.randint(1, 12)), item=item
            )
        elif roll < 0.9:
            line = ledger.add("inventory_count", str(rng.randint(-5, 8)), item=item)
        else:
            line = ledger.add(
                "purchasing",
                str(rng.randint(1, 5)),
                item=item,
                source_type="shift_inventory_report",
            )
        before = replay(ledger, ledger.lines[:-1])
        full = replay(ledger)
        engine = CostingEngine(
            cutover_sequence=None,
            reversed_transactions=ledger.reversed(),
            po_prices=ledger.po_prices(),
            fast=True,
            seeds=seeds_from(before, [(line.item_id, line.warehouse_id)]),
            external_group_inputs=external_groups(before, ledger, line),
        )
        try:
            engine.apply(line)
        except NeedsReplay:
            continue
        fast_hits += 1
        fast = engine.project()
        key = (line.item_id, line.warehouse_id)
        # Quantities and the shape of the projection always match a replay.
        assert fast.levels[key].quantity == full.levels[key].quantity
        by_id = {layer.id: layer for layer in full.layers}
        for layer in fast.layers:
            assert layer.id in by_id
            assert layer.remaining_quantity == by_id[layer.id].remaining_quantity
        full_cons = {row.id: row for row in full.consumptions}
        for row in fast.consumptions:
            assert row.quantity == full_cons[row.id].quantity
            assert row.is_shortfall == full_cons[row.id].is_shortfall
        # Known costs match exactly; only an estimate may lag until a replay.
        if fast.line_costs[line.line_id].is_provisional:
            assert full.line_costs[line.line_id].is_provisional
            continue
        assert fast.levels[key].average_cost == full.levels[key].average_cost
        assert fast.line_costs[line.line_id].total_cost == (
            full.line_costs[line.line_id].total_cost
        )
        assert fast.line_costs[line.line_id].running_value == (
            full.line_costs[line.line_id].running_value
        )
        for layer in fast.layers:
            if not layer.cost_is_provisional:
                assert layer.unit_cost == by_id[layer.id].unit_cost
        for row in fast.consumptions:
            assert row.total_cost == full_cons[row.id].total_cost
    assert_invariant(replay(ledger))


def test_plain_sale_from_known_cost_stock_takes_the_fast_path():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "10", "2", item=item, po=True)
    sale = ledger.add("consumption_from_orders", "-4", item=item)
    before = replay(ledger, ledger.lines[:-1])
    engine = CostingEngine(
        cutover_sequence=None, fast=True, seeds=seeds_from(before, [(item, WH)])
    )
    engine.apply(sale)
    fast = engine.project()
    assert fast.line_costs[sale.line_id].total_cost == D("8")
    assert fast.levels[(item, WH)].quantity == D("6")


def test_fast_path_books_an_oversell_at_an_estimate():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "1", "2", item=item, po=True)
    oversell = ledger.add("consumption_from_orders", "-4", item=item)
    before = replay(ledger, ledger.lines[:-1])
    engine = CostingEngine(
        cutover_sequence=None, fast=True, seeds=seeds_from(before, [(item, WH)])
    )
    engine.apply(oversell)
    cost = engine.project().line_costs[oversell.line_id]
    assert cost.total_cost == D("8")  # 1 @ 2 from stock + 3 estimated at 2
    assert cost.is_provisional


def test_fast_path_refuses_a_receipt_that_prices_waiting_stock():
    item = uuid.uuid4()
    ledger = Ledger()
    ledger.add("consumption_from_orders", "-4", item=item)
    receipt = ledger.add("purchasing", "10", "3", item=item, po=True)
    before = replay(ledger, ledger.lines[:-1])
    engine = CostingEngine(
        cutover_sequence=None, fast=True, seeds=seeds_from(before, [(item, WH)])
    )
    with pytest.raises(NeedsReplay):
        engine.apply(receipt)


@pytest.mark.parametrize("seed", range(30))
def test_invariant_holds_on_random_ledgers_with_reversals(seed):
    rng = random.Random(1000 + seed)
    items = [uuid.uuid4() for _ in range(2)]
    ledger = Ledger()
    refs: list[str] = []
    for index in range(50):
        item = rng.choice(items)
        roll = rng.random()
        ref = f"r{index}"
        if roll < 0.25:
            ledger.add(
                "purchasing",
                str(rng.randint(1, 20)),
                str(rng.randint(1, 9)),
                item=item,
                po=True,
                ref=ref,
            )
            refs.append(ref)
        elif roll < 0.6:
            ledger.add(
                "consumption_from_orders", str(-rng.randint(1, 12)), item=item, ref=ref
            )
            refs.append(ref)
        elif roll < 0.8:
            ledger.add("inventory_count", str(rng.randint(-5, 8)), item=item)
        elif refs:
            target = ledger.by_ref[refs.pop(rng.randrange(len(refs)))]
            ledger.add(
                "quantity_adjustment",
                str(-target.delta),
                str(target.unit_cost),
                item=target.item_id,
                reverses=next(k for k, v in ledger.by_ref.items() if v is target),
            )
    projection = replay(ledger)
    assert_invariant(projection)
    for level in projection.levels.values():
        assert level.average_cost >= 0


def test_made_stock_no_batch_explains_is_estimated_from_its_recipe():
    flour, cake = uuid.uuid4(), uuid.uuid4()
    ledger = Ledger()
    ledger.add("purchasing", "1000", "0.5", item=flour, po=True)
    found = ledger.add("inventory_count", "4", item=cake)  # never produced here
    engine = CostingEngine(
        cutover_sequence=None,
        reversed_transactions=ledger.reversed(),
        po_prices=ledger.po_prices(),
        recipes={cake: [(flour, D("30"))]},
    )
    for line in ledger.lines:
        engine.apply(line)
    projection = engine.project()
    cost = projection.line_costs[found.line_id]
    assert cost.unit_cost == D("15")  # 30 g × 0.5
    assert cost.is_provisional
    # A real batch later prices it for good.
    group = uuid.uuid4()
    ledger.add("consumption_from_production", "-40", item=flour, group=group)
    ledger.add("production", "2", item=cake, group=group)
    projection = replay(ledger)
    assert projection.line_costs[found.line_id].unit_cost == D("10")
    assert not projection.line_costs[found.line_id].is_provisional
