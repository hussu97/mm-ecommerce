"""What columns a shift report shows, and how each one behaves.

One place decides, per report kind, which movement columns a shop *edits*, which
the ledger *fills in*, and which are *derived* — so the register grid, the save
validation, and the posting all read the same contract instead of each guessing.

The shape mirrors the shop's paper "reconciliation" forms: a row is

    Opening + (movements in) − (movements out) = Closing,   then Physical, Difference

where Opening and Closing are derived from the ledger, some movements are entered
by the shop (procurement, production, waste, internal use, transfers) and some are
filled by the ledger (sales, production-consumption). Each entered movement names
the `InventoryTransaction` type it posts when the report is submitted, so a single
sheet drives the ledger — and the Production column on the finished-goods sheet is
what makes it the combined "Production & Finished Goods" report the shop keeps on
paper.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.models.inventory import InventoryTransactionTypeEnum as TX

# Column roles the register grid lays out by:
#   opening    — derived starting stock (prior closing), read-only
#   in / out   — a movement that adds (+, green) or removes (−, red) stock
#   net        — derived closing = opening + ins − outs, read-only
#   physical   — the counted quantity, the one always-editable cell
#   difference — derived physical − net, read-only
ROLE_OPENING = "opening"
ROLE_IN = "in"
ROLE_OUT = "out"
ROLE_NET = "net"
ROLE_PHYSICAL = "physical"
ROLE_DIFFERENCE = "difference"

# Where a movement column's number comes from:
#   entered — the shop types it; it posts `posts` on submit
#   ledger  — the ledger already holds it (sales, production-consumption)
#   derived — computed, never stored as a movement (opening/net/difference)
SOURCE_ENTERED = "entered"
SOURCE_LEDGER = "ledger"
SOURCE_DERIVED = "derived"


@dataclass(frozen=True)
class ColumnSpec:
    #: The `ShiftInventoryReportLine` field this column reads and writes.
    key: str
    label: str
    role: str
    source: str
    #: The `InventoryTransaction` type an *entered* column posts on submit, if any.
    posts: str | None = None

    @property
    def editable(self) -> bool:
        return self.source == SOURCE_ENTERED or self.role == ROLE_PHYSICAL

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["editable"] = self.editable
        return data


# Reusable cells shared by every kind: the derived ends and the physical count.
_OPENING = ColumnSpec("opening_quantity", "Opening", ROLE_OPENING, SOURCE_DERIVED)
_NET = ColumnSpec("expected_quantity", "Closing (system)", ROLE_NET, SOURCE_DERIVED)
_PHYSICAL = ColumnSpec(
    "entered_quantity", "Physical closing", ROLE_PHYSICAL, SOURCE_ENTERED
)
_DIFFERENCE = ColumnSpec(
    "variance_quantity", "Difference", ROLE_DIFFERENCE, SOURCE_DERIVED
)

# Movement cells, named once so a sign/label/posting is defined in exactly one place.
_PRODUCED = ColumnSpec(
    "production_quantity", "Produced", ROLE_IN, SOURCE_ENTERED, TX.PRODUCTION.value
)
_RECEIVED = ColumnSpec(
    "purchasing_quantity", "Received", ROLE_IN, SOURCE_ENTERED, TX.PURCHASING.value
)
_TRANSFER_IN = ColumnSpec(
    "transfer_in_quantity",
    "Transfer in",
    ROLE_IN,
    SOURCE_ENTERED,
    TX.TRANSFER_RECEIVE.value,
)
_SOLD = ColumnSpec("sales_consumption_quantity", "Sold", ROLE_OUT, SOURCE_LEDGER)
_PRODUCTION_USE = ColumnSpec(
    "production_consumption_quantity", "Used in production", ROLE_OUT, SOURCE_LEDGER
)
# An additional, shop-entered production drawdown for raw material the recipe does
# not account for (off-recipe use, or producing a good with no item/recipe). It
# subtracts independently of _PRODUCTION_USE and posts its own EXTRA_PRODUCTION_USE
# movement — never a comparison against the recipe figure.
_EXTRA_PRODUCTION_USE = ColumnSpec(
    "extra_production_consumption_quantity",
    "Extra production use",
    ROLE_OUT,
    SOURCE_ENTERED,
    TX.EXTRA_PRODUCTION_USE.value,
)
_TRANSFER_OUT = ColumnSpec(
    "transfer_out_quantity",
    "Transfer out",
    ROLE_OUT,
    SOURCE_ENTERED,
    TX.TRANSFER_SEND.value,
)
_WASTE = ColumnSpec(
    "waste_quantity", "Waste", ROLE_OUT, SOURCE_ENTERED, TX.WASTE_FROM_PRODUCTION.value
)
_INTERNAL = ColumnSpec(
    "internal_use_quantity",
    "Internal use",
    ROLE_OUT,
    SOURCE_ENTERED,
    TX.INTERNAL_USE.value,
)


# Per report kind, in the order the grid shows them. Opening leads, then movements
# (ins then outs, sales/consumption sitting with the outs), then the derived tail.
_COLUMNS: dict[str, list[ColumnSpec]] = {
    # The combined Production & Finished Goods reconciliation: produced goods, with
    # Production entered (and posting produce()) alongside the count.
    "finished_goods": [
        _OPENING,
        _PRODUCED,
        _SOLD,
        _INTERNAL,
        _TRANSFER_OUT,
        _WASTE,
        _NET,
        _PHYSICAL,
        _DIFFERENCE,
    ],
    # Kept as an alias of the combined sheet so an existing production template
    # renders the same grid rather than a lonely produced-only column.
    "production": [
        _OPENING,
        _PRODUCED,
        _SOLD,
        _INTERNAL,
        _TRANSFER_OUT,
        _WASTE,
        _NET,
        _PHYSICAL,
        _DIFFERENCE,
    ],
    "raw_materials": [
        _OPENING,
        _RECEIVED,
        _TRANSFER_IN,
        _PRODUCTION_USE,
        _EXTRA_PRODUCTION_USE,
        _SOLD,
        _INTERNAL,
        _WASTE,
        _TRANSFER_OUT,
        _NET,
        _PHYSICAL,
        _DIFFERENCE,
    ],
    # Packaging is consumed by SALES (a box leaves with every order it wraps) and by
    # PRODUCTION (boxing a produced good), both filled from the ledger — so its
    # closing must subtract them the way raw materials does. Without the Sold /
    # Used-in-production columns the report over-counted every packaging item by
    # exactly what the shift sold, and read as a standing shortage at every close.
    "packaging": [
        _OPENING,
        _RECEIVED,
        _TRANSFER_IN,
        _PRODUCTION_USE,
        _EXTRA_PRODUCTION_USE,
        _SOLD,
        _INTERNAL,
        _TRANSFER_OUT,
        _NET,
        _PHYSICAL,
        _DIFFERENCE,
    ],
    "spot_check": [_OPENING, _NET, _PHYSICAL, _DIFFERENCE],
}

# The sign each movement role applies to a stock level, so "net" is computed the
# same way everywhere: opening + Σ(in) − Σ(out).
ROLE_SIGN: dict[str, int] = {ROLE_IN: 1, ROLE_OUT: -1}


def columns_for(report_type: str) -> list[ColumnSpec]:
    """The ordered columns for a report kind, defaulting to a plain count."""
    return _COLUMNS.get(report_type, _COLUMNS["spot_check"])


def editable_columns(report_type: str) -> list[ColumnSpec]:
    """Just the movement columns a shop types in — the ones save accepts and posts."""
    return [
        column
        for column in columns_for(report_type)
        if column.source == SOURCE_ENTERED and column.role in (ROLE_IN, ROLE_OUT)
    ]
