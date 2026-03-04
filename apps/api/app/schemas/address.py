from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.address import EmirateEnum


class AddressCreate(BaseModel):
    label: str = Field(default="Home", max_length=50)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=7, max_length=20)
    address_line_1: str = Field(min_length=1, max_length=255)
    address_line_2: str | None = Field(None, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    emirate: EmirateEnum
    country: str = Field(default="AE", max_length=2)
    is_default: bool = False


class AddressUpdate(BaseModel):
    label: str | None = Field(None, max_length=50)
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, min_length=7, max_length=20)
    address_line_1: str | None = Field(None, min_length=1, max_length=255)
    address_line_2: str | None = None
    city: str | None = Field(None, min_length=1, max_length=100)
    emirate: EmirateEnum | None = None
    is_default: bool | None = None


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    label: str
    first_name: str
    last_name: str
    phone: str
    address_line_1: str
    address_line_2: str | None
    city: str
    emirate: EmirateEnum
    country: str
    is_default: bool
    created_at: datetime
