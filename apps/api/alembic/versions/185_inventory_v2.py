"""Add versioned recipes, immutable ledger metadata and shift controls.

Revision ID: 185_inventory_v2
Revises: 184_drop_branch_opening_window
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "185_inventory_v2"
down_revision: Union[str, None] = "184_drop_branch_opening_window"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _timestamps() -> tuple[sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    # Split the former broad inventory grants into explicit authorities without
    # taking access away from roles already operating these workflows.
    op.execute(
        """
        UPDATE roles
        SET permissions = (
          SELECT ARRAY(SELECT DISTINCT p FROM unnest(
            permissions || CASE WHEN 'inventory.read' = ANY(permissions)
              THEN ARRAY['inventory.ledger.read'] ELSE ARRAY[]::varchar[] END
            || CASE WHEN 'pos.till.manage' = ANY(permissions)
                        OR 'pos.register.access' = ANY(permissions)
              THEN ARRAY['inventory.read', 'inventory.reports.submit']
              ELSE ARRAY[]::varchar[] END
            || CASE WHEN 'inventory.adjustments.manage' = ANY(permissions)
              THEN ARRAY['inventory.reports.submit'] ELSE ARRAY[]::varchar[] END
            || CASE WHEN 'inventory.manage' = ANY(permissions)
              THEN ARRAY['inventory.counts.approve', 'inventory.projection.rebuild']
              ELSE ARRAY[]::varchar[] END
          ) p)
        )
        """
    )
    # Branch-facing classification; all imported rows remain safely stocked/raw
    # until the staged Foodics review explicitly changes them.
    op.add_column(
        "inventory_items",
        sa.Column("kind", sa.String(30), nullable=False, server_default="raw_material"),
    )
    op.add_column(
        "inventory_items",
        sa.Column(
            "tracking_mode", sa.String(20), nullable=False, server_default="stocked"
        ),
    )
    op.add_column(
        "inventory_items", sa.Column("storage_zone", sa.String(100), nullable=True)
    )
    op.add_column(
        "inventory_items",
        sa.Column("count_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_inventory_item_kind",
        "inventory_items",
        "kind IN ('raw_material', 'packaging', 'semi_finished', 'produced_good', 'resale_good')",
    )
    op.create_check_constraint(
        "ck_inventory_item_tracking_mode",
        "inventory_items",
        "tracking_mode IN ('stocked', 'phantom')",
    )
    op.create_check_constraint(
        "ck_inventory_item_positive_conversion",
        "inventory_items",
        "storage_to_ingredient_factor > 0",
    )
    op.create_check_constraint(
        "ck_inventory_item_nonnegative_cost", "inventory_items", "cost >= 0"
    )
    op.create_check_constraint(
        "ck_inventory_item_valid_yield",
        "inventory_items",
        "yield_percentage > 0 AND yield_percentage <= 1",
    )
    op.create_index("ix_inventory_items_kind", "inventory_items", ["kind"])
    op.create_index(
        "ix_inventory_items_tracking_mode", "inventory_items", ["tracking_mode"]
    )

    op.add_column(
        "inventory_levels",
        sa.Column("projected_through_sequence", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "inventory_levels",
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "branch_inventory_settings",
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column(
            "inventory_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "production_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "sales_consumption_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "validation_mode", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "allow_negative_stock", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "approval_cost_threshold",
            sa.Numeric(16, 4),
            nullable=False,
            server_default="100",
        ),
        sa.Column(
            "approval_variance_percent",
            sa.Numeric(8, 4),
            nullable=False,
            server_default="10",
        ),
        sa.Column("go_live_sequence", sa.BigInteger(), nullable=True),
        sa.Column("go_live_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("branch_id"),
    )
    op.create_index(
        "ix_branch_inventory_settings_branch_id",
        "branch_inventory_settings",
        ["branch_id"],
        unique=True,
    )

    # One hidden default stock container for every branch. The partial index
    # prevents a second default while still allowing future storage zones.
    op.execute(
        """
        WITH ranked AS (
          SELECT id, row_number() OVER (PARTITION BY branch_id ORDER BY is_default DESC, created_at, id) AS rn
          FROM warehouses WHERE deleted_at IS NULL AND is_active
        )
        UPDATE warehouses w SET is_default = (ranked.rn = 1)
        FROM ranked WHERE ranked.id = w.id
        """
    )
    op.execute(
        """
        INSERT INTO warehouses
          (id, branch_id, name, is_default, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), b.id, b.name || ' Store', true, true, now(), now()
        FROM branches b
        WHERE b.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM warehouses w
            WHERE w.branch_id = b.id AND w.deleted_at IS NULL AND w.is_active
          )
        """
    )
    op.create_index(
        "uq_warehouse_active_default_branch",
        "warehouses",
        ["branch_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND is_active AND deleted_at IS NULL"),
    )
    # The unique index proves "at most one". Deferred constraint triggers add
    # the other half — every active branch has exactly one at commit — while
    # still allowing an old default to be cleared before its replacement is
    # selected inside one transaction.
    op.execute(
        """
        CREATE FUNCTION assert_branch_default_stock_container(target_branch uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM branches
            WHERE id = target_branch AND deleted_at IS NULL
          ) AND (
            SELECT count(*) FROM warehouses
            WHERE branch_id = target_branch
              AND is_default AND is_active AND deleted_at IS NULL
          ) <> 1 THEN
            RAISE EXCEPTION 'active branch % must have exactly one default stock container',
              target_branch;
          END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_warehouse_default_per_branch()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            PERFORM assert_branch_default_stock_container(OLD.branch_id);
          END IF;
          IF TG_OP <> 'DELETE'
             AND (TG_OP = 'INSERT' OR NEW.branch_id IS DISTINCT FROM OLD.branch_id) THEN
            PERFORM assert_branch_default_stock_container(NEW.branch_id);
          ELSIF TG_OP = 'UPDATE' THEN
            PERFORM assert_branch_default_stock_container(NEW.branch_id);
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER warehouse_default_required
          AFTER INSERT OR UPDATE OR DELETE ON warehouses
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_warehouse_default_per_branch()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_new_branch_default()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM assert_branch_default_stock_container(NEW.id);
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER branch_default_required
          AFTER INSERT OR UPDATE ON branches
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_new_branch_default()
        """
    )
    op.execute(
        """
        INSERT INTO branch_inventory_settings
          (id, branch_id, inventory_enabled, production_enabled,
           sales_consumption_enabled, validation_mode, allow_negative_stock,
           approval_cost_threshold, approval_variance_percent, created_at, updated_at)
        SELECT gen_random_uuid(), b.id, false, false, false, true, true, 100, 10, now(), now()
        FROM branches b
        WHERE b.deleted_at IS NULL
        ON CONFLICT (branch_id) DO NOTHING
        """
    )

    op.create_table(
        "inventory_lots",
        sa.Column("warehouse_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("lot_reference", sa.String(120), nullable=False),
        sa.Column("manufactured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "warehouse_id", "item_id", "lot_reference", name="uq_inventory_lot"
        ),
    )

    op.create_table(
        "recipes",
        sa.Column("owner_kind", sa.String(30), nullable=False),
        sa.Column("product_id", UUID, nullable=True),
        sa.Column("modifier_option_id", UUID, nullable=True),
        sa.Column("inventory_item_id", UUID, nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "((product_id IS NOT NULL)::int + (modifier_option_id IS NOT NULL)::int + "
            "(inventory_item_id IS NOT NULL)::int) = 1",
            name="ck_recipe_one_owner",
        ),
        sa.CheckConstraint(
            "(owner_kind = 'product' AND product_id IS NOT NULL) OR "
            "(owner_kind = 'modifier_option' AND modifier_option_id IS NOT NULL) OR "
            "(owner_kind = 'inventory_item' AND inventory_item_id IS NOT NULL)",
            name="ck_recipe_owner_kind",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["modifier_option_id"], ["modifier_options.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="uq_recipe_product"),
        sa.UniqueConstraint("modifier_option_id", name="uq_recipe_modifier_option"),
        sa.UniqueConstraint("inventory_item_id", name="uq_recipe_inventory_item"),
    )
    op.create_index("ix_recipes_owner_kind", "recipes", ["owner_kind"])

    op.create_table(
        "recipe_versions",
        sa.Column("recipe_id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(30), nullable=False, server_default="mm"),
        sa.Column("source_payload_hash", sa.String(64), nullable=True),
        sa.Column(
            "source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", UUID, nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')", name="ck_recipe_version_status"
        ),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recipe_id", "version_number", name="uq_recipe_version_number"
        ),
    )
    op.create_index("ix_recipe_versions_recipe_id", "recipe_versions", ["recipe_id"])
    op.create_index("ix_recipe_versions_status", "recipe_versions", ["status"])
    op.create_index(
        "uq_recipe_one_active_version",
        "recipe_versions",
        ["recipe_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_recipe_one_draft_version",
        "recipe_versions",
        ["recipe_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )

    op.create_table(
        "recipe_lines",
        sa.Column("recipe_version_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("ingredient_unit", sa.String(30), nullable=False),
        sa.Column(
            "yield_percentage", sa.Numeric(8, 6), nullable=False, server_default="1"
        ),
        sa.Column(
            "inactive_in_order_types",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_metadata", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        *_timestamps(),
        sa.CheckConstraint("quantity > 0", name="ck_recipe_line_positive_quantity"),
        sa.CheckConstraint(
            "yield_percentage > 0 AND yield_percentage <= 1",
            name="ck_recipe_line_valid_yield",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_version_id"], ["recipe_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recipe_version_id", "item_id", name="uq_recipe_version_item"
        ),
    )
    op.create_index(
        "ix_recipe_lines_recipe_version_id", "recipe_lines", ["recipe_version_id"]
    )
    op.create_index("ix_recipe_lines_item_id", "recipe_lines", ["item_id"])

    # Backfill any existing compatibility recipes into immutable version 1.
    op.execute(
        """
        INSERT INTO recipes (id, owner_kind, product_id, created_at, updated_at)
        SELECT gen_random_uuid(), 'product', product_id, now(), now()
        FROM product_ingredients GROUP BY product_id
        ON CONFLICT (product_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO recipes (id, owner_kind, modifier_option_id, created_at, updated_at)
        SELECT gen_random_uuid(), 'modifier_option', modifier_option_id, now(), now()
        FROM modifier_option_ingredients GROUP BY modifier_option_id
        ON CONFLICT (modifier_option_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO recipes (id, owner_kind, inventory_item_id, created_at, updated_at)
        SELECT gen_random_uuid(), 'inventory_item', parent_item_id, now(), now()
        FROM inventory_item_ingredients GROUP BY parent_item_id
        ON CONFLICT (inventory_item_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO recipe_versions
          (id, recipe_id, version_number, status, source, activated_at, created_at, updated_at)
        SELECT gen_random_uuid(), r.id, 1, 'active', 'legacy', now(), now(), now()
        FROM recipes r
        WHERE NOT EXISTS (SELECT 1 FROM recipe_versions rv WHERE rv.recipe_id = r.id)
        """
    )
    op.execute(
        """
        INSERT INTO recipe_lines
          (id, recipe_version_id, item_id, quantity, ingredient_unit,
           yield_percentage, inactive_in_order_types, display_order,
           source_metadata, created_at, updated_at)
        SELECT gen_random_uuid(), rv.id, pi.item_id, pi.quantity, ii.ingredient_unit,
               1, pi.inactive_in_order_types, 0, '{}', now(), now()
        FROM product_ingredients pi
        JOIN recipes r ON r.product_id = pi.product_id
        JOIN recipe_versions rv ON rv.recipe_id = r.id AND rv.version_number = 1
        JOIN inventory_items ii ON ii.id = pi.item_id
        ON CONFLICT (recipe_version_id, item_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO recipe_lines
          (id, recipe_version_id, item_id, quantity, ingredient_unit,
           yield_percentage, inactive_in_order_types, display_order,
           source_metadata, created_at, updated_at)
        SELECT gen_random_uuid(), rv.id, mi.item_id, mi.quantity, ii.ingredient_unit,
               1, '[]', 0, '{}', now(), now()
        FROM modifier_option_ingredients mi
        JOIN recipes r ON r.modifier_option_id = mi.modifier_option_id
        JOIN recipe_versions rv ON rv.recipe_id = r.id AND rv.version_number = 1
        JOIN inventory_items ii ON ii.id = mi.item_id
        ON CONFLICT (recipe_version_id, item_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO recipe_lines
          (id, recipe_version_id, item_id, quantity, ingredient_unit,
           yield_percentage, inactive_in_order_types, display_order,
           source_metadata, created_at, updated_at)
        SELECT gen_random_uuid(), rv.id, iii.item_id, iii.quantity, ii.ingredient_unit,
               1, '[]', 0, '{}', now(), now()
        FROM inventory_item_ingredients iii
        JOIN recipes r ON r.inventory_item_id = iii.parent_item_id
        JOIN recipe_versions rv ON rv.recipe_id = r.id AND rv.version_number = 1
        JOIN inventory_items ii ON ii.id = iii.item_id
        ON CONFLICT (recipe_version_id, item_id) DO NOTHING
        """
    )

    op.execute("CREATE SEQUENCE inventory_posting_sequence")
    op.execute("CREATE SEQUENCE inventory_reference_sequence")
    op.execute(
        """
        SELECT setval(
          'inventory_reference_sequence',
          GREATEST(
            COALESCE((
              SELECT MAX(substring(reference FROM '([0-9]+)$')::bigint)
              FROM (
                SELECT reference FROM inventory_transactions
                UNION ALL SELECT reference FROM purchase_orders
                UNION ALL SELECT reference FROM transfer_orders
              ) reference_rows
            ), 0) + 1,
            1
          ),
          false
        )
        """
    )
    op.add_column(
        "inventory_transactions",
        sa.Column("posting_sequence", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "inventory_transactions",
        sa.Column("source_accepted_sequence", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "inventory_transactions",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inventory_transactions",
        sa.Column("idempotency_key", sa.String(200), nullable=True),
    )
    op.add_column(
        "inventory_transactions", sa.Column("source_type", sa.String(40), nullable=True)
    )
    op.add_column(
        "inventory_transactions", sa.Column("source_id", sa.String(100), nullable=True)
    )
    op.add_column(
        "inventory_transactions",
        sa.Column("reverses_transaction_id", UUID, nullable=True),
    )
    op.add_column(
        "inventory_transactions", sa.Column("correction_group_id", UUID, nullable=True)
    )
    op.create_foreign_key(
        "fk_inventory_transaction_reverses",
        "inventory_transactions",
        "inventory_transactions",
        ["reverses_transaction_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        UPDATE inventory_transactions
        SET posting_sequence = COALESCE(
              posting_sequence, nextval('inventory_posting_sequence')
            ),
            posted_at = COALESCE(posted_at, updated_at, created_at, now()),
            occurred_at = COALESCE(occurred_at, created_at)
        WHERE status = 'closed'
        """
    )
    op.create_index(
        "ix_inventory_transactions_posting_sequence",
        "inventory_transactions",
        ["posting_sequence"],
        unique=True,
    )
    op.create_index(
        "ix_inventory_transactions_source_accepted_sequence",
        "inventory_transactions",
        ["source_accepted_sequence"],
    )
    op.create_index(
        "ix_inventory_transactions_correction_group_id",
        "inventory_transactions",
        ["correction_group_id"],
    )
    # PostgreSQL unique constraints already allow multiple NULL values, which
    # is exactly the legacy/manual-transaction case.
    op.create_unique_constraint(
        "uq_inventory_transaction_idempotency",
        "inventory_transactions",
        ["idempotency_key"],
    )
    op.create_check_constraint(
        "ck_inventory_closed_posting_metadata",
        "inventory_transactions",
        "status <> 'closed' OR (posting_sequence IS NOT NULL AND posted_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        "type IN ('purchasing', 'transfer_send', 'transfer_receive', "
        "'quantity_adjustment', 'return_to_supplier', 'production', "
        "'consumption_from_production', 'consumption_from_orders', "
        "'return_from_orders', 'return_from_transfers', 'waste_from_orders', "
        "'waste_from_production', 'cost_adjustment', 'inventory_count', "
        "'opening_balance', 'internal_use')",
    )

    op.add_column(
        "inventory_transaction_items",
        sa.Column(
            "signed_quantity", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "inventory_transaction_items",
        sa.Column("balance_after_quantity", sa.Numeric(20, 6), nullable=True),
    )
    op.add_column(
        "inventory_transaction_items",
        sa.Column("balance_after_value", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "inventory_transaction_items",
        sa.Column("previous_unit_cost", sa.Numeric(16, 6), nullable=True),
    )
    op.add_column(
        "inventory_transaction_items",
        sa.Column("recipe_version_id", UUID, nullable=True),
    )
    op.add_column(
        "inventory_transaction_items",
        sa.Column(
            "recipe_path", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
    )
    op.add_column(
        "inventory_transaction_items", sa.Column("lot_id", UUID, nullable=True)
    )
    op.create_check_constraint(
        "ck_inventory_transaction_item_unit",
        "inventory_transaction_items",
        "unit IN ('storage', 'ingredient')",
    )
    op.create_foreign_key(
        "fk_inventory_line_recipe_version",
        "inventory_transaction_items",
        "recipe_versions",
        ["recipe_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_inventory_line_lot",
        "inventory_transaction_items",
        "inventory_lots",
        ["lot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE inventory_transaction_items line
        SET signed_quantity = CASE
          WHEN tx.type IN ('purchasing', 'transfer_receive', 'production',
                           'return_from_orders', 'return_from_transfers')
            THEN line.quantity_in_ingredient_unit
          WHEN tx.type IN ('transfer_send', 'return_to_supplier',
                           'consumption_from_production', 'consumption_from_orders',
                           'waste_from_orders', 'waste_from_production')
            THEN -line.quantity_in_ingredient_unit
          WHEN tx.type = 'inventory_count'
            THEN line.quantity_in_ingredient_unit - COALESCE(line.expected_quantity, 0)
          WHEN tx.type = 'quantity_adjustment'
            THEN line.quantity_in_ingredient_unit
          ELSE 0
        END
        FROM inventory_transactions tx
        WHERE tx.id = line.transaction_id AND tx.status = 'closed'
        """
    )

    op.create_table(
        "inventory_source_events",
        sa.Column("accepted_sequence", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "frozen_plan", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "recipe_version_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("transaction_id", UUID, nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'posted', 'cancelled', 'exception')",
            name="ck_inventory_source_event_status",
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["inventory_transactions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accepted_sequence"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_inventory_source_event_idempotency"
        ),
    )
    op.create_index(
        "ix_inventory_source_events_accepted_sequence",
        "inventory_source_events",
        ["accepted_sequence"],
        unique=True,
    )
    op.create_index(
        "ix_inventory_source_events_branch_id", "inventory_source_events", ["branch_id"]
    )
    op.create_index(
        "ix_inventory_source_events_source_type",
        "inventory_source_events",
        ["source_type"],
    )
    op.create_index(
        "ix_inventory_source_events_source_id", "inventory_source_events", ["source_id"]
    )
    op.create_index(
        "ix_inventory_source_events_status", "inventory_source_events", ["status"]
    )

    op.create_table(
        "inventory_report_templates",
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("report_type", sa.String(40), nullable=False),
        sa.Column("cadence", sa.String(30), nullable=False, server_default="per_till"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "configuration", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("approval_cost_threshold", sa.Numeric(16, 4), nullable=True),
        sa.Column("approval_variance_percent", sa.Numeric(8, 4), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "report_type IN ('production', 'finished_goods', 'raw_materials', 'packaging', 'spot_check')",
            name="ck_inventory_report_template_type",
        ),
        sa.CheckConstraint(
            "cadence IN ('per_till', 'per_business_day', 'ad_hoc')",
            name="ck_inventory_report_template_cadence",
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "branch_id", "name", name="uq_inventory_report_template_name"
        ),
    )
    op.create_index(
        "ix_inventory_report_templates_branch_id",
        "inventory_report_templates",
        ["branch_id"],
    )
    op.create_index(
        "ix_inventory_report_templates_report_type",
        "inventory_report_templates",
        ["report_type"],
    )

    op.create_table(
        "inventory_report_template_items",
        sa.Column("template_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "required_input",
            sa.String(30),
            nullable=False,
            server_default="physical_count",
        ),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"], ["inventory_report_templates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_id", "item_id", name="uq_inventory_report_template_item"
        ),
        sa.CheckConstraint(
            "required_input IN ('physical_count', 'production', 'internal_use', "
            "'waste', 'receipt')",
            name="ck_inventory_report_template_item_input",
        ),
    )
    op.create_index(
        "ix_inventory_report_template_items_template_id",
        "inventory_report_template_items",
        ["template_id"],
    )

    op.create_table(
        "shift_inventory_reports",
        sa.Column("template_id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("till_id", UUID, nullable=True),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="outstanding"
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("last_save_idempotency_key", sa.String(200), nullable=True),
        sa.Column("last_save_payload_hash", sa.String(64), nullable=True),
        sa.Column(
            "template_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("base_posting_sequence", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("deferred_reason", sa.Text(), nullable=True),
        sa.Column("submitted_by", UUID, nullable=True),
        sa.Column("approved_by", UUID, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_id", UUID, nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('outstanding', 'draft', 'pending_approval', 'approved', 'posted', 'deferred', 'skipped', 'rejected')",
            name="ck_shift_inventory_report_status",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["inventory_report_templates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["till_id"], ["tills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["inventory_transactions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_shift_inventory_report_idempotency"
        ),
    )
    op.create_index(
        "ix_shift_inventory_reports_branch_id", "shift_inventory_reports", ["branch_id"]
    )
    op.create_index(
        "ix_shift_inventory_reports_template_id",
        "shift_inventory_reports",
        ["template_id"],
    )
    op.create_index(
        "ix_shift_inventory_reports_till_id", "shift_inventory_reports", ["till_id"]
    )
    op.create_index(
        "ix_shift_inventory_reports_business_date",
        "shift_inventory_reports",
        ["business_date"],
    )

    op.create_table(
        "shift_inventory_report_lines",
        sa.Column("report_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column(
            "opening_quantity", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
        sa.Column(
            "purchasing_quantity", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
        sa.Column(
            "transfer_in_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "production_quantity", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
        sa.Column(
            "sales_consumption_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "production_consumption_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "transfer_out_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "waste_quantity", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
        sa.Column(
            "internal_use_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "expected_quantity", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
        sa.Column("entered_quantity", sa.Numeric(20, 6), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("variance_quantity", sa.Numeric(20, 6), nullable=True),
        sa.Column("variance_cost", sa.Numeric(20, 4), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column(
            "source_summary", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"], ["shift_inventory_reports.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id", "item_id", name="uq_shift_inventory_report_line"
        ),
    )
    op.create_index(
        "ix_shift_inventory_report_lines_report_id",
        "shift_inventory_report_lines",
        ["report_id"],
    )

    op.add_column(
        "external_item_map", sa.Column("inventory_item_id", UUID, nullable=True)
    )
    op.create_foreign_key(
        "fk_external_item_map_inventory_item",
        "external_item_map",
        "inventory_items",
        ["inventory_item_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("ck_external_item_map_kind", "external_item_map", type_="check")
    op.drop_constraint(
        "ck_external_item_map_one_entity", "external_item_map", type_="check"
    )
    op.create_check_constraint(
        "ck_external_item_map_kind",
        "external_item_map",
        "mm_kind IN ('product', 'option', 'category', 'inventory_item')",
    )
    op.create_check_constraint(
        "ck_external_item_map_one_entity",
        "external_item_map",
        "((product_id IS NOT NULL)::int + (modifier_option_id IS NOT NULL)::int + "
        "(category_id IS NOT NULL)::int + (inventory_item_id IS NOT NULL)::int) <= 1 "
        "AND (product_id IS NULL OR mm_kind = 'product') "
        "AND (modifier_option_id IS NULL OR mm_kind = 'option') "
        "AND (category_id IS NULL OR mm_kind = 'category') "
        "AND (inventory_item_id IS NULL OR mm_kind = 'inventory_item')",
    )

    # Closed ledger rows and activated recipe history are append-only even for
    # accidental direct SQL writes.
    op.execute(
        """
        CREATE FUNCTION prevent_closed_inventory_transaction_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'closed' THEN
            RAISE EXCEPTION 'closed inventory transactions are immutable';
          END IF;
          IF TG_OP = 'UPDATE' AND NEW.status = 'closed'
             AND COALESCE(current_setting('mm.inventory_posting', true), '') <> 'on' THEN
            RAISE EXCEPTION 'inventory transactions must be closed through the ledger poster';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER inventory_transaction_immutable
          BEFORE UPDATE OR DELETE ON inventory_transactions
          FOR EACH ROW EXECUTE FUNCTION prevent_closed_inventory_transaction_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_closed_inventory_line_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
          SELECT status INTO parent_status FROM inventory_transactions
          WHERE id = CASE WHEN TG_OP = 'INSERT' THEN NEW.transaction_id ELSE OLD.transaction_id END;
          IF parent_status = 'closed' THEN
            RAISE EXCEPTION 'closed inventory transaction lines are immutable';
          END IF;
          IF TG_OP = 'UPDATE' AND NEW.transaction_id <> OLD.transaction_id THEN
            SELECT status INTO parent_status FROM inventory_transactions
            WHERE id = NEW.transaction_id;
            IF parent_status = 'closed' THEN
              RAISE EXCEPTION 'closed inventory transaction lines are immutable';
            END IF;
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER inventory_transaction_line_immutable
          BEFORE INSERT OR UPDATE OR DELETE ON inventory_transaction_items
          FOR EACH ROW EXECUTE FUNCTION prevent_closed_inventory_line_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_inventory_source_snapshot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'inventory source events are append-only';
          END IF;
          IF NEW.id <> OLD.id
             OR NEW.created_at <> OLD.created_at
             OR NEW.accepted_sequence <> OLD.accepted_sequence
             OR NEW.branch_id <> OLD.branch_id
             OR NEW.source_type <> OLD.source_type
             OR NEW.source_id <> OLD.source_id
             OR NEW.source_revision <> OLD.source_revision
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
             OR NEW.accepted_at <> OLD.accepted_at
             OR NEW.frozen_plan <> OLD.frozen_plan
             OR NEW.recipe_version_ids <> OLD.recipe_version_ids THEN
            RAISE EXCEPTION 'accepted inventory source snapshots are immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER inventory_source_snapshot_immutable
          BEFORE UPDATE OR DELETE ON inventory_source_events
          FOR EACH ROW EXECUTE FUNCTION prevent_inventory_source_snapshot_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_recipe_owner_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM recipe_versions WHERE recipe_id = OLD.id) THEN
            RAISE EXCEPTION 'recipe ownership with version history is immutable';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER recipe_owner_immutable
          BEFORE UPDATE OR DELETE ON recipes
          FOR EACH ROW EXECUTE FUNCTION prevent_recipe_owner_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_published_recipe_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status IN ('active', 'retired') THEN
              RAISE EXCEPTION 'published recipe versions are immutable';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status = 'retired' THEN
            RAISE EXCEPTION 'published recipe versions are immutable';
          END IF;
          IF OLD.status = 'active' AND NOT (
            NEW.status = 'retired'
            AND NEW.id = OLD.id
            AND NEW.recipe_id = OLD.recipe_id
            AND NEW.version_number = OLD.version_number
            AND NEW.source = OLD.source
            AND NEW.source_payload_hash IS NOT DISTINCT FROM OLD.source_payload_hash
            AND NEW.source_metadata = OLD.source_metadata
            AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at
            AND NEW.activated_by IS NOT DISTINCT FROM OLD.activated_by
            AND NEW.created_at = OLD.created_at
            AND NEW.retired_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'active recipe contents are immutable';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER recipe_version_immutable
          BEFORE UPDATE OR DELETE ON recipe_versions
          FOR EACH ROW EXECUTE FUNCTION prevent_published_recipe_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_published_recipe_line_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE version_status text;
        DECLARE version_id uuid;
        BEGIN
          version_id := CASE WHEN TG_OP = 'INSERT' THEN NEW.recipe_version_id ELSE OLD.recipe_version_id END;
          SELECT status INTO version_status FROM recipe_versions
          WHERE id = version_id;
          IF version_status IN ('active', 'retired') THEN
            RAISE EXCEPTION 'published recipe lines are immutable';
          END IF;
          IF TG_OP = 'UPDATE' AND NEW.recipe_version_id <> OLD.recipe_version_id THEN
            SELECT status INTO version_status FROM recipe_versions
            WHERE id = NEW.recipe_version_id;
            IF version_status IN ('active', 'retired') THEN
              RAISE EXCEPTION 'published recipe lines are immutable';
            END IF;
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER recipe_line_immutable
          BEFORE INSERT OR UPDATE OR DELETE ON recipe_lines
          FOR EACH ROW EXECUTE FUNCTION prevent_published_recipe_line_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET permissions = array_remove(
          array_remove(
            array_remove(
              array_remove(permissions, 'inventory.ledger.read'),
              'inventory.reports.submit'
            ),
            'inventory.counts.approve'
          ),
          'inventory.projection.rebuild'
        )
        """
    )
    op.execute("DROP TRIGGER IF EXISTS recipe_line_immutable ON recipe_lines")
    op.execute("DROP FUNCTION IF EXISTS prevent_published_recipe_line_mutation")
    op.execute("DROP TRIGGER IF EXISTS recipe_version_immutable ON recipe_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_published_recipe_mutation")
    op.execute("DROP TRIGGER IF EXISTS recipe_owner_immutable ON recipes")
    op.execute("DROP FUNCTION IF EXISTS prevent_recipe_owner_mutation")
    op.execute(
        "DROP TRIGGER IF EXISTS inventory_source_snapshot_immutable ON inventory_source_events"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_inventory_source_snapshot_mutation")
    op.execute(
        "DROP TRIGGER IF EXISTS inventory_transaction_line_immutable ON inventory_transaction_items"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_closed_inventory_line_mutation")
    op.execute(
        "DROP TRIGGER IF EXISTS inventory_transaction_immutable ON inventory_transactions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_closed_inventory_transaction_mutation")
    op.execute("DROP TRIGGER IF EXISTS branch_default_required ON branches")
    op.execute("DROP FUNCTION IF EXISTS enforce_new_branch_default")
    op.execute("DROP TRIGGER IF EXISTS warehouse_default_required ON warehouses")
    op.execute("DROP FUNCTION IF EXISTS enforce_warehouse_default_per_branch")
    op.execute("DROP FUNCTION IF EXISTS assert_branch_default_stock_container")

    op.drop_constraint(
        "ck_external_item_map_one_entity", "external_item_map", type_="check"
    )
    op.drop_constraint("ck_external_item_map_kind", "external_item_map", type_="check")
    op.create_check_constraint(
        "ck_external_item_map_kind",
        "external_item_map",
        "mm_kind IN ('product', 'option', 'category')",
    )
    op.create_check_constraint(
        "ck_external_item_map_one_entity",
        "external_item_map",
        "((product_id IS NOT NULL)::int + (modifier_option_id IS NOT NULL)::int + (category_id IS NOT NULL)::int) <= 1 "
        "AND (product_id IS NULL OR mm_kind = 'product') "
        "AND (modifier_option_id IS NULL OR mm_kind = 'option') "
        "AND (category_id IS NULL OR mm_kind = 'category')",
    )
    op.drop_constraint(
        "fk_external_item_map_inventory_item", "external_item_map", type_="foreignkey"
    )
    op.drop_column("external_item_map", "inventory_item_id")

    op.drop_table("shift_inventory_report_lines")
    op.drop_table("shift_inventory_reports")
    op.drop_table("inventory_report_template_items")
    op.drop_table("inventory_report_templates")
    op.drop_table("inventory_source_events")

    op.drop_constraint(
        "fk_inventory_line_lot", "inventory_transaction_items", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_inventory_transaction_item_unit",
        "inventory_transaction_items",
        type_="check",
    )
    op.drop_constraint(
        "fk_inventory_line_recipe_version",
        "inventory_transaction_items",
        type_="foreignkey",
    )
    for column in (
        "lot_id",
        "recipe_path",
        "recipe_version_id",
        "previous_unit_cost",
        "balance_after_value",
        "balance_after_quantity",
        "signed_quantity",
    ):
        op.drop_column("inventory_transaction_items", column)

    op.drop_constraint(
        "fk_inventory_transaction_reverses",
        "inventory_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_inventory_transaction_idempotency",
        "inventory_transactions",
        type_="unique",
    )
    op.drop_constraint(
        "ck_inventory_closed_posting_metadata",
        "inventory_transactions",
        type_="check",
    )
    op.drop_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        type_="check",
    )
    for name in (
        "ix_inventory_transactions_correction_group_id",
        "ix_inventory_transactions_source_accepted_sequence",
        "ix_inventory_transactions_posting_sequence",
    ):
        op.drop_index(name, table_name="inventory_transactions")
    for column in (
        "correction_group_id",
        "reverses_transaction_id",
        "source_id",
        "source_type",
        "idempotency_key",
        "occurred_at",
        "source_accepted_sequence",
        "posting_sequence",
    ):
        op.drop_column("inventory_transactions", column)
    op.execute("DROP SEQUENCE inventory_posting_sequence")
    op.execute("DROP SEQUENCE inventory_reference_sequence")

    op.drop_table("recipe_lines")
    op.drop_table("recipe_versions")
    op.drop_table("recipes")
    op.drop_table("inventory_lots")
    op.drop_index("uq_warehouse_active_default_branch", table_name="warehouses")
    op.drop_table("branch_inventory_settings")
    op.drop_column("inventory_levels", "reconciled_at")
    op.drop_column("inventory_levels", "projected_through_sequence")
    op.drop_index("ix_inventory_items_tracking_mode", table_name="inventory_items")
    op.drop_index("ix_inventory_items_kind", table_name="inventory_items")
    op.drop_constraint(
        "ck_inventory_item_tracking_mode", "inventory_items", type_="check"
    )
    op.drop_constraint(
        "ck_inventory_item_positive_conversion", "inventory_items", type_="check"
    )
    op.drop_constraint(
        "ck_inventory_item_nonnegative_cost", "inventory_items", type_="check"
    )
    op.drop_constraint(
        "ck_inventory_item_valid_yield", "inventory_items", type_="check"
    )
    op.drop_constraint("ck_inventory_item_kind", "inventory_items", type_="check")
    op.drop_column("inventory_items", "count_order")
    op.drop_column("inventory_items", "storage_zone")
    op.drop_column("inventory_items", "tracking_mode")
    op.drop_column("inventory_items", "kind")
