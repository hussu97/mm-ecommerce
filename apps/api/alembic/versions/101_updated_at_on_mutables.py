"""The two mutable tables that could not say when they last changed.

`order_items` and `promo_codes` are edited in place — a line is voided, partly
returned, re-priced by a recalculation; a coupon's ceiling or expiry is moved —
and neither table recorded when. Reconciling a check against a kitchen ticket,
or a discount against the campaign that promised it, meant trusting memory.
Both get `updated_at`, timezone-aware, maintained by the ORM's `onupdate` like
every table on `TimestampMixin`.

`order_items` had no `created_at` either — only a nullable `added_at`, stamped
by the register and absent on every storefront line. It gets `created_at` too,
backfilled from `added_at` where the counter recorded one and from the parent
order's `created_at` where it did not, which is when a storefront line actually
came into being: the website writes the order and its lines in one breath.

`updated_at` is backfilled from `created_at` rather than `now()`: "not touched
since it was written" is the truthful statement about every existing row, and a
backfill that stamps migration time would forever read as a mass edit on the
day this shipped.

No server defaults, matching `TimestampMixin`: the ORM owns these values, and a
raw INSERT that skips it should fail loudly rather than get a silent stamp.

Revision ID: 101_updated_at_on_mutables
Revises: 100_business_date_format_check
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "101_updated_at_on_mutables"
down_revision: Union[str, None] = "100_business_date_format_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_items",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # `order_id` is NOT NULL with an FK, so the join covers every row; `now()`
    # is only the backstop for a parent row that somehow has no created_at,
    # which the mixin makes impossible.
    op.execute(
        "UPDATE order_items SET created_at = "
        "COALESCE(order_items.added_at, orders.created_at, now()) "
        "FROM orders WHERE orders.id = order_items.order_id"
    )
    op.execute("UPDATE order_items SET updated_at = created_at")
    op.alter_column("order_items", "created_at", nullable=False)
    op.alter_column("order_items", "updated_at", nullable=False)

    op.add_column(
        "promo_codes",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE promo_codes SET updated_at = created_at")
    op.alter_column("promo_codes", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("promo_codes", "updated_at")
    op.drop_column("order_items", "updated_at")
    op.drop_column("order_items", "created_at")
