"""Menu extensions (price tags, groups, allergens, combos, branch overrides) and
marketing (discounts, promotions, timed events, gift cards, loyalty, house accounts).

Generated from the ORM metadata so the schema cannot drift from the models.

Revision ID: 038_menu_marketing
Revises: 037_inventory
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "038_menu_marketing"
down_revision: Union[str, None] = "037_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "price_tags",
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_localized", sa.String(120), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_tags_reference", "price_tags", ["reference"], unique=True)

    op.create_table(
        "product_prices",
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("price_tag_id", UUID, nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["price_tag_id"], ["price_tags.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "price_tag_id", name="uq_product_price_tag"),
    )
    op.create_index(
        "ix_product_prices_product_id", "product_prices", ["product_id"], unique=False
    )
    op.create_index(
        "ix_product_prices_price_tag_id",
        "product_prices",
        ["price_tag_id"],
        unique=False,
    )

    op.create_table(
        "menu_groups",
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_localized", sa.String(150), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("image_url", sa.String(500), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_menu_groups_reference", "menu_groups", ["reference"], unique=True
    )

    op.create_table(
        "menu_group_products",
        sa.Column("group_id", UUID, nullable=False),
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["menu_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "product_id", name="uq_menu_group_product"),
    )
    op.create_index(
        "ix_menu_group_products_group_id",
        "menu_group_products",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        "ix_menu_group_products_product_id",
        "menu_group_products",
        ["product_id"],
        unique=False,
    )

    op.create_table(
        "allergens",
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_localized", sa.String(120), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("icon", sa.String(60), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "product_allergens",
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("allergen_id", UUID, nullable=False),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["allergen_id"], ["allergens.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "allergen_id", name="uq_product_allergen"),
    )
    op.create_index(
        "ix_product_allergens_product_id",
        "product_allergens",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_allergens_allergen_id",
        "product_allergens",
        ["allergen_id"],
        unique=False,
    )

    op.create_table(
        "branch_products",
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_in_stock", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("branch_id", "product_id", name="uq_branch_product"),
    )
    op.create_index(
        "ix_branch_products_branch_id", "branch_products", ["branch_id"], unique=False
    )
    op.create_index(
        "ix_branch_products_product_id", "branch_products", ["product_id"], unique=False
    )

    op.create_table(
        "combos",
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_localized", sa.String(200), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(500), nullable=True),
        sa.Column("category_id", UUID, nullable=True),
        sa.Column("tax_group_id", UUID, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tax_group_id"], ["tax_groups.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_combos_sku", "combos", ["sku"], unique=True)

    op.create_table(
        "combo_sizes",
        sa.Column("combo_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_localized", sa.String(120), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(["combo_id"], ["combos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_combo_sizes_combo_id", "combo_sizes", ["combo_id"], unique=False
    )

    op.create_table(
        "combo_items",
        sa.Column("combo_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_localized", sa.String(120), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(["combo_id"], ["combos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_combo_items_combo_id", "combo_items", ["combo_id"], unique=False
    )

    op.create_table(
        "combo_options",
        sa.Column("combo_item_id", UUID, nullable=False),
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("combo_size_id", UUID, nullable=True),
        sa.Column("extra_price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["combo_item_id"], ["combo_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["combo_size_id"], ["combo_sizes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "combo_item_id", "product_id", "combo_size_id", name="uq_combo_option"
        ),
    )
    op.create_index(
        "ix_combo_options_combo_item_id",
        "combo_options",
        ["combo_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_combo_options_product_id", "combo_options", ["product_id"], unique=False
    )
    op.create_index(
        "ix_combo_options_combo_size_id",
        "combo_options",
        ["combo_size_id"],
        unique=False,
    )

    op.create_table(
        "discounts",
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_localized", sa.String(150), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column(
            "qualification", sa.String(20), nullable=False, server_default="order"
        ),
        sa.Column("amount", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("is_percentage", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_taxable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "minimum_order_price", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "minimum_product_price",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("maximum_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "branch_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column(
            "order_types",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_discounts_reference", "discounts", ["reference"], unique=True)

    op.create_table(
        "promotions",
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_localized", sa.String(150), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("type", sa.String(20), nullable=False, server_default="basic"),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="spend"),
        sa.Column(
            "trigger_value", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("reward", sa.String(40), nullable=False),
        sa.Column(
            "reward_value", sa.Numeric(12, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "trigger_product_ids",
            postgresql.ARRAY(UUID),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "reward_product_ids",
            postgresql.ARRAY(UUID),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "branch_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column(
            "order_types",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "customer_tag_ids",
            postgresql.ARRAY(UUID),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "max_uses_per_order", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("from_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("to_time", sa.Integer(), nullable=False, server_default="1439"),
        sa.Column("is_mon", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_tue", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_wed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_thu", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_fri", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_sat", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_sun", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "timed_events",
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_localized", sa.String(150), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column(
            "product_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column(
            "category_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column(
            "branch_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column(
            "order_types",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("from_time", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("to_time", sa.Integer(), nullable=False, server_default="1439"),
        sa.Column("is_mon", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_tue", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_wed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_thu", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_fri", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_sat", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_sun", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "gift_cards",
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("initial_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("customer_id", UUID, nullable=True),
        sa.Column("issued_by_id", UUID, nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["issued_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gift_cards_code", "gift_cards", ["code"], unique=True)
    op.create_index("ix_gift_cards_status", "gift_cards", ["status"], unique=False)

    op.create_table(
        "gift_card_transactions",
        sa.Column("gift_card_id", UUID, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["gift_card_id"], ["gift_cards.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gift_card_transactions_gift_card_id",
        "gift_card_transactions",
        ["gift_card_id"],
        unique=False,
    )
    op.create_index(
        "ix_gift_card_transactions_type",
        "gift_card_transactions",
        ["type"],
        unique=False,
    )

    op.create_table(
        "loyalty_programs",
        sa.Column(
            "name",
            sa.String(150),
            nullable=False,
            server_default="Melting Moments Rewards",
        ),
        sa.Column(
            "points_per_currency_unit",
            sa.Numeric(10, 4),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "currency_per_point",
            sa.Numeric(10, 4),
            nullable=False,
            server_default="0.01",
        ),
        sa.Column(
            "minimum_points_to_redeem",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
        sa.Column(
            "points_expire_after_days", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "earn_on_discounted_items",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "branch_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "loyalty_transactions",
        sa.Column("customer_id", UUID, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_loyalty_transactions_customer_id",
        "loyalty_transactions",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_loyalty_transactions_type", "loyalty_transactions", ["type"], unique=False
    )

    op.create_table(
        "house_accounts",
        sa.Column("customer_id", UUID, nullable=False),
        sa.Column(
            "credit_limit", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", name="uq_house_account_customer"),
    )
    op.create_index(
        "ix_house_accounts_customer_id", "house_accounts", ["customer_id"], unique=False
    )

    op.create_table(
        "house_account_transactions",
        sa.Column("house_account_id", UUID, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["house_account_id"], ["house_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_house_account_transactions_house_account_id",
        "house_account_transactions",
        ["house_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_house_account_transactions_type",
        "house_account_transactions",
        ["type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("house_account_transactions")
    op.drop_table("house_accounts")
    op.drop_table("loyalty_transactions")
    op.drop_table("loyalty_programs")
    op.drop_table("gift_card_transactions")
    op.drop_table("gift_cards")
    op.drop_table("timed_events")
    op.drop_table("promotions")
    op.drop_table("discounts")
    op.drop_table("combo_options")
    op.drop_table("combo_items")
    op.drop_table("combo_sizes")
    op.drop_table("combos")
    op.drop_table("branch_products")
    op.drop_table("product_allergens")
    op.drop_table("allergens")
    op.drop_table("menu_group_products")
    op.drop_table("menu_groups")
    op.drop_table("product_prices")
    op.drop_table("price_tags")
