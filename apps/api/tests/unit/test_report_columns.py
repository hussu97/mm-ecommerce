"""The shift-report column contract: one place, read by the grid, save and posting."""

from __future__ import annotations

from app.models.inventory import InventoryTransactionTypeEnum as TX
from app.models.inventory_v2 import ShiftInventoryReportLine
from app.services.inventory import report_columns as rc


def _by_key(report_type: str) -> dict[str, rc.ColumnSpec]:
    return {c.key: c for c in rc.columns_for(report_type)}


def test_every_column_key_is_a_real_report_line_field() -> None:
    fields = set(ShiftInventoryReportLine.__table__.columns.keys())
    for report_type in ("finished_goods", "raw_materials", "packaging", "spot_check"):
        for column in rc.columns_for(report_type):
            assert column.key in fields, (
                f"{report_type}:{column.key} is not a line field"
            )


def test_finished_goods_is_the_combined_production_sheet() -> None:
    cols = _by_key("finished_goods")
    # Production is entered here and posts a production movement — this is what
    # merges "production" into the finished-goods reconciliation.
    assert cols["production_quantity"].role == rc.ROLE_IN
    assert cols["production_quantity"].source == rc.SOURCE_ENTERED
    assert cols["production_quantity"].posts == TX.PRODUCTION.value
    assert cols["production_quantity"].editable is True
    # Sales are filled by the ledger, never typed.
    assert cols["sales_consumption_quantity"].source == rc.SOURCE_LEDGER
    assert cols["sales_consumption_quantity"].editable is False


def test_derived_ends_are_read_only_and_physical_is_editable() -> None:
    for report_type in ("finished_goods", "raw_materials", "packaging"):
        cols = _by_key(report_type)
        assert cols["opening_quantity"].role == rc.ROLE_OPENING
        assert cols["opening_quantity"].editable is False
        assert cols["expected_quantity"].role == rc.ROLE_NET
        assert cols["expected_quantity"].editable is False
        assert cols["variance_quantity"].editable is False
        assert cols["entered_quantity"].role == rc.ROLE_PHYSICAL
        assert cols["entered_quantity"].editable is True


def test_raw_materials_infers_consumption_but_lets_procurement_be_typed() -> None:
    cols = _by_key("raw_materials")
    assert cols["purchasing_quantity"].source == rc.SOURCE_ENTERED
    assert cols["purchasing_quantity"].posts == TX.PURCHASING.value
    # Production-consumption comes from the production sheet via the ledger.
    assert cols["production_consumption_quantity"].source == rc.SOURCE_LEDGER
    assert cols["production_consumption_quantity"].editable is False


def test_editable_columns_are_only_the_entered_movements_and_carry_a_posting() -> None:
    editable = rc.editable_columns("raw_materials")
    keys = {c.key for c in editable}
    assert keys == {
        "purchasing_quantity",
        "transfer_in_quantity",
        "extra_production_consumption_quantity",
        "internal_use_quantity",
        "waste_quantity",
        "transfer_out_quantity",
    }
    # Never the derived ends, the physical count, or the ledger-filled columns.
    assert "entered_quantity" not in keys
    assert "sales_consumption_quantity" not in keys
    # The recipe-derived production drawdown stays ledger-filled and non-editable;
    # only the extra, off-recipe drawdown beside it is typed.
    assert "production_consumption_quantity" not in keys
    assert "opening_quantity" not in keys
    for column in editable:
        assert column.posts is not None, f"{column.key} must name the movement it posts"
        assert column.role in (rc.ROLE_IN, rc.ROLE_OUT)


def test_packaging_subtracts_the_consumption_it_actually_incurs() -> None:
    # A box leaves with every order it wraps, so packaging is consumed by sales (and
    # by boxing a produced good) — both ledger-filled OUT columns, so the closing
    # figure subtracts them. Without these the report read as a standing shortage.
    cols = _by_key("packaging")
    assert cols["sales_consumption_quantity"].role == rc.ROLE_OUT
    assert cols["sales_consumption_quantity"].source == rc.SOURCE_LEDGER
    assert cols["sales_consumption_quantity"].editable is False
    assert cols["production_consumption_quantity"].role == rc.ROLE_OUT
    assert cols["production_consumption_quantity"].source == rc.SOURCE_LEDGER


def test_extra_production_use_is_an_editable_deduction_beside_the_recipe_figure() -> (
    None
):
    # The recipe-derived "Used in production" stays ledger-filled and read-only; the
    # new "Extra production use" is a separate, shop-typed OUT deduction for off-recipe
    # consumption, posting its own EXTRA_PRODUCTION_USE movement.
    for report_type in ("raw_materials", "packaging"):
        cols = _by_key(report_type)
        assert "extra_production_consumption_quantity" in cols, report_type
        extra = cols["extra_production_consumption_quantity"]
        assert extra.role == rc.ROLE_OUT
        assert extra.source == rc.SOURCE_ENTERED
        assert extra.editable is True
        assert extra.posts == TX.EXTRA_PRODUCTION_USE.value
        # It sits right after the recipe figure, which remains non-editable.
        assert cols["production_consumption_quantity"].editable is False


def test_serialisation_exposes_editable_flag_for_the_grid() -> None:
    payload = rc.columns_for("packaging")[0].to_dict()
    assert set(payload) >= {"key", "label", "role", "source", "posts", "editable"}
