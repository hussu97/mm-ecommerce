from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.address import AddressCreate


class TestAddressCreate:
    def _valid_data(self) -> dict:
        return dict(
            first_name="John",
            last_name="Doe",
            phone="0501234567",
            address_line_1="123 Test St",
            latitude="25.2048",
            longitude="55.2708",
        )

    def test_valid_address(self):
        addr = AddressCreate(**self._valid_data())
        assert addr.address_line_1 == "123 Test St"

    def test_coordinates_are_required(self):
        """
        An address without a pin cannot be priced or delivered. It used to be
        rescuable by the emirate the customer picked; there is no emirate any
        more, so the pin is not optional.
        """
        for field in ("latitude", "longitude"):
            data = self._valid_data()
            del data[field]
            with pytest.raises(ValidationError):
                AddressCreate(**data)

    def test_an_emirate_is_no_longer_part_of_an_address(self):
        """
        Not merely optional — absent. A stray `region` from an old client is
        ignored rather than stored, so nothing downstream can start believing
        it again.
        """
        addr = AddressCreate(**self._valid_data(), region="dubai")
        assert not hasattr(addr, "region")

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
