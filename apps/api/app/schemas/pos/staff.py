"""Roles, permissions, staff records and PIN login."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel, Translations


class PermissionEntry(BaseModel):
    slug: str
    description: str


class PermissionCatalogue(BaseModel):
    groups: dict[str, list[PermissionEntry]]


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    is_super_admin: bool = False
    is_active: bool = True


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    permissions: list[str] | None = None
    is_super_admin: bool | None = None
    is_active: bool | None = None


class RoleResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    permissions: list[str]
    is_super_admin: bool
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    user_count: int = 0


class StaffCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=150)
    staff_number: str | None = Field(None, max_length=30)
    phone: str | None = Field(None, max_length=20)
    password: str | None = Field(None, min_length=8, max_length=128)
    pin: str | None = Field(None, pattern=r"^\d{4,8}$")
    role_id: UUID | None = None
    branch_ids: list[UUID] = Field(default_factory=list)
    is_active: bool = True
    is_admin: bool = False
    is_driver: bool = False


class StaffUpdate(BaseModel):
    email: str | None = Field(None, min_length=3, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=150)
    staff_number: str | None = Field(None, max_length=30)
    phone: str | None = Field(None, max_length=20)
    password: str | None = Field(None, min_length=8, max_length=128)
    pin: str | None = Field(None, pattern=r"^\d{4,8}$")
    role_id: UUID | None = None
    branch_ids: list[UUID] | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    is_driver: bool | None = None


class StaffResponse(ORMModel):
    id: UUID
    email: str
    display_name: str | None
    staff_number: str | None
    phone: str | None
    role_id: UUID | None
    role_name: str | None = None
    branch_ids: list[UUID] = []
    is_active: bool
    is_admin: bool
    is_staff: bool
    is_driver: bool
    has_pin: bool = False
    created_at: datetime
    updated_at: datetime


class PinLoginRequest(BaseModel):
    branch_id: UUID
    pin: str = Field(pattern=r"^\d{4,8}$")
    device_token: str | None = None


class PinLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    staff: StaffResponse
    permissions: list[str]
