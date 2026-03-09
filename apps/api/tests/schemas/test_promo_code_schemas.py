from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.promo_code import DiscountTypeEnum
from app.schemas.promo_code import (
    PromoCodeCreate,
    PromoCodeValidateRequest,
    PromoCodeValidateResponse,
)


class TestPromoCodeCreate:
    def test_valid_percentage_code(self):
        code = PromoCodeCreate(
            code="SAVE10",
            discount_type=DiscountTypeEnum.PERCENTAGE,
            discount_value=Decimal("10.00"),
        )
        assert code.code == "SAVE10"

    def test_valid_fixed_code(self):
        code = PromoCodeCreate(
            code="AED20OFF",
            discount_type=DiscountTypeEnum.FIXED,
            discount_value=Decimal("20.00"),
        )
        assert code.discount_type == DiscountTypeEnum.FIXED

    def test_lowercase_code_invalid(self):
        with pytest.raises(ValidationError):
            PromoCodeCreate(
                code="save10",
                discount_type=DiscountTypeEnum.PERCENTAGE,
                discount_value=Decimal("10.00"),
            )

    def test_code_too_short(self):
        with pytest.raises(ValidationError):
            PromoCodeCreate(
                code="AB",  # min_length=3
                discount_type=DiscountTypeEnum.PERCENTAGE,
                discount_value=Decimal("10.00"),
            )

    def test_discount_value_zero_invalid(self):
        with pytest.raises(ValidationError):
            PromoCodeCreate(
                code="TEST",
                discount_type=DiscountTypeEnum.FIXED,
                discount_value=Decimal("0.00"),
            )

    def test_max_uses_zero_invalid(self):
        with pytest.raises(ValidationError):
            PromoCodeCreate(
                code="TEST",
                discount_type=DiscountTypeEnum.FIXED,
                discount_value=Decimal("10.00"),
                max_uses=0,
            )


class TestPromoCodeValidateRequest:
    def test_valid_request(self):
        req = PromoCodeValidateRequest(code="TEST", order_subtotal=Decimal("100.00"))
        assert req.code == "TEST"

    def test_zero_subtotal_valid(self):
        req = PromoCodeValidateRequest(code="TEST", order_subtotal=Decimal("0.00"))
        assert req.order_subtotal == Decimal("0.00")

    def test_negative_subtotal_invalid(self):
        with pytest.raises(ValidationError):
            PromoCodeValidateRequest(code="TEST", order_subtotal=Decimal("-1.00"))


class TestPromoCodeValidateResponseTypes:
    """
    Type-safety tests for PromoCodeValidateResponse.
    Regression suite for: TypeError: unsupported operand type(s) for -:
    'decimal.Decimal' and 'float' (discount_amount was typed as float).
    """

    def test_discount_amount_is_decimal(self):
        resp = PromoCodeValidateResponse(valid=True, discount_amount=Decimal("15.00"))
        assert isinstance(resp.discount_amount, Decimal)

    def test_discount_amount_default_is_decimal_zero(self):
        resp = PromoCodeValidateResponse(valid=False)
        assert isinstance(resp.discount_amount, Decimal)
        assert resp.discount_amount == Decimal("0.00")

    def test_decimal_subtraction_does_not_raise(self):
        """Core regression: Decimal subtotal - Decimal discount_amount must not raise TypeError."""
        resp = PromoCodeValidateResponse(valid=True, discount_amount=Decimal("15.00"))
        result = Decimal("100.00") - resp.discount_amount
        assert result == Decimal("85.00")

    def test_integer_input_coerced_to_decimal(self):
        resp = PromoCodeValidateResponse(valid=True, discount_amount=10)
        assert isinstance(resp.discount_amount, Decimal)

    def test_string_decimal_input_accepted(self):
        resp = PromoCodeValidateResponse(valid=True, discount_amount="25.50")
        assert isinstance(resp.discount_amount, Decimal)
        assert resp.discount_amount == Decimal("25.50")
