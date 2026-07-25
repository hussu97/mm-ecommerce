"""Nested menu groups, and an explicit POS visibility flag on a product.

Foodics builds the terminal's menu from groups that nest inside one another —
"Drinks" holding "Hot Coffee" and "Cold Coffee" — and an item only reaches the
register through that tree. `menu_groups` already existed but was flat and
unused: no parent, no API, and no bearing on what a cashier could sell.

Two things are added:

  * `menu_groups.parent_id`, so a group can hold groups as well as products,
    with a guard against a group parenting itself.
  * `products.is_pos_visible`, the counterpart to `is_web_visible`. The tree
    decides how the menu is laid out and lets a whole branch be switched off
    at once; the flag is the per-product override, and answers "why can I not
    see this item" without the operator having to walk the tree.

Every existing product is left visible on the register, and one group is
seeded per active category so the terminal has a full menu the moment this
lands rather than an empty one waiting for someone to build a tree.

Revision ID: 044_menu_group_tree
Revises: 043_sell_by_weight
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "044_menu_group_tree"
down_revision: Union[str, None] = "043_sell_by_weight"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_groups",
        sa.Column("parent_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_menu_groups_parent",
        "menu_groups",
        "menu_groups",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_menu_groups_parent_id", "menu_groups", ["parent_id"])
    # A group that is its own parent makes the recursive walk non-terminating.
    # Deeper cycles are caught in the service, which has to walk anyway.
    op.create_check_constraint(
        "ck_menu_groups_not_self_parent",
        "menu_groups",
        "parent_id IS NULL OR parent_id <> id",
    )

    op.add_column(
        "products",
        sa.Column(
            "is_pos_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_products_is_pos_visible", "products", ["is_pos_visible", "is_active"]
    )

    # Seed a group per active category, in the same order, and file each
    # product under its own. Without this the terminal's menu would be empty
    # until somebody built the tree by hand.
    op.execute(
        """
        INSERT INTO menu_groups (id, name, name_localized, translations,
                                 reference, image_url, display_order, is_active,
                                 created_at, updated_at)
        SELECT gen_random_uuid(), c.name,
               nullif(c.translations #>> '{ar,name}', ''),
               coalesce(c.translations, '{}'::jsonb),
               'cat-' || left(c.id::text, 8), c.image_url, c.display_order, true,
               now(), now()
        FROM categories c
        WHERE c.is_active
          AND NOT EXISTS (
            SELECT 1 FROM menu_groups g
            WHERE g.reference = 'cat-' || left(c.id::text, 8)
          )
        """
    )
    op.execute(
        """
        INSERT INTO menu_group_products (id, group_id, product_id, display_order)
        SELECT gen_random_uuid(), g.id, p.id, p.display_order
        FROM products p
        JOIN categories c ON c.id = p.category_id
        JOIN menu_groups g ON g.reference = 'cat-' || left(c.id::text, 8)
        ON CONFLICT ON CONSTRAINT uq_menu_group_product DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM menu_groups WHERE reference LIKE 'cat-%'")
    op.drop_index("ix_products_is_pos_visible", table_name="products")
    op.drop_column("products", "is_pos_visible")
    op.drop_constraint("ck_menu_groups_not_self_parent", "menu_groups")
    op.drop_index("ix_menu_groups_parent_id", table_name="menu_groups")
    op.drop_constraint("fk_menu_groups_parent", "menu_groups", type_="foreignkey")
    op.drop_column("menu_groups", "parent_id")
