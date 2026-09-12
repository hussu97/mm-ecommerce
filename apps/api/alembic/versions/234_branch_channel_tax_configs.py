"""Per-(branch, sales-channel) VAT and trade-license identity.

A branch no longer has one tax identity. The same physical branch can trade
under different trade licenses depending on the sales channel: Barsha's counter
sales run under a license that is NOT VAT-registered (under the threshold),
while its website and aggregator sales come under the VAT-registered Melting
Moments license. `branches.tax_number`/`tax_registration_name`/`tax_group_id`
can only say one thing per branch, and the same receipt renderer prints all
three channels — so identity has to be resolved per (branch, channel) and frozen
onto each order (see `235_orders_tax_identity`).

`channel_class` is one of counter / website / aggregator (`Order.source` maps
cashier -> counter, online -> website, aggregator -> aggregator). A branch with
no active row for a channel behaves exactly as today: VAT-registered, identity
inherited from the branch / business settings.

This creates the table and seeds one VAT-registered, identity-inheriting row per
active branch x channel so the admin console shows an editable grid for every
branch. It deliberately does NOT hardcode the Barsha counter override: branch
references differ per environment, and Barsha's non-VAT trade-license name is
set from the admin console. Seed rows are behaviourally identical to no row, so
this changes nothing until a row is edited. Guarded so it never fights the admin.

Revision ID: 234_branch_channel_tax
Revises: 233_agg_talabat_reversal_bf
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "234_branch_channel_tax"
down_revision: Union[str, None] = "233_agg_talabat_reversal_bf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "branch_channel_tax_configs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
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
            index=True,
        ),
        sa.Column("channel_class", sa.String(length=20), nullable=False),
        sa.Column(
            "vat_registered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "tax_group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tax_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tax_number", sa.String(length=50), nullable=True),
        sa.Column("tax_registration_name", sa.String(length=200), nullable=True),
        sa.Column("invoice_title", sa.String(length=120), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
        sa.UniqueConstraint("branch_id", "channel_class", name="uq_branch_channel_tax"),
        sa.CheckConstraint(
            "channel_class IN ('counter','website','aggregator')",
            name="ck_branch_channel_tax_class",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_branch_channel_tax_configs_tax_group_id",
        _TABLE,
        ["tax_group_id"],
        if_not_exists=True,
    )

    # Seed a VAT-registered, identity-inheriting row per active branch x channel.
    # Environment-agnostic (selects live branch ids rather than naming any),
    # guarded on the unique key so a re-run — or a database already carrying an
    # admin edit — inserts nothing. Identity columns stay null: the resolver
    # inherits the branch / business identity, so these rows behave exactly like
    # no row until an operator changes one.
    op.execute(
        f"""
        INSERT INTO {_TABLE} (id, branch_id, channel_class, vat_registered, is_active)
        SELECT gen_random_uuid(), b.id, c.channel_class, true, true
        FROM branches b
        CROSS JOIN (
            VALUES ('counter'), ('website'), ('aggregator')
        ) AS c(channel_class)
        WHERE b.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM {_TABLE} x
              WHERE x.branch_id = b.id AND x.channel_class = c.channel_class
          )
        """
    )


def downgrade() -> None:
    op.drop_table(_TABLE, if_exists=True)
