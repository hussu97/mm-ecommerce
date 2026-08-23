"""The tables that let a "mark out of stock" on the terminal reach GrubOps.

The aggregators are fed from GrubTech's console, so today an item that runs out
is marked twice — once here, once there — and the second one is the one that
gets forgotten at half past four on a Friday. These three tables are what the
sync needs in order to say it automatically. Their shapes and the reasoning
behind them are on the models in `app/models/grubops.py`.

Schema only. Neither map is seeded here, and the branch one is the interesting
case: an earlier draft of this migration hardcoded branch references (`K001`,
`B001`) read out of migration 070, and inserted nothing at all when it was run
against a database whose Sharjah kitchen is called `SHJ`. A migration that
silently seeds nothing is worse than one that does not try, because the feature
it feeds then does nothing for a reason nobody can see.

So both maps are discovered instead. `POST /grubops/mappings/sync` reads
GrubOps' own location list, matches it to branches by name, and writes the rows
— which works whatever the local references happen to be, and is re-runnable
when a shop opens somewhere new.

Every branch it creates starts **inactive**, and that is the important half.
Turning sync on for a branch is a decision about whether the people standing in
that shop are marking things out on the terminal: where they are not, syncing
would push a permanent "everything is available" over whatever that counter
maintains in the GrubOps console by hand. Today that means Sharjah on and
Barsha Heights off. Somebody makes that call per branch in the console, and a
migration is not the place to guess it.

Revision ID: 131_grubops_mapping
Revises: 130_about_title_70
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "131_grubops_mapping"
down_revision: Union[str, None] = "130_about_title_70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grubops_location_map",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("grubops_location_id", sa.String(64), nullable=False),
        sa.Column("grubops_partner_id", sa.String(64), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.UniqueConstraint("branch_id", name="uq_grubops_location_branch"),
    )

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
        sa.Column(
            "approved", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        # A row points at a product or at an option, never both and never
        # neither — a half-filled row would otherwise be written happily and
        # then skipped in silence every tick.
        sa.CheckConstraint(
            "(mm_kind = 'product' AND product_id IS NOT NULL "
            "AND modifier_option_id IS NULL) OR "
            "(mm_kind = 'option' AND modifier_option_id IS NOT NULL "
            "AND product_id IS NULL)",
            name="ck_grubops_item_map_one_entity",
        ),
        sa.CheckConstraint(
            "grubops_type IN ('RECIPE', 'MODIFIER', 'NESTED_MODIFIER')",
            name="ck_grubops_item_map_type",
        ),
        sa.CheckConstraint(
            "match_method IN ('exact', 'fuzzy', 'manual')",
            name="ck_grubops_item_map_method",
        ),
        sa.UniqueConstraint("product_id", name="uq_grubops_item_map_product"),
        sa.UniqueConstraint("modifier_option_id", name="uq_grubops_item_map_option"),
    )
    op.create_index("ix_grubops_item_map_approved", "grubops_item_map", ["approved"])

    op.create_table(
        "grubops_sync_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "grubops_item_map_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grubops_item_map.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("last_pushed_available", sa.Boolean(), nullable=True),
        sa.Column("last_pushed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "branch_id", "grubops_item_map_id", name="uq_grubops_sync_state"
        ),
    )


def downgrade() -> None:
    op.drop_table("grubops_sync_state")
    op.drop_index("ix_grubops_item_map_approved", table_name="grubops_item_map")
    op.drop_table("grubops_item_map")
    op.drop_table("grubops_location_map")
