from __future__ import annotations

from decimal import Decimal

from app.models.inventory import (
    TRANSACTION_SIGN,
    InventoryLevel,
    InventoryTransactionTypeEnum,
)
from app.services.inventory.inventory_service import apply_movement

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
    # Value-only movement.
    assert TRANSACTION_SIGN["cost_adjustment"] == 0


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
