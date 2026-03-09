"""Add VAT fields to orders table

Revision ID: 020
Revises: 019
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "vat_rate", sa.Numeric(5, 4), nullable=False, server_default="0.0500"
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "vat_amount", sa.Numeric(10, 2), nullable=False, server_default="0.00"
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "total_excl_vat", sa.Numeric(10, 2), nullable=False, server_default="0.00"
        ),
    )

    op.execute("""
        UPDATE orders SET
            vat_rate = 0.05,
            vat_amount = ROUND((subtotal - discount_amount) * 5.0 / 105.0, 2),
            total_excl_vat = ROUND((subtotal - discount_amount) / 1.05, 2)
    """)


def downgrade() -> None:
    op.drop_column("orders", "total_excl_vat")
    op.drop_column("orders", "vat_amount")
    op.drop_column("orders", "vat_rate")
