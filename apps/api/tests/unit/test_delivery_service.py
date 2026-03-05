from __future__ import annotations

from decimal import Decimal


from app.models.address import EmirateEnum
from app.models.order import DeliveryMethodEnum
from app.services.delivery_service import DELIVERY_RATES, calculate_fee


class TestPickup:
    def test_pickup_is_always_free(self):
        fee = calculate_fee(DeliveryMethodEnum.PICKUP, None, Decimal("10.00"))
        assert fee == Decimal("0.00")

    def test_pickup_free_even_with_low_subtotal(self):
        fee = calculate_fee(
            DeliveryMethodEnum.PICKUP, EmirateEnum.DUBAI, Decimal("1.00")
        )
        assert fee == Decimal("0.00")

    def test_pickup_free_with_any_emirate(self):
        for emirate in EmirateEnum:
            fee = calculate_fee(DeliveryMethodEnum.PICKUP, emirate, Decimal("50.00"))
            assert fee == Decimal("0.00")


class TestFreeThreshold:
    def test_free_at_exact_threshold(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.DUBAI, Decimal("200.00")
        )
        assert fee == Decimal("0.00")

    def test_free_above_threshold(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.ABU_DHABI, Decimal("250.00")
        )
        assert fee == Decimal("0.00")

    def test_not_free_below_threshold(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.DUBAI, Decimal("199.99")
        )
        assert fee > Decimal("0.00")


class TestStandardZones:
    def test_dubai_standard_rate(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.DUBAI, Decimal("100.00")
        )
        assert fee == Decimal("35.00")

    def test_sharjah_standard_rate(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.SHARJAH, Decimal("100.00")
        )
        assert fee == Decimal("35.00")

    def test_ajman_standard_rate(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.AJMAN, Decimal("100.00")
        )
        assert fee == Decimal("35.00")


class TestRemoteZones:
    def test_abu_dhabi_remote_rate(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.ABU_DHABI, Decimal("100.00")
        )
        assert fee == Decimal("50.00")

    def test_fujairah_remote_rate(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.FUJAIRAH, Decimal("100.00")
        )
        assert fee == Decimal("50.00")

    def test_rak_remote_rate(self):
        fee = calculate_fee(
            DeliveryMethodEnum.DELIVERY, EmirateEnum.RAS_AL_KHAIMAH, Decimal("100.00")
        )
        assert fee == Decimal("50.00")


class TestDeliveryRatesDict:
    def test_has_required_keys(self):
        for key in ("standard_rate", "remote_rate", "free_threshold", "pickup_rate"):
            assert key in DELIVERY_RATES

    def test_pickup_rate_is_zero(self):
        assert DELIVERY_RATES["pickup_rate"] == 0.0

    def test_standard_zones_not_empty(self):
        assert len(DELIVERY_RATES["standard_zones"]) > 0

    def test_remote_zones_not_empty(self):
        assert len(DELIVERY_RATES["remote_zones"]) > 0
