"""Phone verification, and the coupons that require it.

A ledger rather than a flag on the account, because the customer this exists for
is a guest: no account to hang a flag on, and no session worth trusting across a
checkout that spans an SMS, a switch to the messages app, and a page reload. A
row keyed on the number survives all of that and is readable without the client
being involved.

`promo_codes.requires_phone_verification` is what makes `first_orders_limit`
worth having. That rule counts identities — account, email, phone — and two of
the three are free to mint. A phone number is the only one in the checkout that
costs anything to fake.

`users.phone_verified_at` is a timestamp, not a boolean, because the proof ages:
the question is never "was this ever verified" but "recently enough to still
mean something".

Revision ID: 087_phone_verification
Revises: 086_new_customer_coupons
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "087_phone_verification"
down_revision: Union[str, None] = "086_new_customer_coupons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "phone_verifications",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("firebase_uid", sa.String(128), nullable=True),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # SET NULL, not CASCADE: tidying up a guest row must not erase the
        # evidence that a number was verified, or the coupon it earned becomes
        # claimable all over again.
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The lookup is always "this number, verified since this cutoff", and it
    # runs on every coupon validation — which is checkout-blocking.
    op.create_index(
        "ix_phone_verifications_phone_verified_at",
        "phone_verifications",
        ["phone", "verified_at"],
    )
    op.create_index(
        "ix_phone_verifications_user_id", "phone_verifications", ["user_id"]
    )

    op.add_column(
        "users",
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "promo_codes",
        sa.Column(
            "requires_phone_verification",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("promo_codes", "requires_phone_verification")
    op.drop_column("users", "phone_verified_at")
    op.drop_index("ix_phone_verifications_user_id", table_name="phone_verifications")
    op.drop_index(
        "ix_phone_verifications_phone_verified_at", table_name="phone_verifications"
    )
    op.drop_table("phone_verifications")
