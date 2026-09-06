"""
Menu extensions: combos, price tags, menu groups, allergens, and per-branch
product overrides.

The core `Product` stays the single definition of an item. Everything here
layers on top of it:

* **price tags** give a product a different price for a channel or context
  (delivery menu, corporate rate) without duplicating the product;
* **branch overrides** let one shop mark an item unavailable or price it
  differently without touching the others;
* **combos** bundle products into a meal with its own price.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    pass


#: What a *root* group is for. Only meaningful on a root (``parent_id IS NULL``);
#: descendants inherit it through their root. A ``branch`` root is the menu one
#: shop's terminals render — one per branch. The single ``integrator`` root is
#: the menu MM pushes to every marketplace (Foodics + the five aggregators); a
#: product reaches the marketplaces by being a member of that tree, which is what
#: the old ``Product.sync_to_aggregators`` flag used to say. A DB CHECK holds this
#: set, so a typo is rejected rather than becoming a root nothing renders.
ROOT_KINDS: tuple[str, ...] = ("branch", "integrator")

ROOT_KIND_BRANCH = "branch"
ROOT_KIND_INTEGRATOR = "integrator"

#: The well-known ``reference`` of the one integrator root. Lets the sync find it
#: without threading an id through config.
INTEGRATOR_ROOT_REFERENCE = "integrator-root"

#: How deep the integrator root is allowed to nest: L1 = category groups, L2 =
#: items. Foodics lays its Grubtech menu out as subgroup → product and nothing
#: deeper, so a third level would have no place to map to.
INTEGRATOR_MAX_DEPTH = 2


class MenuGroup(Base, UUIDMixin, TimestampMixin):
    """
    A node in a menu tree.

    Categories are taxonomy ("Cakes"); groups are how a menu is laid out, and
    they nest — "Drinks" holds "Hot Coffee" and "Cold Coffee", each holding
    products. Deactivating a group hides everything beneath it in one move,
    which is the point of the tree.

    There is more than one tree. Each **branch** has its own root
    (``root_kind='branch'``, ``branch_id`` set) — that is the menu its terminals
    render, so Sharjah and Barsha can lay their counters out differently. A
    single **integrator** root (``root_kind='integrator'``) is the menu MM pushes
    to the marketplaces. ``root_kind`` and ``branch_id`` are only meaningful on a
    root; every node also carries ``root_id`` pointing at its own root so "which
    tree is this in" is a column read rather than a walk to the top.
    """

    __tablename__ = "menu_groups"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(150), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reference: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: What a root is for — ``branch`` or ``integrator`` (see ``ROOT_KINDS``).
    #: Set on every node, but only a root's own value is authoritative; a child's
    #: is kept equal to its root's so a branch-scoped query never has to climb.
    root_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=ROOT_KIND_BRANCH
    )
    #: Which shop this tree belongs to, on a ``branch`` root (and mirrored onto its
    #: descendants). Null on the integrator tree, which is branch-agnostic.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: The root this node lives under (a root points at itself). Denormalised so
    #: "every group in Sharjah's menu" is an indexed equality rather than a
    #: recursive walk; the service keeps it in step on create and reparent.
    root_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("menu_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: Null for a top-level group. Self-referential, so groups nest to any
    #: depth; the database rejects a group parenting itself and the service
    #: rejects longer cycles.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("menu_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "root_kind IN ('branch', 'integrator')",
            name="ck_menu_groups_root_kind",
        ),
        # A branch root must name its branch; the integrator tree must not.
        # (Enforced on roots, which is where the columns are authoritative.)
        CheckConstraint(
            "parent_id IS NOT NULL OR (root_kind = 'branch') = (branch_id IS NOT NULL)",
            name="ck_menu_groups_branch_root_has_branch",
        ),
        # One live root per branch, and one live integrator root, so the sync and
        # the register never have to choose between two trees.
        Index(
            "uq_menu_groups_branch_root",
            "branch_id",
            unique=True,
            postgresql_where=text(
                "parent_id IS NULL AND deleted_at IS NULL AND root_kind = 'branch'"
            ),
        ),
        Index(
            "uq_menu_groups_integrator_root",
            "root_kind",
            unique=True,
            postgresql_where=text(
                "parent_id IS NULL AND deleted_at IS NULL AND root_kind = 'integrator'"
            ),
        ),
    )

    children: Mapped[list[MenuGroup]] = relationship(
        "MenuGroup",
        back_populates="parent",
        cascade="all, delete-orphan",
        lazy="selectin",
        # Two self-referential FKs live on this table now (parent_id and
        # root_id); name the one the hierarchy is built from so SQLAlchemy does
        # not have to guess.
        foreign_keys="MenuGroup.parent_id",
    )
    parent: Mapped[MenuGroup | None] = relationship(
        "MenuGroup",
        back_populates="children",
        remote_side="MenuGroup.id",
        foreign_keys="MenuGroup.parent_id",
    )

    members: Mapped[list[MenuGroupProduct]] = relationship(
        "MenuGroupProduct",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<MenuGroup {self.name}>"


class MenuGroupProduct(Base, UUIDMixin):
    __tablename__ = "menu_group_products"
    __table_args__ = (
        UniqueConstraint("group_id", "product_id", name="uq_menu_group_product"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("menu_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    group: Mapped[MenuGroup] = relationship("MenuGroup", back_populates="members")

    def __repr__(self) -> str:
        return f"<MenuGroupProduct group={self.group_id}>"


class Allergen(Base, UUIDMixin, TimestampMixin):
    """A declarable allergen. UAE labelling requires these to be surfaced."""

    __tablename__ = "allergens"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(120), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    icon: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Allergen {self.name}>"


class ProductAllergen(Base, UUIDMixin):
    __tablename__ = "product_allergens"
    __table_args__ = (
        UniqueConstraint("product_id", "allergen_id", name="uq_product_allergen"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    allergen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("allergens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<ProductAllergen product={self.product_id}>"


class BranchProduct(Base, UUIDMixin, TimestampMixin):
    """
    Per-branch overrides for a product.

    A row only exists where a branch differs from the default, so the absence of
    a row means "sold here at the standard price" — the common case stays free.
    """

    __tablename__ = "branch_products"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: Cleared by "mark out of stock" on the terminal.
    #:
    #: Never read on its own — `availability_service` pairs it with
    #: `out_of_stock_until`, and a row whose moment has passed is available
    #: again regardless of what this says.
    is_in_stock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: When it comes back, or null for "until somebody says so".
    #:
    #: Only meaningful while `is_in_stock` is false, which a CHECK enforces —
    #: an available row that also claims a return time is two answers to one
    #: question.
    out_of_stock_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("branch_id", "product_id", name="uq_branch_product"),
        CheckConstraint(
            "out_of_stock_until IS NULL OR is_in_stock = false",
            name="ck_branch_products_until_only_when_out",
        ),
    )

    def __repr__(self) -> str:
        return f"<BranchProduct branch={self.branch_id} product={self.product_id}>"


class BranchModifierOption(Base, UUIDMixin, TimestampMixin):
    """
    Per-branch stock for one option of one modifier.

    `BranchProduct` for an option, and deliberately the same two columns with
    the same two meanings: a box of three is still sellable when the fudge runs
    out, so the shop needs to say the fudge is gone without saying the box is.
    One reader answers both — see `availability_service`.

    Exception-only, like its sibling: no row means the option is offered here.
    """

    __tablename__ = "branch_modifier_options"
    __table_args__ = (
        UniqueConstraint(
            "branch_id", "modifier_option_id", name="uq_branch_modifier_option"
        ),
        CheckConstraint(
            "out_of_stock_until IS NULL OR is_in_stock = false",
            name="ck_branch_modifier_options_until_only_when_out",
        ),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    modifier_option_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifier_options.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_in_stock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    out_of_stock_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<BranchModifierOption branch={self.branch_id} "
            f"option={self.modifier_option_id}>"
        )


# ─── Combos ───────────────────────────────────────────────────────────────────


class Combo(Base, UUIDMixin, TimestampMixin):
    """
    A meal deal. Structure mirrors Foodics:

        Combo → sizes (Medium, Large)
              → items (Sandwich, Drink, Side)
                  → options (Beef, Chicken)
                      → products, priced per size
    """

    __tablename__ = "combos"

    sku: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sizes: Mapped[list[ComboSize]] = relationship(
        "ComboSize",
        back_populates="combo",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ComboSize.display_order",
    )
    items: Mapped[list[ComboItem]] = relationship(
        "ComboItem",
        back_populates="combo",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ComboItem.display_order",
    )

    @property
    def is_ready(self) -> bool:
        """A combo is only sellable once every item has at least one option."""
        return (
            bool(self.sizes)
            and bool(self.items)
            and all(item.options for item in self.items)
        )

    def __repr__(self) -> str:
        return f"<Combo {self.sku} {self.name}>"


class ComboSize(Base, UUIDMixin):
    __tablename__ = "combo_sizes"

    combo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("combos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    combo: Mapped[Combo] = relationship("Combo", back_populates="sizes")

    def __repr__(self) -> str:
        return f"<ComboSize {self.name} {self.price}>"


class ComboItem(Base, UUIDMixin):
    """A slot in the combo the customer must fill, e.g. "Drink"."""

    __tablename__ = "combo_items"

    combo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("combos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    combo: Mapped[Combo] = relationship("Combo", back_populates="items")
    options: Mapped[list[ComboOption]] = relationship(
        "ComboOption",
        back_populates="item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ComboItem {self.name}>"


class ComboOption(Base, UUIDMixin):
    """
    A choice within a combo slot, pointing at a real product.

    `extra_price` is the surcharge for picking this option over the base one —
    a large drink inside a medium meal, for instance.
    """

    __tablename__ = "combo_options"
    __table_args__ = (
        UniqueConstraint(
            "combo_item_id", "product_id", "combo_size_id", name="uq_combo_option"
        ),
    )

    combo_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("combo_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    combo_size_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("combo_sizes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    extra_price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    item: Mapped[ComboItem] = relationship("ComboItem", back_populates="options")

    def __repr__(self) -> str:
        return f"<ComboOption item={self.combo_item_id} product={self.product_id}>"


class SellingMethodEnum(str, enum.Enum):
    UNIT = "unit"
    WEIGHT = "weight"
