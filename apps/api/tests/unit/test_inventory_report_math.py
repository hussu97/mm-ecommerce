from decimal import Decimal
from uuid import uuid4

from app.models.inventory import InventoryTransactionTypeEnum
from app.models.inventory_v2 import ShiftInventoryReportLine
from app.services.inventory.report_service import _apply_source_columns


def test_report_source_columns_reconcile_to_expected_closing_stock():
    line = ShiftInventoryReportLine(
        item_id=uuid4(),
        unit="unit",
        source_summary={"required_input": "physical_count"},
    )
    movements = {
        InventoryTransactionTypeEnum.PURCHASING.value: Decimal("10"),
        InventoryTransactionTypeEnum.PRODUCTION.value: Decimal("5"),
        InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value: Decimal("-7"),
        InventoryTransactionTypeEnum.TRANSFER_SEND.value: Decimal("-2"),
        InventoryTransactionTypeEnum.INTERNAL_USE.value: Decimal("-1"),
    }

    _apply_source_columns(
        line,
        expected=Decimal("25"),
        item_movements=movements,
        through_sequence=91,
    )

    assert line.opening_quantity == Decimal("20.0000")
    assert line.purchasing_quantity == Decimal("10.0000")
    assert line.production_quantity == Decimal("5.0000")
    assert line.sales_consumption_quantity == Decimal("7.0000")
    assert line.transfer_out_quantity == Decimal("2.0000")
    assert line.internal_use_quantity == Decimal("1.0000")
    assert line.expected_quantity == Decimal("25")
    assert line.source_summary["through_sequence"] == 91


def _line() -> ShiftInventoryReportLine:
    return ShiftInventoryReportLine(
        item_id=uuid4(),
        unit="unit",
        source_summary={"required_input": "physical_count"},
    )


def test_derived_net_equals_the_ledger_expected_when_no_column_is_edited():
    # Opening + Σ(in) − Σ(out) over the filled columns must reproduce the ledger's
    # expected closing exactly, so the grid and the ledger never disagree.
    from app.services.inventory.report_service import _net_quantity

    line = _line()
    _apply_source_columns(
        line,
        expected=Decimal("25"),
        item_movements={
            InventoryTransactionTypeEnum.PURCHASING.value: Decimal("10"),
            InventoryTransactionTypeEnum.PRODUCTION.value: Decimal("5"),
            InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value: Decimal("-7"),
            InventoryTransactionTypeEnum.TRANSFER_SEND.value: Decimal("-2"),
            InventoryTransactionTypeEnum.INTERNAL_USE.value: Decimal("-1"),
        },
        through_sequence=91,
    )
    assert _net_quantity(line) == line.expected_quantity == Decimal("25")


def test_net_tracks_an_edited_production_column():
    from app.services.inventory.report_service import _net_quantity

    line = _line()
    # 47 sold, nothing else on the ledger: opening 63 → net 16.
    _apply_source_columns(
        line,
        expected=Decimal("16"),
        item_movements={
            InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value: Decimal("-47")
        },
        through_sequence=1,
    )
    assert line.opening_quantity == Decimal("63.0000")
    assert _net_quantity(line) == Decimal("16")
    # The shop records 82 produced; the closing the count is measured against rises.
    line.production_quantity = Decimal("82")
    assert _net_quantity(line) == Decimal("98")
