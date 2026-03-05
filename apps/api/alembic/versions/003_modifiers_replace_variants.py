"""Modifiers replace variants

Revision ID: 003_modifiers_replace_variants
Revises: 002_refresh_tokens
Create Date: 2026-03-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "003_modifiers_replace_variants"
down_revision: Union[str, None] = "002_refresh_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop tables that reference product_variants
    op.drop_table("cart_items")
    op.drop_table("order_items")
    op.drop_table("product_variants")

    # 2. Alter categories: add localization + reference
    op.add_column(
        "categories", sa.Column("name_localized", sa.String(300), nullable=True)
    )
    op.add_column("categories", sa.Column("reference", sa.String(100), nullable=True))
    op.create_index("ix_categories_reference", "categories", ["reference"], unique=True)

    # 3. Alter products: add new columns, make base_price default 0
    op.add_column("products", sa.Column("sku", sa.String(100), nullable=True))
    op.create_index("ix_products_sku", "products", ["sku"], unique=True)
    op.add_column(
        "products", sa.Column("name_localized", sa.String(300), nullable=True)
    )
    op.add_column(
        "products", sa.Column("description_localized", sa.Text, nullable=True)
    )
    op.add_column("products", sa.Column("cost", sa.Numeric(10, 2), nullable=True))
    op.add_column("products", sa.Column("barcode", sa.String(100), nullable=True))
    op.add_column("products", sa.Column("calories", sa.Integer, nullable=True))
    op.add_column("products", sa.Column("preparation_time", sa.Integer, nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "is_sold_by_weight", sa.Boolean, nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "is_stock_product", sa.Boolean, nullable=False, server_default="false"
        ),
    )
    op.alter_column("products", "base_price", server_default="0")

    # 4. Create modifiers table
    op.create_table(
        "modifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_localized", sa.String(300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_modifiers_reference", "modifiers", ["reference"], unique=True)

    # 5. Create modifier_options table
    op.create_table(
        "modifier_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "modifier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modifiers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_localized", sa.String(300), nullable=True),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("calories", sa.Integer, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_modifier_options_modifier_id", "modifier_options", ["modifier_id"]
    )
    op.create_index("ix_modifier_options_sku", "modifier_options", ["sku"], unique=True)

    # 6. Create product_modifiers table
    op.create_table(
        "product_modifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "modifier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modifiers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("minimum_options", sa.Integer, nullable=False, server_default="0"),
        sa.Column("maximum_options", sa.Integer, nullable=False, server_default="1"),
        sa.Column("free_options", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unique_options", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("product_id", "modifier_id", name="uq_product_modifier"),
    )
    op.create_index(
        "ix_product_modifiers_product_id", "product_modifiers", ["product_id"]
    )
    op.create_index(
        "ix_product_modifiers_modifier_id", "product_modifiers", ["modifier_id"]
    )

    # 7. Recreate cart_items with new schema
    op.create_table(
        "cart_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cart_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("carts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "selected_options", postgresql.JSONB, nullable=False, server_default="[]"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])
    op.create_index("ix_cart_items_product_id", "cart_items", ["product_id"])

    # 8. Recreate order_items with new schema
    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("product_name_localized", sa.String(300), nullable=True),
        sa.Column("product_sku", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("base_price", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "options_price", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("total_price", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "selected_options_snapshot",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    # Drop new tables
    op.drop_table("order_items")
    op.drop_table("cart_items")
    op.drop_table("product_modifiers")
    op.drop_table("modifier_options")
    op.drop_table("modifiers")

    # Remove added columns from products
    op.drop_index("ix_products_sku", "products")
    op.drop_column("products", "sku")
    op.drop_column("products", "name_localized")
    op.drop_column("products", "description_localized")
    op.drop_column("products", "cost")
    op.drop_column("products", "barcode")
    op.drop_column("products", "calories")
    op.drop_column("products", "preparation_time")
    op.drop_column("products", "is_sold_by_weight")
    op.drop_column("products", "is_stock_product")

    # Remove added columns from categories
    op.drop_index("ix_categories_reference", "categories")
    op.drop_column("categories", "reference")
    op.drop_column("categories", "name_localized")

    # Recreate product_variants table
    op.create_table(
        "product_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("stock_quantity", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_product_variants_sku", "product_variants", ["sku"], unique=True)
    op.create_index(
        "ix_product_variants_product_id", "product_variants", ["product_id"]
    )

    # Recreate cart_items with old schema
    op.create_table(
        "cart_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cart_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("carts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])

    # Recreate order_items with old schema
    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("variant_name", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("total_price", sa.Numeric(10, 2), nullable=False),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
