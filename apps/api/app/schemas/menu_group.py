from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MenuGroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: dict[str, dict[str, str]] = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    image_url: str | None = Field(None, max_length=500)
    #: Null puts the group at the top of the menu.
    parent_id: UUID | None = None
    display_order: int = 0
    is_active: bool = True


class MenuGroupCreate(MenuGroupBase):
    #: Replaces the group's contents wholesale, in the order given — that
    #: order is what the terminal lays the buttons out in.
    product_ids: list[UUID] = Field(default_factory=list)


class MenuGroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: dict[str, dict[str, str]] | None = None
    reference: str | None = Field(None, max_length=50)
    image_url: str | None = Field(None, max_length=500)
    parent_id: UUID | None = None
    display_order: int | None = None
    is_active: bool | None = None
    product_ids: list[UUID] | None = None


class MenuGroupNode(BaseModel):
    """One node of the tree, with its descendants nested inside it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    name_localized: str | None = None
    reference: str | None = None
    image_url: str | None = None
    parent_id: UUID | None = None
    display_order: int
    is_active: bool
    product_ids: list[UUID] = []
    product_count: int = 0
    children: list[MenuGroupNode] = []


class MenuGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    name_localized: str | None = None
    translations: dict[str, dict[str, str]] = {}
    reference: str | None = None
    image_url: str | None = None
    parent_id: UUID | None = None
    display_order: int
    is_active: bool
    product_ids: list[UUID] = []


MenuGroupNode.model_rebuild()
