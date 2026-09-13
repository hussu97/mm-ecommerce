"""Add `exclude_from_pdf` to menu-group memberships.

A product can sit in a menu group (so it sells on the register) yet be left off
the printable menu PDF — a staff drink, a placeholder, a one-off. The flag lives
on the membership (`menu_group_products`), not the product, so the same product
can still print via another group. Defaults false: every existing membership
keeps showing on the PDF.

Revision ID: 243_menu_group_pdf_exclude
Revises: 242_drop_static_marketplace_fees
Create Date: 2026-09-13
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "243_menu_group_pdf_exclude"
down_revision: Union[str, None] = "242_drop_static_marketplace_fees"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "menu_group_products",
        sa.Column(
            "exclude_from_pdf",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("menu_group_products", "exclude_from_pdf")
