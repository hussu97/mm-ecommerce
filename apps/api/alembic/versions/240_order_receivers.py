"""Gift recipient per order — the `order_receivers` one-to-one table.

A customer ordering for someone else gives us two people: themselves (who pays,
and whom the coupon rules and the confirmation email are about) and the
recipient (who the courier actually hands the box to). The orderer stays on the
order's own `customer_*` columns; the recipient lives here, at most one row per
order, cascade-deleted with it. Checkout-level, not a saved-address-book entry.

Read by `address_format.delivery_contact` to build the courier drop-off. No
phone verification — the recipient never signs in.

Revision ID: 240_order_receivers
Revises: 239_orders_legal_entity
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "240_order_receivers"
down_revision: Union[str, None] = "239_orders_legal_entity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_receivers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        # E.164 where parseable, the given string otherwise — the same policy
        # `orders.customer_phone` follows. Country/type sit beside it, null when
        # the number could not be parsed.
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("phone_country", sa.String(2), nullable=True),
        sa.Column("phone_type", sa.String(20), nullable=True),
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
    )
    # One receiver per order — the one-to-one is a database fact, and the index
    # also serves the only lookup (this order's receiver).
    op.create_unique_constraint(
        "uq_order_receivers_order_id", "order_receivers", ["order_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_order_receivers_order_id", "order_receivers", type_="unique")
    op.drop_table("order_receivers")
