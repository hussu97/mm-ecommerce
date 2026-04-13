"""Add missing performance indexes

Indexes identified in the April 2026 tech-debt audit that were absent from
earlier migrations.  All are created with IF NOT EXISTS so the migration is
idempotent if any of them already exist.

Revision ID: 024
Revises: 023
Create Date: 2026-04-13
"""

from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── carts ──────────────────────────────────────────────────────────────────
    # Cart.session_id lookups (guest cart retrieval, order creation fallback)
    op.execute("CREATE INDEX IF NOT EXISTS ix_carts_session_id ON carts (session_id)")

    # ── cart_items ─────────────────────────────────────────────────────────────
    # Composite index for cart item dedup checks (cart_id + product_id)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cart_items_cart_product"
        " ON cart_items (cart_id, product_id)"
    )

    # ── refresh_tokens ─────────────────────────────────────────────────────────
    # Used when revoking all tokens for a user (password change / account delete)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id"
        " ON refresh_tokens (user_id)"
    )

    # Partial index for active (non-revoked, non-expired) token lookups
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_refresh_tokens_active"
        " ON refresh_tokens (user_id, expires_at)"
        " WHERE is_revoked = false"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_refresh_tokens_active")
    op.execute("DROP INDEX IF EXISTS ix_refresh_tokens_user_id")
    op.execute("DROP INDEX IF EXISTS ix_cart_items_cart_product")
    op.execute("DROP INDEX IF EXISTS ix_carts_session_id")
