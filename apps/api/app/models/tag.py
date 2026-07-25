from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class TagTypeEnum(str, enum.Enum):
    ORDER = "order"
    CUSTOMER = "customer"
    PRODUCT = "product"
    INVENTORY_ITEM = "inventory_item"
    REVENUE_CENTER = "revenue_center"


class Tag(Base, UUIDMixin, TimestampMixin):
    """
    A free-form label attached to orders, customers, products or inventory items.

    `revenue_center` tags are special: a table or order carrying one is attributed
    to that revenue centre in sales reporting (e.g. "Cafe" vs "Retail Counter").
    """

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("type", "name", name="uq_tag_type_name"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Tag {self.type}:{self.name}>"


class TaggedEntity(Base, UUIDMixin, TimestampMixin):
    """
    Polymorphic tag assignment. `entity_type` matches Tag.type
    ("order", "customer", "product", …) and `entity_id` is that row's UUID.

    Kept generic rather than one join table per entity — tags are a cross-cutting
    labelling concern and the set of taggable entities keeps growing.
    """

    __tablename__ = "tagged_entities"
    __table_args__ = (
        UniqueConstraint("tag_id", "entity_type", "entity_id", name="uq_tagged_entity"),
    )

    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"<TaggedEntity {self.entity_type}:{self.entity_id} tag={self.tag_id}>"
