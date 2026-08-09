from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.order import DeliveryMethodEnum
from app.models.promo_code import DiscountTypeEnum
from app.services import promo_code_service
from app.services.promo_code_service import _calc_discount


class TestCalcDiscount:
    def _make_promo(
        self,
        discount_type: DiscountTypeEnum,
        discount_value: str,
        max_discount_amount: str | None = None,
    ) -> MagicMock:
        promo = MagicMock()
        promo.discount_type = discount_type
        promo.discount_value = Decimal(discount_value)
        # Set explicitly, because a MagicMock invents any attribute asked of it
        # and the invention is not a number — leaving it unset does not mean
        # "no cap", it means `Decimal(<MagicMock>)` and an InvalidOperation.
        promo.max_discount_amount = (
            None if max_discount_amount is None else Decimal(max_discount_amount)
        )
        return promo

    def test_percentage_discount_is_capped_when_a_cap_is_set(self):
        """15% of a 600-dirham catering order is 90, which is not what "15% off,
        up to AED 30" promised."""
        promo = self._make_promo(DiscountTypeEnum.PERCENTAGE, "15", "30.00")
        assert _calc_discount(promo, Decimal("600.00")) == Decimal("30.00")

    def test_the_cap_does_not_raise_a_smaller_discount(self):
        promo = self._make_promo(DiscountTypeEnum.PERCENTAGE, "15", "30.00")
        # 15% of 60 is 9, well under the cap, and stays 9.
        assert _calc_discount(promo, Decimal("60.00")) == Decimal("9.00")

    def test_a_cap_also_binds_a_fixed_discount(self):
        promo = self._make_promo(DiscountTypeEnum.FIXED, "50.00", "30.00")
        assert _calc_discount(promo, Decimal("500.00")) == Decimal("30.00")

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


class TestPhoneGate:
    """
    When an unverified phone costs the customer the discount, and when it only
    costs them a prompt.

    The rule has two axes and they are easy to conflate: *whether the gate
    applies* (a delivery costs us a courier, a collection does not) and
    *whether we are enforcing it yet* (the basket reports, `create_order`
    refuses). The bug this replaced collapsed both into one refusal, at the
    basket, on a page with no phone field — so the offer was unclaimable by the
    customer it was written for, and a pickup order was refused hardest of all
    because the only phone it looked at lives on a shipping address a pickup
    does not have.
    """

    def _gated_promo(self) -> MagicMock:
        promo = MagicMock()
        promo.discount_type = DiscountTypeEnum.PERCENTAGE
        promo.discount_value = Decimal("20")
        promo.max_discount_amount = Decimal("30.00")
        promo.requires_phone_verification = True
        promo.is_active = True
        promo.valid_from = None
        promo.valid_until = None
        promo.max_uses = None
        promo.max_uses_per_user = None
        promo.min_order_amount = None
        promo.first_orders_limit = None
        promo.current_uses = 0
        promo.code = "NEW"
        return promo

    async def _validate(self, *, verified: bool, **kwargs):
        promo = self._gated_promo()
        with (
            patch.object(
                promo_code_service, "find_by_code", AsyncMock(return_value=promo)
            ),
            patch.object(
                promo_code_service.firebase_auth_service,
                "is_phone_verified",
                AsyncMock(return_value=verified),
            ),
        ):
            return await promo_code_service.validate(
                MagicMock(), "NEW", Decimal("100.00"), **kwargs
            )

    async def test_an_unverified_basket_still_gets_the_code_and_the_discount(self):
        """The whole point. The basket has no phone field on it, so refusing
        here is a dead end — it reports what is outstanding instead."""
        result = await self._validate(verified=False)
        assert result.valid is True
        assert result.discount_amount == Decimal("20.00")
        assert result.requires_phone_verification is True
        assert result.phone_verified is False

    async def test_a_pickup_order_is_never_asked(self):
        """No courier, no logistics cost, nothing for the gate to protect — and
        a pickup has no shipping address to carry a phone in the first place."""
        result = await self._validate(
            verified=False,
            delivery_method=DeliveryMethodEnum.PICKUP,
            enforce_phone_verification=True,
        )
        assert result.valid is True
        assert result.discount_amount == Decimal("20.00")
        # Outstanding is `requires and not verified`; a gate that does not apply
        # must not read as outstanding, or the checkout blocks a pickup order.
        assert result.phone_verified is True

    async def test_a_delivery_order_is_refused_at_enforcement(self):
        result = await self._validate(
            verified=False,
            delivery_method=DeliveryMethodEnum.DELIVERY,
            enforce_phone_verification=True,
        )
        assert result.valid is False
        assert result.requires_phone_verification is True
        assert result.phone_verified is False

    async def test_a_verified_delivery_order_goes_through(self):
        result = await self._validate(
            verified=True,
            delivery_method=DeliveryMethodEnum.DELIVERY,
            enforce_phone_verification=True,
        )
        assert result.valid is True
        assert result.discount_amount == Decimal("20.00")
        assert result.phone_verified is True

    async def test_an_ungated_coupon_never_reports_an_outstanding_gate(self):
        promo = self._gated_promo()
        promo.requires_phone_verification = False
        with (
            patch.object(
                promo_code_service, "find_by_code", AsyncMock(return_value=promo)
            ),
            patch.object(
                promo_code_service.firebase_auth_service,
                "is_phone_verified",
                AsyncMock(return_value=False),
            ) as checked,
        ):
            result = await promo_code_service.validate(
                MagicMock(),
                "NEW",
                Decimal("100.00"),
                delivery_method=DeliveryMethodEnum.DELIVERY,
                enforce_phone_verification=True,
            )
        assert result.valid is True
        assert result.requires_phone_verification is False
        assert result.phone_verified is True
        # And the ledger is not queried for a coupon that does not care.
        checked.assert_not_awaited()
