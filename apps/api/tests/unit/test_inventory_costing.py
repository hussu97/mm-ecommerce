from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import BadRequestError, ConflictError
from app.models.inventory import (
    TRANSACTION_SIGN,
    InventoryItem,
    InventoryLevel,
    InventoryTransactionTypeEnum,
)
from app.models.inventory_v2 import RecipeLine, RecipeVersion
from app.services.inventory.inventory_service import (
    apply_movement,
    apply_reversal_movement,
    ingredient_cost_for_unit,
    inventory_item_cost_for_unit,
)
from app.services.inventory.recipe_service import (
    ActiveRecipeCatalog,
    _assert_acyclic,
    expand_owner,
)

D = Decimal


def level(quantity: str = "0", average: str = "0") -> InventoryLevel:
    row = InventoryLevel()
    row.quantity = D(quantity)
    row.average_cost = D(average)
    return row


# ─── Transaction signs ────────────────────────────────────────────────────────


def test_every_transaction_type_has_a_direction():
    """A missing sign would silently fail to move stock."""
    declared = {member.value for member in InventoryTransactionTypeEnum}
    assert declared == set(TRANSACTION_SIGN)


def test_receipts_and_issues_point_the_right_way():
    assert TRANSACTION_SIGN["purchasing"] == 1
    assert TRANSACTION_SIGN["production"] == 1
    assert TRANSACTION_SIGN["transfer_receive"] == 1
    assert TRANSACTION_SIGN["transfer_send"] == -1
    assert TRANSACTION_SIGN["return_to_supplier"] == -1
    assert TRANSACTION_SIGN["consumption_from_orders"] == -1
    assert TRANSACTION_SIGN["waste_from_production"] == -1
    assert TRANSACTION_SIGN["opening_balance"] == 1
    assert TRANSACTION_SIGN["internal_use"] == -1
    # Value-only movement.
    assert TRANSACTION_SIGN["cost_adjustment"] == 0


def test_catalogue_storage_cost_converts_once_for_ingredient_movements():
    item = InventoryItem(
        sku="FLOUR-25KG",
        name="Flour",
        cost=D("100"),
        storage_unit="sack",
        ingredient_unit="gram",
        storage_to_ingredient_factor=D("25000"),
    )
    assert inventory_item_cost_for_unit(item, "storage") == D("100.000000")
    assert inventory_item_cost_for_unit(item, "ingredient") == D("0.004000")


def test_moving_average_cost_converts_back_to_transfer_entry_unit():
    item = InventoryItem(
        sku="FLOUR-25KG",
        name="Flour",
        storage_unit="sack",
        ingredient_unit="gram",
        storage_to_ingredient_factor=D("25000"),
    )
    assert ingredient_cost_for_unit(item, D("0.004"), "ingredient") == D("0.004000")
    assert ingredient_cost_for_unit(item, D("0.004"), "storage") == D("100.000000")


def test_unknown_ledger_unit_is_rejected_instead_of_assumed_to_be_storage():
    item = InventoryItem(
        sku="FLOUR",
        name="Flour",
        cost=D("1"),
        storage_to_ingredient_factor=D("1"),
    )
    with pytest.raises(BadRequestError, match="Unknown inventory entry unit"):
        inventory_item_cost_for_unit(item, "grams")


# ─── Weighted average ─────────────────────────────────────────────────────────


def test_first_receipt_sets_the_average_to_the_purchase_cost():
    row = level()
    apply_movement(row, D("100"), D("2.50"))
    assert row.quantity == D("100.0000")
    assert row.average_cost == D("2.500000")


def test_second_receipt_blends_the_average():
    """100 @ 2.50 then 100 @ 3.50 must average to 3.00, not jump to 3.50."""
    row = level("100", "2.50")
    apply_movement(row, D("100"), D("3.50"))
    assert row.quantity == D("200.0000")
    assert row.average_cost == D("3.000000")


def test_blend_is_weighted_by_quantity_not_by_receipt_count():
    row = level("300", "2.00")
    apply_movement(row, D("100"), D("6.00"))
    # (300*2 + 100*6) / 400 = 3.00
    assert row.average_cost == D("3.000000")


def test_issuing_stock_leaves_the_average_untouched():
    row = level("200", "3.00")
    apply_movement(row, D("-50"), None)
    assert row.quantity == D("150.0000")
    assert row.average_cost == D("3.000000")


def test_issuing_everything_keeps_the_last_known_cost():
    row = level("50", "4.25")
    apply_movement(row, D("-50"), None)
    assert row.quantity == D("0.0000")
    assert row.average_cost == D("4.250000")


