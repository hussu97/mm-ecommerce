from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from app.models.promo_code import DiscountTypeEnum
from app.services.promo_code_service import _calc_discount


class TestCalcDiscount:
    def _make_promo(
        self, discount_type: DiscountTypeEnum, discount_value: str
    ) -> MagicMock:
        promo = MagicMock()
        promo.discount_type = discount_type
        promo.discount_value = Decimal(discount_value)
        return promo

    def test_percentage_discount_basic(self):
        promo = self._make_promo(DiscountTypeEnum.PERCENTAGE, "10")
        result = _calc_discount(promo, Decimal("100"))
        assert result == Decimal("10.00")

    def test_percentage_discount_fractional(self):
        promo = self._make_promo(DiscountTypeEnum.PERCENTAGE, "15")
        result = _calc_discount(promo, Decimal("33.00"))
        assert result == Decimal("4.95")

    def test_percentage_discount_rounds_to_two_decimals(self):
        promo = self._make_promo(DiscountTypeEnum.PERCENTAGE, "10")
        result = _calc_discount(promo, Decimal("33.33"))
        # 33.33 * 10 / 100 = 3.333 → rounds to 3.33
        assert result == Decimal("3.33")

    def test_fixed_discount_within_subtotal(self):
        promo = self._make_promo(DiscountTypeEnum.FIXED, "15.00")
        result = _calc_discount(promo, Decimal("100"))
        assert result == Decimal("15.00")

    def test_fixed_discount_capped_at_subtotal(self):
        """Fixed discount cannot exceed the cart subtotal."""
        promo = self._make_promo(DiscountTypeEnum.FIXED, "200.00")
        result = _calc_discount(promo, Decimal("50.00"))
        assert result == Decimal("50.00")

    def test_fixed_discount_exact_subtotal(self):
        promo = self._make_promo(DiscountTypeEnum.FIXED, "100.00")
        result = _calc_discount(promo, Decimal("100.00"))
        assert result == Decimal("100.00")

    def test_percentage_discount_zero_subtotal(self):
        promo = self._make_promo(DiscountTypeEnum.PERCENTAGE, "20")
        result = _calc_discount(promo, Decimal("0"))
        assert result == Decimal("0.00")
