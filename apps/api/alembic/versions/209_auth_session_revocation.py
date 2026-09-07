"""Auth session revocation: password_changed_at + refresh-token families.

Two additive, nullable columns that give the auth layer a way to revoke
sessions it previously could not touch (F-POS-24):

  * `users.password_changed_at` — stamped whenever the password changes (the
    reset flow, a staff password edit). Every access and reset token now carries
    an `iat`; the token validators refuse any whose `iat` predates this instant,
    so a reset or a password change invalidates sessions that stateless access
    tokens would otherwise keep alive for their full lifetime, and makes a reset
    token single-use.

  * `refresh_tokens.token_family` — the rotation lineage a refresh token belongs
    to. Rotation carries the family forward; presenting a token that has already
    been rotated away (a replayed, stolen token) revokes the whole family. The
    column is indexed because that revoke is a lookup by family.

Both are nullable with no default and no backfill — they describe only what
happens from here. Existing refresh tokens carry a NULL family and simply age
out under the old single-token rules; existing users have never changed their
password as far as this column knows, which no token's `iat` can predate.

Revision ID: 209_auth_session_revocation
Revises: 208_order_promo_released
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "209_auth_session_revocation"
down_revision: Union[str, None] = "208_order_promo_released"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("token_family", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_refresh_tokens_token_family",
        "refresh_tokens",
        ["token_family"],
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_token_family", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "token_family")
    op.drop_column("users", "password_changed_at")