def test_receipt_onto_a_negative_balance_resets_rather_than_blending():
    """
    A negative balance means stock was issued before the delivery was keyed in.
    Blending against it would produce a meaningless (or negative) average, so
    the incoming cost wins.
    """
    row = level("-10", "9.99")
    apply_movement(row, D("100"), D("2.00"))
    assert row.quantity == D("90.0000")
    assert row.average_cost == D("2.000000")


def test_value_is_conserved_across_a_receipt():
    row = level("40", "1.25")
    opening_value = row.quantity * row.average_cost
    apply_movement(row, D("60"), D("2.75"))
    expected = opening_value + D("60") * D("2.75")
    assert (row.quantity * row.average_cost).quantize(D("0.01")) == expected.quantize(
        D("0.01")
    )


def test_reversing_an_old_receipt_removes_its_original_value():
    row = level("200", "3.00")  # 100 @ 2.00 followed by 100 @ 4.00
    apply_reversal_movement(row, D("-100"), D("2.00"))
    assert row.quantity == D("100.0000")
    assert row.average_cost == D("4.000000")


def test_reversing_an_issue_blends_its_historical_value_back_in():
    row = level("100", "4.00")
    apply_reversal_movement(row, D("50"), D("2.00"))
    assert row.quantity == D("150.0000")
    assert row.average_cost == D("3.333333")


def test_a_realistic_flour_lifecycle():
    """
    Buy two sacks at different prices, bake with some, then count.
    Every step must leave a defensible average.
    """
    flour = level()
    apply_movement(flour, D("25000"), D("0.004"))  # 25kg @ 4 fils/g
    assert flour.average_cost == D("0.004000")

    apply_movement(flour, D("25000"), D("0.006"))  # price rose
    assert flour.average_cost == D("0.005000")  # blended

    apply_movement(flour, D("-12000"), None)  # baking
    assert flour.quantity == D("38000.0000")
    assert flour.average_cost == D("0.005000")

    # Stock value after the bake.
    assert (flour.quantity * flour.average_cost).quantize(D("0.01")) == D("190.00")


def test_recursive_recipe_graph_accepts_shared_subrecipes_but_rejects_cycles():
    brownie = UUID("00000000-0000-0000-0000-000000000001")
    brookie = UUID("00000000-0000-0000-0000-000000000002")
    chocolate = UUID("00000000-0000-0000-0000-000000000003")
    flour = UUID("00000000-0000-0000-0000-000000000004")

    _assert_acyclic(
        {
            brownie: {chocolate, flour},
            brookie: {brownie, chocolate},
        }
    )

    with pytest.raises(ConflictError, match="Recipe cycle detected"):
        _assert_acyclic({brownie: {brookie}, brookie: {brownie}})


@pytest.mark.asyncio
async def test_recursive_yield_loss_reaches_the_stocked_raw_material():
    product_id, prep_id, flour_id = uuid4(), uuid4(), uuid4()
    product_version = RecipeVersion(id=uuid4(), recipe_id=uuid4(), version_number=1)
    product_version.lines = [
        RecipeLine(
            item_id=prep_id,
            quantity=D("1"),
            ingredient_unit="unit",
            yield_percentage=D("0.5"),
        )
    ]
    prep_version = RecipeVersion(id=uuid4(), recipe_id=uuid4(), version_number=1)
    prep_version.lines = [
        RecipeLine(
            item_id=flour_id,
            quantity=D("10"),
            ingredient_unit="gram",
            yield_percentage=D("0.8"),
        )
    ]
    prep = InventoryItem(
        id=prep_id,
        sku="PREP",
        name="Prep",
        tracking_mode="phantom",
        storage_unit="unit",
        ingredient_unit="unit",
        storage_to_ingredient_factor=D("1"),
    )
    flour = InventoryItem(
        id=flour_id,
        sku="FLOUR",
        name="Flour",
        tracking_mode="stocked",
        storage_unit="gram",
        ingredient_unit="gram",
        storage_to_ingredient_factor=D("1"),
    )
    catalog = ActiveRecipeCatalog(
        versions={
            ("product", product_id): product_version,
            ("inventory_item", prep_id): prep_version,
        },
        items={prep_id: prep, flour_id: flour},
    )

    expanded, _ = await expand_owner(
        None, kind="product", owner_id=product_id, catalog=catalog
    )

    assert expanded[flour_id].quantity == D("25.0000")
    assert expanded[flour_id].planned_waste == D("15.0000")
