"""Inventory: categories, warehouses, items, levels, suppliers, the transaction
ledger, purchase orders and recipes.

Generated from the ORM metadata so the schema cannot drift from the models.

Revision ID: 037_inventory
Revises: 036_product_pos_fields
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "037_inventory"
down_revision: Union[str, None] = "036_product_pos_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "inventory_categories",
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_localized", sa.String(150), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("parent_id", UUID, nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["inventory_categories.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_categories_reference",
        "inventory_categories",
        ["reference"],
        unique=True,
    )

    op.create_table(
        "warehouses",
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_localized", sa.String(150), nullable=True),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_warehouses_branch_id", "warehouses", ["branch_id"], unique=False
    )

    op.create_table(
        "inventory_items",
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("barcode", sa.String(100), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_localized", sa.String(200), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("category_id", UUID, nullable=True),
        sa.Column("storage_unit", sa.String(30), nullable=False, server_default="unit"),
        sa.Column(
            "ingredient_unit", sa.String(30), nullable=False, server_default="unit"
        ),
        sa.Column(
            "storage_to_ingredient_factor",
            sa.Numeric(16, 6),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "minimum_level", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "maximum_level", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column("par_level", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(16, 6), nullable=False, server_default="0"),
        sa.Column(
            "costing_method", sa.String(30), nullable=False, server_default="fixed"
        ),
        sa.Column(
            "yield_percentage", sa.Numeric(6, 4), nullable=False, server_default="1"
        ),
        sa.Column("is_product", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"], ["inventory_categories.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_items_sku", "inventory_items", ["sku"], unique=True)
    op.create_index(
        "ix_inventory_items_barcode", "inventory_items", ["barcode"], unique=False
    )
    op.create_index(
        "ix_inventory_items_category_id",
        "inventory_items",
        ["category_id"],
        unique=False,
    )

    op.create_table(
        "suppliers",
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_localized", sa.String(200), nullable=True),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("contact_name", sa.String(150), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("tax_number", sa.String(50), nullable=True),
        sa.Column(
            "payment_terms_days", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suppliers_reference", "suppliers", ["reference"], unique=True)

    op.create_table(
        "supplier_items",
        sa.Column("supplier_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("supplier_sku", sa.String(100), nullable=True),
        sa.Column("cost", sa.Numeric(16, 6), nullable=False, server_default="0"),
        sa.Column("lead_time_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supplier_id", "item_id", name="uq_supplier_item"),
    )
    op.create_index(
        "ix_supplier_items_supplier_id", "supplier_items", ["supplier_id"], unique=False
    )
    op.create_index(
        "ix_supplier_items_item_id", "supplier_items", ["item_id"], unique=False
    )

    op.create_table(
        "purchase_orders",
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("supplier_id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("warehouse_id", UUID, nullable=True),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column(
            "additional_cost", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column("total_cost", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("creator_id", UUID, nullable=True),
        sa.Column("submitter_id", UUID, nullable=True),
        sa.Column("approver_id", UUID, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitter_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchase_orders_reference", "purchase_orders", ["reference"], unique=True
    )
    op.create_index(
        "ix_purchase_orders_status", "purchase_orders", ["status"], unique=False
    )
    op.create_index(
        "ix_purchase_orders_supplier_id",
        "purchase_orders",
        ["supplier_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_orders_branch_id", "purchase_orders", ["branch_id"], unique=False
    )
    op.create_index(
        "ix_purchase_orders_business_date",
        "purchase_orders",
        ["business_date"],
        unique=False,
    )

    op.create_table(
        "purchase_order_items",
        sa.Column("purchase_order_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column(
            "received_quantity", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column("unit", sa.String(30), nullable=False, server_default="storage"),
        sa.Column(
            "conversion_factor", sa.Numeric(16, 6), nullable=False, server_default="1"
        ),
        sa.Column("unit_cost", sa.Numeric(16, 6), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"], ["purchase_orders.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchase_order_items_purchase_order_id",
        "purchase_order_items",
        ["purchase_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_order_items_item_id",
        "purchase_order_items",
        ["item_id"],
        unique=False,
    )

    op.create_table(
        "inventory_transactions",
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("warehouse_id", UUID, nullable=True),
        sa.Column("other_branch_id", UUID, nullable=True),
        sa.Column("other_warehouse_id", UUID, nullable=True),
        sa.Column("supplier_id", UUID, nullable=True),
        sa.Column("purchase_order_id", UUID, nullable=True),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("reason_id", UUID, nullable=True),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column(
            "additional_cost", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column("paid_tax", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("creator_id", UUID, nullable=True),
        sa.Column("poster_id", UUID, nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["other_branch_id"], ["branches.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["other_warehouse_id"], ["warehouses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"], ["purchase_orders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reason_id"], ["reasons.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["poster_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_transactions_reference",
        "inventory_transactions",
        ["reference"],
        unique=True,
    )
    op.create_index(
        "ix_inventory_transactions_type",
        "inventory_transactions",
        ["type"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_transactions_status",
        "inventory_transactions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_transactions_branch_id",
        "inventory_transactions",
        ["branch_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_transactions_warehouse_id",
        "inventory_transactions",
        ["warehouse_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_transactions_business_date",
        "inventory_transactions",
        ["business_date"],
        unique=False,
    )

    op.create_table(
        "inventory_transaction_items",
        sa.Column("transaction_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False, server_default="storage"),
        sa.Column(
            "conversion_factor", sa.Numeric(16, 6), nullable=False, server_default="1"
        ),
        sa.Column(
            "quantity_in_ingredient_unit",
            sa.Numeric(16, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("unit_cost", sa.Numeric(16, 6), nullable=False, server_default="0"),
        sa.Column("total_cost", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("expected_quantity", sa.Numeric(16, 4), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["inventory_transactions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_transaction_items_transaction_id",
        "inventory_transaction_items",
        ["transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_transaction_items_item_id",
        "inventory_transaction_items",
        ["item_id"],
        unique=False,
    )

    op.create_table(
        "inventory_levels",
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("warehouse_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column(
            "average_cost", sa.Numeric(16, 6), nullable=False, server_default="0"
        ),
        sa.Column("last_counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "warehouse_id", name="uq_inventory_level"),
    )
    op.create_index(
        "ix_inventory_levels_item_id", "inventory_levels", ["item_id"], unique=False
    )
    op.create_index(
        "ix_inventory_levels_warehouse_id",
        "inventory_levels",
        ["warehouse_id"],
        unique=False,
    )

    op.create_table(
        "product_ingredients",
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column(
            "inactive_in_order_types", JSONB, nullable=False, server_default="[]"
        ),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "item_id", name="uq_product_ingredient"),
    )
    op.create_index(
        "ix_product_ingredients_product_id",
        "product_ingredients",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_ingredients_item_id",
        "product_ingredients",
        ["item_id"],
        unique=False,
    )

    op.create_table(
        "modifier_option_ingredients",
        sa.Column("modifier_option_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["modifier_option_id"], ["modifier_options.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "modifier_option_id", "item_id", name="uq_modifier_option_ingredient"
        ),
    )
    op.create_index(
        "ix_modifier_option_ingredients_modifier_option_id",
        "modifier_option_ingredients",
        ["modifier_option_id"],
        unique=False,
    )
    op.create_index(
        "ix_modifier_option_ingredients_item_id",
        "modifier_option_ingredients",
        ["item_id"],
        unique=False,
    )

    op.create_table(
        "inventory_item_ingredients",
        sa.Column("parent_item_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_item_id", "item_id", name="uq_inventory_item_ingredient"
        ),
    )
    op.create_index(
        "ix_inventory_item_ingredients_parent_item_id",
        "inventory_item_ingredients",
        ["parent_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_item_ingredients_item_id",
        "inventory_item_ingredients",
        ["item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("inventory_item_ingredients")
    op.drop_table("modifier_option_ingredients")
    op.drop_table("product_ingredients")
    op.drop_table("inventory_levels")
    op.drop_table("inventory_transaction_items")
    op.drop_table("inventory_transactions")
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("supplier_items")
    op.drop_table("suppliers")
    op.drop_table("inventory_items")
    op.drop_table("warehouses")
    op.drop_table("inventory_categories")
