"""The tables that let a "mark out of stock" on the terminal reach GrubOps.

The aggregators are fed from GrubTech's console, so today an item that runs out
is marked twice — once here, once there — and the second one is the one that
gets forgotten at half past four on a Friday. These three tables are what the
sync needs in order to say it automatically. Their shapes and the reasoning
behind them are on the models in `app/models/grubops.py`.

Two rows of content ride along with the schema: the branch to GrubOps-location
map, which is two rows and will not change unless the shop opens somewhere new.
It goes here rather than in a script for the reason every content migration in
this tree does — a script is only as good as somebody remembering to run it, and
until they do the sync has no idea which location is which and does nothing.

Guarded on `reference`, and inserted only where the branch exists and has no row
yet. So a database that has already been seeded gets nothing, a branch somebody
has since pointed at a different GrubOps location keeps that pointing, and a
deployment where K001/B001 do not exist gets no rows rather than an error.

The **item** map is deliberately not seeded here. Matching our catalogue to
GrubOps' is a live call to their API plus a fuzzy name match, which is neither
deterministic nor offline and has to be re-runnable as menus change; it belongs
to `POST /grubops/mappings/sync` and the admin screen over it, not to a
migration that must produce the same database every time it runs.

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


#: The partner this console belongs to. One shop, one partner; carried on each
#: row rather than in settings so a second brand needs no schema change.
PARTNER_ID = "6922fe267f5b1c6d208c634f"

#: branch reference -> (GrubOps location id, synced from the start).
#:
#: Ids read from their location API against the live account, 2026-08-23.
#: Karama and DSO are absent on purpose: they do not trade on GrubOps, and a
#: branch with no row here is simply never synced.
#:
#: Barsha Heights is mapped but starts **inactive**, because the register is
#: only running in Sharjah today. A branch whose staff are not marking things
#: out on the terminal has nothing true to say about its stock, and syncing it
#: would push a permanent "everything is available" over whatever the Barsha
#: counter is maintaining in the GrubOps console by hand. The id is recorded
#: now so that turning it on later is a switch in the admin console rather than
#: another migration.
LOCATIONS: dict[str, tuple[str, bool]] = {
    "K001": ("692300947f5b1c6d208c6352", True),  # Sharjah — register live
    "B001": ("692300c0323715175fede66c", False),  # Barsha Heights — not yet
}


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

    # ── the two locations ────────────────────────────────────────────────────
    # INSERT ... SELECT so the branch lookup and the guard are one statement:
    # nothing is written when the reference is absent, and the NOT EXISTS makes
    # a re-run — or a restore of a database that already has the row — a no-op
    # rather than a unique violation.
    for reference, (location_id, is_active) in LOCATIONS.items():
        op.execute(
            sa.text(
                """
                INSERT INTO grubops_location_map
                    (branch_id, grubops_location_id, grubops_partner_id, is_active)
                SELECT b.id, :location_id, :partner_id, :is_active
                  FROM branches b
                 WHERE b.reference = :reference
                   AND NOT EXISTS (
                       SELECT 1 FROM grubops_location_map m
                        WHERE m.branch_id = b.id
                   )
                """
            ).bindparams(
                location_id=location_id,
                partner_id=PARTNER_ID,
                is_active=is_active,
                reference=reference,
            )
        )


def downgrade() -> None:
    op.drop_table("grubops_sync_state")
    op.drop_index("ix_grubops_item_map_approved", table_name="grubops_item_map")
    op.drop_table("grubops_item_map")
    op.drop_table("grubops_location_map")
