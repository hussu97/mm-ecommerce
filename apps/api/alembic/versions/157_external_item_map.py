"""Generalized external-system item → catalogue map.

`external_item_map` is the generalised sibling of `grubops_item_map`: one row maps
an item as an external *system* names it (an aggregator's scraped item name, a
GrubOps recipe id, a Foodics sku) to an MM `Product` or `ModifierOption`. Aggregator
order promotion resolves scraped line names to products through it. Matching a name
is a guess, so nothing acts on a row until a human sets `approved` — the same gate
`grubops_item_map` uses.

Table only; no seed. Rows are proposed at runtime by promotion (unapproved) and
approved by a human, the way the GrubOps map is populated.

Revision ID: 157_external_item_map
Revises: 156_agg_order_promotion
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "157_external_item_map"
down_revision: Union[str, None] = "156_agg_order_promotion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYSTEMS = ("grubops", "foodics", "careem", "deliveroo", "talabat", "noon", "keeta")
_SYSTEMS_SQL = ", ".join(f"'{s}'" for s in _SYSTEMS)


def upgrade() -> None:
    op.create_table(
        "external_item_map",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("system", sa.String(20), nullable=False),
        sa.Column("external_ref", sa.String(200), nullable=False),
        sa.Column("external_name", sa.String(255), nullable=True),
        sa.Column("mm_kind", sa.String(16), nullable=False, server_default="product"),
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
        sa.Column("approved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column(
            "match_method", sa.String(16), nullable=False, server_default="fuzzy"
        ),
        sa.Column("match_score", sa.Numeric(5, 2), nullable=True),
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
        sa.UniqueConstraint("system", "external_ref", name="uq_external_item_map_ref"),
        sa.CheckConstraint(
            f"system IN ({_SYSTEMS_SQL})", name="ck_external_item_map_system"
        ),
        sa.CheckConstraint(
            "mm_kind IN ('product', 'option')", name="ck_external_item_map_kind"
        ),
        sa.CheckConstraint(
            "match_method IN ('exact', 'fuzzy', 'manual')",
            name="ck_external_item_map_method",
        ),
        sa.CheckConstraint(
            "NOT (product_id IS NOT NULL AND modifier_option_id IS NOT NULL) "
            "AND (product_id IS NULL OR mm_kind = 'product') "
            "AND (modifier_option_id IS NULL OR mm_kind = 'option')",
            name="ck_external_item_map_one_entity",
        ),
    )
    op.create_index(
        "ix_external_item_map_system_approved",
        "external_item_map",
        ["system", "approved"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_item_map_system_approved", table_name="external_item_map"
    )
    op.drop_table("external_item_map")
