"""Add admin passkeys and seed admin users

Revision ID: 032_admin_passkeys
Revises: 031_audit_logs
Create Date: 2026-06-06
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "032_admin_passkeys"
down_revision: Union[str, None] = "031_audit_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADMIN_EMAILS = (
    "admin@meltingmomentscakes.com",
    "fatema_f@hotmail.co.uk",
    "fahimakhtarabbasi@gmail.com",
    "h_abbasi97@hotmail.com",
)


def upgrade() -> None:
    op.create_table(
        "admin_passkeys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", sa.String(512), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("transports", postgresql.ARRAY(sa.String(30)), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_passkeys_user_id", "admin_passkeys", ["user_id"])
    op.create_index(
        "ix_admin_passkeys_credential_id",
        "admin_passkeys",
        ["credential_id"],
        unique=True,
    )

    op.create_table(
        "webauthn_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("challenge", sa.String(255), nullable=False),
        sa.Column("ceremony", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_webauthn_challenges_user_id", "webauthn_challenges", ["user_id"]
    )
    op.create_index("ix_webauthn_challenges_email", "webauthn_challenges", ["email"])
    op.create_index(
        "ix_webauthn_challenges_email_ceremony",
        "webauthn_challenges",
        ["email", "ceremony"],
    )
    op.create_index(
        "ix_webauthn_challenges_expires_at",
        "webauthn_challenges",
        ["expires_at"],
    )

    bind = op.get_bind()
    for email in ADMIN_EMAILS:
        bind.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, email, hashed_password, phone,
                    is_active, is_admin, is_guest, created_at, updated_at
                )
                VALUES (
                    :id, :email, :hashed_password, NULL,
                    TRUE, TRUE, FALSE, NOW(), NOW()
                )
                ON CONFLICT (email) DO UPDATE
                SET is_active = TRUE,
                    is_admin = TRUE,
                    is_guest = FALSE,
                    updated_at = NOW()
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "email": email,
                "hashed_password": None,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_webauthn_challenges_expires_at", table_name="webauthn_challenges")
    op.drop_index(
        "ix_webauthn_challenges_email_ceremony", table_name="webauthn_challenges"
    )
    op.drop_index("ix_webauthn_challenges_email", table_name="webauthn_challenges")
    op.drop_index("ix_webauthn_challenges_user_id", table_name="webauthn_challenges")
    op.drop_table("webauthn_challenges")

    op.drop_index("ix_admin_passkeys_credential_id", table_name="admin_passkeys")
    op.drop_index("ix_admin_passkeys_user_id", table_name="admin_passkeys")
    op.drop_table("admin_passkeys")
