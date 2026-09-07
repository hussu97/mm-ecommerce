"""Set initial passwords for seeded admin users (now a guarded no-op)

Revision ID: 033_set_admin_initial_passwords
Revises: 032_admin_passkeys
Create Date: 2026-06-06

NOTE: this revision was applied to production but was missing from version
control — it existed only inside the deployed API image. It is restored here so
the migration chain is reproducible from a clean checkout. Production is already
stamped at this revision, so re-running it is a no-op in practice.

**F-OPS-21 — neutralised.** As originally restored it committed a literal bcrypt
password hash and re-granted `is_admin` to three named accounts on *every*
upgrade, so any fresh database (a developer's local, a restored dump, a review
app) came up with three known-password admin logins, and re-running it in
production would have reset those accounts' passwords back to a hash that was in
version control. The password rotation itself is an ops action handled
separately; this file's job is only to stop shipping the credential.

`upgrade` is now a no-op. The three production accounts are long since stamped at
this revision with their own rotated passwords, so there is nothing to apply; a
fresh database must not be seeded with admin credentials at all. Keeping the
revision (rather than deleting it) preserves the chain — production and every
existing checkout descend from it. No migration may carry a bcrypt literal;
`tests/unit/test_migration_chain.py` enforces that.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op  # noqa: F401 — kept so the revision reads as a migration

revision: str = "033_set_admin_initial_passwords"
down_revision: Union[str, None] = "032_admin_passkeys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: this revision must never seed a credential or grant admin. See the
    # module docstring (F-OPS-21). Production is already stamped here.
    pass


def downgrade() -> None:
    # Credential seeding is not reversible — the previous hashes are not recorded.
    pass
