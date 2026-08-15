"""Stock that comes back on its own, and a modifier option that can run out.

`branch_products.is_in_stock` has existed since 038 and nothing ever read it.
Two things were missing before it could carry "86 it" from the terminal.

**When it comes back.** A counter marks kunafa out at four in the afternoon and
nobody is going to remember to turn it on again at close. `out_of_stock_until`
holds the moment it returns; null with `is_in_stock = false` means it stays out
until a person says otherwise. The column is read as part of the availability
question rather than swept by a job — a row whose moment has passed is
available again whether or not anything has run since. The sweep that tidies
lapsed rows is housekeeping, not the rule.

**Options run out too.** A box of three is still sellable when the fudge is
gone; it is the fudge that is unavailable, not the box. `branch_modifier_options`
is `branch_products` for one option of one modifier, with the same two columns
meaning the same two things — deliberately, so one reader answers both.

Both tables stay exception-only: a row exists where a branch differs from the
catalogue, so the ordinary case costs nothing and "no row" means "sold here".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "104_branch_availability_expiry"
down_revision: Union[str, None] = "103_soft_delete_consistency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branch_products",
        sa.Column("out_of_stock_until", sa.DateTime(timezone=True), nullable=True),
    )
    # An in-stock row has nothing to come back from. Without this a row can say
    # "available, and also returning at six", which is two answers to one
    # question and the reader would have to pick.
    op.create_check_constraint(
        "ck_branch_products_until_only_when_out",
        "branch_products",
        "out_of_stock_until IS NULL OR is_in_stock = false",
    )
    # The storefront asks "is anything out at this branch" on every listing.
    op.create_index(
        "ix_branch_products_out",
        "branch_products",
        ["branch_id", "product_id"],
        postgresql_where=sa.text("is_in_stock = false"),
    )

    op.create_table(
        "branch_modifier_options",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "modifier_option_id",
            UUID(as_uuid=True),
            sa.ForeignKey("modifier_options.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_in_stock", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("out_of_stock_until", sa.DateTime(timezone=True), nullable=True),
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
            "branch_id", "modifier_option_id", name="uq_branch_modifier_option"
        ),
        sa.CheckConstraint(
            "out_of_stock_until IS NULL OR is_in_stock = false",
            name="ck_branch_modifier_options_until_only_when_out",
        ),
    )
    op.create_index(
        "ix_branch_modifier_options_branch_id",
        "branch_modifier_options",
        ["branch_id"],
    )
    op.create_index(
        "ix_branch_modifier_options_option_id",
        "branch_modifier_options",
        ["modifier_option_id"],
    )
    op.create_index(
        "ix_branch_modifier_options_out",
        "branch_modifier_options",
        ["branch_id", "modifier_option_id"],
        postgresql_where=sa.text("is_in_stock = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_branch_modifier_options_out", "branch_modifier_options")
    op.drop_index("ix_branch_modifier_options_option_id", "branch_modifier_options")
    op.drop_index("ix_branch_modifier_options_branch_id", "branch_modifier_options")
    op.drop_table("branch_modifier_options")
    op.drop_index("ix_branch_products_out", "branch_products")
    op.drop_constraint(
        "ck_branch_products_until_only_when_out", "branch_products", type_="check"
    )
    op.drop_column("branch_products", "out_of_stock_until")
