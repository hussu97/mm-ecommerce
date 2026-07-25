from __future__ import annotations

from decimal import Decimal

from app.models.till import DRAWER_SIGN, DrawerOperation, DrawerOperationTypeEnum


def _op(op_type: str, amount: str) -> DrawerOperation:
    operation = DrawerOperation()
    operation.type = op_type
    operation.amount = Decimal(amount)
    return operation


def test_every_operation_type_has_a_sign():
    """A missing sign would silently drop cash from the reconciliation."""
    declared = {member.value for member in DrawerOperationTypeEnum}
    assert declared == set(DRAWER_SIGN)


def test_signs_match_the_direction_cash_actually_moves():
    assert DRAWER_SIGN[DrawerOperationTypeEnum.PAY_IN.value] == 1
    assert DRAWER_SIGN[DrawerOperationTypeEnum.SALES.value] == 1
    assert DRAWER_SIGN[DrawerOperationTypeEnum.PAY_OUT.value] == -1
    assert DRAWER_SIGN[DrawerOperationTypeEnum.CASH_DROP.value] == -1
    assert DRAWER_SIGN[DrawerOperationTypeEnum.RETURN.value] == -1
    # A no-sale drawer open must never move the expected balance.
    assert DRAWER_SIGN[DrawerOperationTypeEnum.OPEN_DRAWER.value] == 0


def test_signed_amount_applies_the_direction():
    assert _op("sales", "120.50").signed_amount == Decimal("120.50")
    assert _op("pay_out", "40.00").signed_amount == Decimal("-40.00")
    assert _op("open_drawer", "0").signed_amount == Decimal("0")


def test_a_full_shift_reconciles_to_the_expected_drawer_total():
    """
    Float 200, takes 340.75 in cash, refunds 25.50, pays out 60 for supplies,
    drops 150 to the safe, and opens the drawer once for change.
    """
    opening = Decimal("200.00")
    operations = [
        _op(DrawerOperationTypeEnum.SALES.value, "340.75"),
        _op(DrawerOperationTypeEnum.RETURN.value, "25.50"),
        _op(DrawerOperationTypeEnum.PAY_OUT.value, "60.00"),
        _op(DrawerOperationTypeEnum.CASH_DROP.value, "150.00"),
        _op(DrawerOperationTypeEnum.OPEN_DRAWER.value, "0"),
        _op(DrawerOperationTypeEnum.PAY_IN.value, "20.00"),
    ]
    expected = opening + sum((o.signed_amount for o in operations), start=Decimal("0"))
    assert expected == Decimal("325.25")

    # Cashier counts 320.25 — the till is 5.00 short.
    counted = Decimal("320.25")
    assert counted - expected == Decimal("-5.00")
