from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.order import DeliveryMethodEnum
from app.models.region import DeliverySettings, Region
from app.services.delivery_service import calculate_fee


def _make_db(settings: DeliverySettings, region: Region | None = None):
    """Return a minimal AsyncMock session that yields the given fixtures."""
    db = AsyncMock()

    def execute_side_effect(stmt):
        result = MagicMock()
        # Determine which query is being run by inspecting the bound params
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        if "delivery_settings" in sql.lower():
            result.scalars.return_value.first.return_value = settings
        elif "regions" in sql.lower():
            result.scalars.return_value.first.return_value = region
            result.scalars.return_value.all.return_value = [region] if region else []
        return result

    async def async_execute(stmt):
        return execute_side_effect(stmt)

    db.execute = async_execute
    return db


_DEFAULT_SETTINGS = DeliverySettings(
    free_delivery_threshold=Decimal("200.00"),
    pickup_fee=Decimal("0.00"),
)


@pytest.mark.asyncio
class TestPickup:
    async def test_pickup_is_always_free(self):
        db = _make_db(_DEFAULT_SETTINGS)
        fee = await calculate_fee(DeliveryMethodEnum.PICKUP, None, Decimal("10.00"), db)
        assert fee == Decimal("0.00")

    async def test_pickup_free_even_with_low_subtotal(self):
        db = _make_db(_DEFAULT_SETTINGS)
        fee = await calculate_fee(
            DeliveryMethodEnum.PICKUP, "dubai", Decimal("1.00"), db
        )
        assert fee == Decimal("0.00")


@pytest.mark.asyncio
class TestFreeThreshold:
    async def test_free_at_exact_threshold(self):
        db = _make_db(_DEFAULT_SETTINGS)
        fee = await calculate_fee(
            DeliveryMethodEnum.DELIVERY, "dubai", Decimal("200.00"), db
        )
        assert fee == Decimal("0.00")

    async def test_free_above_threshold(self):
        db = _make_db(_DEFAULT_SETTINGS)
        fee = await calculate_fee(
            DeliveryMethodEnum.DELIVERY, "abu-dhabi", Decimal("250.00"), db
        )
        assert fee == Decimal("0.00")

    async def test_not_free_below_threshold(self):
        region = Region(
            slug="dubai",
            delivery_fee=Decimal("35.00"),
            is_active=True,
            sort_order=1,
            name_translations={"en": "Dubai", "ar": "دبي"},
        )
        db = _make_db(_DEFAULT_SETTINGS, region)
        fee = await calculate_fee(
            DeliveryMethodEnum.DELIVERY, "dubai", Decimal("199.99"), db
        )
        assert fee > Decimal("0.00")


@pytest.mark.asyncio
class TestRegionRates:
    async def test_dubai_rate(self):
        region = Region(
            slug="dubai",
            delivery_fee=Decimal("35.00"),
            is_active=True,
            sort_order=1,
            name_translations={"en": "Dubai", "ar": "دبي"},
        )
        db = _make_db(_DEFAULT_SETTINGS, region)
        fee = await calculate_fee(
            DeliveryMethodEnum.DELIVERY, "dubai", Decimal("100.00"), db
        )
        assert fee == Decimal("35.00")

    async def test_remote_region_rate(self):
        region = Region(
            slug="abu-dhabi",
            delivery_fee=Decimal("50.00"),
            is_active=True,
            sort_order=2,
            name_translations={"en": "Abu Dhabi", "ar": "أبو ظبي"},
        )
        db = _make_db(_DEFAULT_SETTINGS, region)
        fee = await calculate_fee(
            DeliveryMethodEnum.DELIVERY, "abu-dhabi", Decimal("100.00"), db
        )
        assert fee == Decimal("50.00")
