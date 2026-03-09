from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.address import RegionEnum
from app.schemas.address import AddressCreate


class TestAddressCreate:
    def _valid_data(self) -> dict:
        return dict(
            first_name="John",
            last_name="Doe",
            phone="0501234567",
            address_line_1="123 Test St",
            region=RegionEnum.DUBAI,
            latitude="25.2048",
            longitude="55.2708",
        )

    def test_valid_address(self):
        addr = AddressCreate(**self._valid_data())
        assert addr.region == RegionEnum.DUBAI

    def test_invalid_region(self):
        data = self._valid_data()
        data["region"] = "InvalidRegion"
        with pytest.raises(ValidationError):
            AddressCreate(**data)

    def test_first_name_required(self):
        data = self._valid_data()
        del data["first_name"]
        with pytest.raises(ValidationError):
            AddressCreate(**data)

    def test_phone_too_short(self):
        data = self._valid_data()
        data["phone"] = "123"  # min_length=7
        with pytest.raises(ValidationError):
            AddressCreate(**data)

    def test_default_country_is_ae(self):
        addr = AddressCreate(**self._valid_data())
        assert addr.country == "AE"

    def test_all_regions_valid(self):
        data = self._valid_data()
        for region in RegionEnum:
            data["region"] = region
            addr = AddressCreate(**data)
            assert addr.region == region
