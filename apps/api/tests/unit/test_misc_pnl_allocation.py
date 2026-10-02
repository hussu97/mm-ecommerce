"""Placing misc PO spend on the P&L: level, channels, branches, split by GMV.

The pure half of `services/orders/misc_expenses` — which placement a line gets,
how it is split over the window's sales, and that the split never changes the
total — plus the statement arithmetic that carries it to PC1–PC4.
"""

from __future__ import annotations

import uuid
from decimal import Decimal as D

import pytest

from app.services.orders.misc_expenses import (
    MiscLine,
    Placement,
    allocate,
    resolve_placement,
)
from app.services.orders.order_pnl import PnlTotals

SHJ = uuid.uuid4()
DSO = uuid.uuid4()
KRM = uuid.uuid4()
MM = uuid.uuid4()  # the registered entity

# The window's GMV per (channel, branch, entity) cell.
GMV = {
    ("counter", SHJ, MM): D("1000.00"),
    ("talabat", SHJ, MM): D("3000.00"),
    ("custom", SHJ, MM): D("500.00"),
    ("counter", DSO, MM): D("400.00"),
    ("talabat", DSO, MM): D("600.00"),
    # A cell with orders but no GMV (charged cancellations only) carries nothing.
    ("keeta", KRM, MM): D("0.00"),
}


def _line(
    gross,
    cost=None,
    *,
    category="Salary",
    placement=Placement(),
    cat_id=None,
    entity=None,
):
    return MiscLine(
        category_id=cat_id or uuid.uuid5(uuid.NAMESPACE_DNS, category),
        category=category,
        admin_only=False,
        placement=placement,
        gross=D(gross),
        cost=D(cost if cost is not None else gross),
        legal_entity_id=entity,
    )


def _by_channel(row):
    return {code: g for code, (g, _) in row.amounts(lambda cell: True).items()}


# ── which placement a line gets ──────────────────────────────────────────────


def test_supplier_wins_field_by_field_then_category_then_pc4():
    both = resolve_placement(
        category_level="pc1",
        category_channels=["custom"],
        supplier_level="pc2",
        supplier_channels=[],
        supplier_branch_ids=[DSO],
    )
    # The supplier's level and branches; the category's channels (the supplier
    # set none).
    assert both == Placement(level="pc2", channels=("custom",), branch_ids=(DSO,))
    nothing = resolve_placement(
        category_level=None,
        category_channels=[],
        supplier_level=None,
        supplier_channels=[],
        supplier_branch_ids=[],
    )
    assert nothing == Placement(level="pc4", channels=(), branch_ids=())


# ── the split ────────────────────────────────────────────────────────────────


def test_an_unplaced_cost_is_spread_over_every_sale_by_gmv():
    """Salaries: every column carries the same % of its GMV."""
    (row,) = allocate([_line("5500.00")], GMV)
    assert row.level == "pc4"
    split = _by_channel(row)
    # 5500 over 5500 of GMV — one AED per AED of sales.
    assert split == {
        "counter": D("1400.00"),
        "talabat": D("3600.00"),
        "custom": D("500.00"),
    }
    assert row.unallocated == (D("0"), D("0"))


def test_a_branch_placed_cost_lands_only_on_that_branch_in_proportion():
    """DSO rent at PC2: on DSO's sales only, 40/60 across its channels."""
    rent = Placement(level="pc2", branch_ids=(DSO,))
    (row,) = allocate(
        [_line("3150.00", "3000.00", category="Rent", placement=rent)], GMV
    )
    assert row.level == "pc2"
    assert row.cells == {
        ("counter", DSO, MM): (D("1260.00"), D("60.00")),
        ("talabat", DSO, MM): (D("1890.00"), D("90.00")),
    }


def test_a_channel_placed_cost_with_no_sales_is_split_evenly_over_its_channels():
    """Cake supplies on custom orders, a day with no custom order: still on the
    custom column (no branch, no entity — no order to say which)."""
    supplies = Placement(level="pc1", channels=("custom", "website_pickup"))
    (row,) = allocate(
        [_line("100.00", category="Cake Supplies", placement=supplies)],
        {("counter", SHJ, MM): D("1000.00")},
    )
    assert row.cells == {
        ("custom", None, None): (D("50.00"), D("0.00")),
        ("website_pickup", None, None): (D("50.00"), D("0.00")),
    }
    assert row.unallocated == (D("0"), D("0"))


def test_a_cost_lands_only_on_its_buying_entitys_sales():
    """A Sharjah (Fatema) PO never lands on Barsha's counter (Najm) sales, and a
    Barsha PO never on Fatema's — even unplaced, where it spreads over all."""
    brs = uuid.uuid4()
    najm = uuid.uuid4()
    gmv = {
        ("counter", SHJ, MM): D("1000.00"),
        ("talabat", brs, MM): D("1000.00"),  # Barsha's marketplace: Fatema
        ("counter", brs, najm): D("2000.00"),
    }
    (fatema_row,) = allocate([_line("300.00", entity=MM)], gmv)
    assert fatema_row.cells == {
        ("counter", SHJ, MM): (D("150.00"), D("0.00")),
        ("talabat", brs, MM): (D("150.00"), D("0.00")),
    }
    (najm_row,) = allocate([_line("300.00", category="Rent", entity=najm)], gmv)
    assert najm_row.cells == {("counter", brs, najm): (D("300.00"), D("0.00"))}


