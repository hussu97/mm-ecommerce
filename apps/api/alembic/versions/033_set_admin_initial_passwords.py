"""Set initial passwords for seeded admin users

Revision ID: 033_set_admin_initial_passwords
Revises: 032_admin_passkeys
Create Date: 2026-06-06

NOTE: this revision was applied to production but was missing from version
control — it existed only inside the deployed API image. It is restored here so
the migration chain is reproducible from a clean checkout. Production is already
stamped at this revision, so re-running it is a no-op in practice.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "033_set_admin_initial_passwords"
down_revision: Union[str, None] = "032_admin_passkeys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INITIAL_PASSWORD_HASH = "$2b$12$39MsmgEfBrBGQNqHmlWIvOTmrl/LychT.3wUcHDt7WmbJGboX7NL."


def upgrade() -> None:
    bind = op.get_bind()
    stmt = sa.text(
        """
        UPDATE users
        SET hashed_password = :password_hash,
            is_active = TRUE,
            is_admin = TRUE,
            is_guest = FALSE,
            updated_at = NOW()
        WHERE email IN (
            'fatema_f@hotmail.co.uk',
            'fahimakhtarabbasi@gmail.com',
            'h_abbasi97@hotmail.com'
        )
        """
    )
    bind.execute(stmt, {"password_hash": INITIAL_PASSWORD_HASH})


def downgrade() -> None:
    # Credential seeding is not reversible — the previous hashes are not recorded.
    pass
