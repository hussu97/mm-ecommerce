from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.address import EmirateEnum
from app.schemas.address import AddressCreate


class TestAddressCreate:
    def _valid_data(self) -> dict:
        return dict(
            first_name="John",
            last_name="Doe",
            phone="0501234567",
            address_line_1="123 Test St",
            city="Dubai",
            emirate=EmirateEnum.DUBAI,
        )

    def test_valid_address(self):
        addr = AddressCreate(**self._valid_data())
        assert addr.emirate == EmirateEnum.DUBAI

    def test_invalid_emirate(self):
        data = self._valid_data()
        data["emirate"] = "InvalidEmirate"
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

    def test_all_emirates_valid(self):
        data = self._valid_data()
        for emirate in EmirateEnum:
            data["emirate"] = emirate
            addr = AddressCreate(**data)
            assert addr.emirate == emirate
