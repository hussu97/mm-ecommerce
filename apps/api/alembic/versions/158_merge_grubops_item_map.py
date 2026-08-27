"""Fold grubops_item_map into the generalized external_item_map, and drop it.

One item-map table for every external system. `external_item_map` gains the
columns GrubOps needs — `scope` (brand id), `external_sub_ref` (modifier id under
its recipe), `external_child_ref` (nested modifier), `external_type`
(RECIPE/MODIFIER/NESTED_MODIFIER) — and its unique key becomes the full composite
identity (NULLS NOT DISTINCT, so the name-keyed aggregator case still means one
row per `(system, external_ref)`).

The 192 live GrubOps rows are copied in **preserving their ids**, so
`grubops_sync_state`'s FK stays valid when it is repointed at the new table.
GrubOps' order-ingest resolver and OOS push read the same values from the new
columns, so behaviour is unchanged. Verified on prod first: 192 rows, all
approved, no null recipe, no duplicate composite key, sync-state 1:1.

Revision ID: 158_merge_grubops_item_map
Revises: 157_external_item_map
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "158_merge_grubops_item_map"
down_revision: Union[str, None] = "157_external_item_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. The GrubOps-supporting columns on the generalized table.
    op.add_column("external_item_map", sa.Column("scope", sa.String(64), nullable=True))
    op.add_column(
        "external_item_map", sa.Column("external_sub_ref", sa.String(64), nullable=True)
    )
    op.add_column(
        "external_item_map",
        sa.Column("external_child_ref", sa.String(64), nullable=True),
    )
    op.add_column(
        "external_item_map", sa.Column("external_type", sa.String(32), nullable=True)
    )
    op.create_check_constraint(
        "ck_external_item_map_type",
        "external_item_map",
        "external_type IS NULL OR external_type IN "
        "('RECIPE', 'MODIFIER', 'NESTED_MODIFIER')",
    )

    # 2. Swap the unique key to the full composite identity. NULLS NOT DISTINCT
    #    (Postgres 15+) keeps the name-keyed aggregator case — sub/child/scope all
    #    null — behaving like a plain (system, external_ref) unique.
    op.drop_constraint("uq_external_item_map_ref", "external_item_map", type_="unique")
    op.execute(
        "ALTER TABLE external_item_map ADD CONSTRAINT uq_external_item_map_identity "
        "UNIQUE NULLS NOT DISTINCT "
        "(system, scope, external_ref, external_sub_ref, external_child_ref)"
    )

    # 3. Copy the GrubOps map in, preserving ids. brand→scope, recipe→external_ref,
    #    modifier→external_sub_ref, child→external_child_ref, type→external_type.
    op.execute(
        """
        INSERT INTO external_item_map (
            id, system, scope, external_ref, external_sub_ref, external_child_ref,
            external_type, external_name, mm_kind, product_id, modifier_option_id,
            approved, approved_by, match_method, match_score, notes,
            created_at, updated_at)
        SELECT id, 'grubops', grubops_brand_id,
            COALESCE(grubops_recipe_id, grubops_modifier_id),
            grubops_modifier_id, grubops_child_modifier_id, grubops_type,
            grubops_name, mm_kind, product_id, modifier_option_id,
            approved, approved_by, match_method, match_score, notes,
            created_at, updated_at
        FROM grubops_item_map
        """
    )

    # 4. Repoint grubops_sync_state at the generalized table (ids preserved above).
    op.drop_constraint(
        "grubops_sync_state_grubops_item_map_id_fkey",
        "grubops_sync_state",
        type_="foreignkey",
    )
    op.alter_column(
        "grubops_sync_state",
        "grubops_item_map_id",
        new_column_name="external_item_map_id",
    )
    op.create_foreign_key(
        "grubops_sync_state_external_item_map_id_fkey",
        "grubops_sync_state",
        "external_item_map",
        ["external_item_map_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 5. The old table is now unreferenced.
    op.drop_table("grubops_item_map")


def downgrade() -> None:
    # Recreate grubops_item_map and copy the GrubOps rows back out. Best-effort:
    # aggregator rows added after the merge have no home here and are left behind.
    op.create_table(
        "grubops_item_map",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("mm_kind", sa.String(16), nullable=False),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "modifier_option_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modifier_options.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("grubops_brand_id", sa.String(64), nullable=False),
        sa.Column("grubops_recipe_id", sa.String(64), nullable=True),
        sa.Column("grubops_modifier_id", sa.String(64), nullable=True),
        sa.Column("grubops_child_modifier_id", sa.String(64), nullable=True),
        sa.Column("grubops_type", sa.String(32), nullable=False),
        sa.Column("grubops_name", sa.String(255), nullable=True),
        sa.Column("match_method", sa.String(16), nullable=False),
        sa.Column("match_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("approved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("product_id", name="uq_grubops_item_map_product"),
        sa.UniqueConstraint("modifier_option_id", name="uq_grubops_item_map_option"),
    )
    op.execute(
        """
        INSERT INTO grubops_item_map (
            id, mm_kind, product_id, modifier_option_id, grubops_brand_id,
            grubops_recipe_id, grubops_modifier_id, grubops_child_modifier_id,
            grubops_type, grubops_name, match_method, match_score, approved,
            approved_by, notes, created_at, updated_at)
        SELECT id, mm_kind, product_id, modifier_option_id, COALESCE(scope, ''),
            external_ref, external_sub_ref, external_child_ref,
            COALESCE(external_type, 'RECIPE'), external_name, match_method,
            match_score, approved, approved_by, notes, created_at, updated_at
        FROM external_item_map WHERE system = 'grubops'
        """
    )
    op.drop_constraint(
        "grubops_sync_state_external_item_map_id_fkey",
        "grubops_sync_state",
        type_="foreignkey",
    )
    op.alter_column(
        "grubops_sync_state",
        "external_item_map_id",
        new_column_name="grubops_item_map_id",
    )
    op.create_foreign_key(
        "grubops_sync_state_grubops_item_map_id_fkey",
        "grubops_sync_state",
        "grubops_item_map",
        ["grubops_item_map_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute("DELETE FROM external_item_map WHERE system = 'grubops'")
    op.drop_constraint(
        "uq_external_item_map_identity", "external_item_map", type_="unique"
    )
    op.create_unique_constraint(
        "uq_external_item_map_ref", "external_item_map", ["system", "external_ref"]
    )
    op.drop_constraint("ck_external_item_map_type", "external_item_map", type_="check")
    op.drop_column("external_item_map", "external_type")
    op.drop_column("external_item_map", "external_child_ref")
    op.drop_column("external_item_map", "external_sub_ref")
    op.drop_column("external_item_map", "scope")