def test_an_entity_with_no_sales_and_no_channel_placement_is_unallocated():
    (row,) = allocate([_line("90.00", entity=uuid.uuid4())], GMV)
    assert row.cells == {}
    assert row.unallocated == (D("90.00"), D("0.00"))


def test_an_even_split_with_no_sales_stays_in_its_entitys_slice():
    """Cake supplies on custom orders in a month with none: the even split is
    booked under the PO's entity, so the entity filter keeps it."""
    supplies = Placement(level="pc1", channels=("custom",))
    (row,) = allocate(
        [_line("100.00", category="Cake Supplies", placement=supplies, entity=MM)],
        {("counter", SHJ, MM): D("1000.00")},
    )
    assert row.cells == {("custom", None, MM): (D("100.00"), D("0.00"))}
    kept = row.amounts(lambda cell: cell[2] == MM)
    assert kept == {"custom": (D("100.00"), D("0.00"))}


def test_a_branch_placed_cost_whose_branch_sold_nothing_is_unallocated():
    rent = Placement(level="pc2", branch_ids=(KRM,))
    (row,) = allocate([_line("2000.00", category="Rent", placement=rent)], GMV)
    assert row.cells == {}
    assert row.unallocated == (D("2000.00"), D("0.00"))


def test_the_split_adds_back_to_the_unsplit_total_to_the_fils():
    """100.00 over three equal cells is 33.34 + 33.33 + 33.33, not 99.99."""
    gmv = {
        ("counter", SHJ, MM): D("1"),
        ("talabat", SHJ, MM): D("1"),
        ("keeta", SHJ, MM): D("1"),
    }
    (row,) = allocate([_line("100.00")], gmv)
    parts = sorted(g for g, _ in row.cells.values())
    assert parts == [D("33.33"), D("33.33"), D("33.34")]
    # Several lines of one category: the category total is quantised once.
    lines = [_line("0.333"), _line("0.333"), _line("0.333")]
    (row,) = allocate(lines, gmv)
    assert sum(g for g, _ in row.cells.values()) == D("1.00")


def test_vat_recovered_follows_the_cost_and_nets_to_it_exactly():
    (row,) = allocate([_line("105.00", "100.00")], GMV)
    gross = sum(g for g, _ in row.cells.values())
    vat = sum(v for _, v in row.cells.values())
    assert (gross, vat) == (D("105.00"), D("5.00"))
    for g, v in row.cells.values():
        assert v >= 0


def test_one_category_at_two_levels_is_two_rows():
    """A supplier override moves its lines to another level; the rest stay."""
    cat = uuid.uuid4()
    rows = allocate(
        [
            _line("300.00", category="Rent", cat_id=cat),
            _line(
                "3150.00",
                category="Rent",
                cat_id=cat,
                placement=Placement(level="pc2", branch_ids=(DSO,)),
            ),
        ],
        GMV,
    )
    assert [(r.level, r.lines) for r in rows] == [("pc2", 1), ("pc4", 1)]
    total = sum(g for r in rows for g, _ in r.cells.values())
    assert total == D("3450.00")


def test_rows_are_ordered_by_level_then_name():
    rows = allocate(
        [
            _line("1.00", category="Utilities"),
            _line("1.00", category="Salary"),
            _line("1.00", category="Cake Supplies", placement=Placement(level="pc1")),
        ],
        GMV,
    )
    assert [(r.level, r.category) for r in rows] == [
        ("pc1", "Cake Supplies"),
        ("pc4", "Salary"),
        ("pc4", "Utilities"),
    ]


def test_filtering_cells_picks_a_slice_of_the_same_split():
    rent = Placement(level="pc2", branch_ids=(SHJ, DSO))
    (row,) = allocate([_line("550.00", category="Rent", placement=rent)], GMV)
    dso_only = row.amounts(lambda cell: cell[1] == DSO)
    # DSO is 1000 of the 5500 GMV the two branches sold.
    assert {k: g for k, (g, _) in dso_only.items()} == {
        "counter": D("40.00"),
        "talabat": D("60.00"),
    }


# ── the statement ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("level", "moves"),
    [
        ("pc1", ("pc1", "pc2", "pc3")),
        ("pc2", ("pc2", "pc3")),
        ("pc3", ("pc3",)),
        ("pc4", ()),
    ],
)
def test_a_level_takes_its_spend_off_that_subtotal_and_every_one_below(level, moves):
    """Placement redistributes: PC4 is the same wherever the spend sits."""
    base = PnlTotals(gmv=D("1000.00"), cogs=D("200.00"))
    placed = PnlTotals(gmv=D("1000.00"), cogs=D("200.00"))
    setattr(placed, f"misc_{level}", D("105.00"))
    setattr(placed, f"misc_{level}_vat", D("5.00"))
    for sub in ("pc1", "pc2", "pc3"):
        expected = getattr(base, sub) - (D("100.00") if sub in moves else 0)
        assert getattr(placed, sub) == expected
    assert placed.pc4 == base.pc4 - D("100.00")


def test_totals_add_the_misc_lines():
    a = PnlTotals(misc_pc2=D("10.00"), misc_pc2_vat=D("0.50"))
    b = PnlTotals(misc_pc2=D("5.00"), misc_pc4=D("1.00"))
    a.add(b)
    assert (a.misc_pc2, a.misc_pc2_vat, a.misc_pc4) == (
        D("15.00"),
        D("0.50"),
        D("1.00"),
    )


def test_an_orders_pc4_is_its_pc3():
    order = PnlTotals(gmv=D("50.00"), discounts=D("5.00"))
    assert order.pc4 == order.pc3
