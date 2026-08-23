"""POS fields on products: tax group, pricing/costing method, non-revenue flag.

Existing products are backfilled onto the default tax group so VAT keeps being
charged exactly as it was before this change.

Revision ID: 036_product_pos_fields
Revises: 035_pos_orders
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "036_product_pos_fields"
down_revision: Union[str, None] = "035_pos_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "products", sa.Column("name_localized", sa.String(200), nullable=True)
    )
    op.add_column("products", sa.Column("tax_group_id", UUID, nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "pricing_method", sa.String(20), nullable=False, server_default="fixed"
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "costing_method", sa.String(20), nullable=False, server_default="fixed"
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "is_non_revenue", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "products",
        sa.Column("walking_minutes_to_burn_calories", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_products_tax_group_id",
        "products",
        "tax_groups",
        ["tax_group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_products_tax_group_id", "products", ["tax_group_id"])

    # Seed the UAE VAT tax group and point every existing product at it, so the
    # POS charges the same 5% the storefront has been charging via Order.vat_rate.
    op.execute(
        """
        INSERT INTO taxes (id, name, name_localized, translations, rate, type,
                           is_active, created_at, updated_at)
        VALUES (gen_random_uuid(), 'VAT', 'ضريبة القيمة المضافة', '{}'::jsonb,
                0.0500, 'inclusive', TRUE, NOW(), NOW())
        """
    )
    op.execute(
        """
        INSERT INTO tax_groups (id, name, name_localized, translations, reference,
                                is_active, is_default, created_at, updated_at)
        VALUES (gen_random_uuid(), 'UAE VAT', 'ضريبة الإمارات', '{}'::jsonb, 'uae_vat',
                TRUE, TRUE, NOW(), NOW())
        """
    )
    op.execute(
        """
        INSERT INTO tax_group_taxes (id, tax_group_id, tax_id)
        SELECT gen_random_uuid(), g.id, t.id
        FROM tax_groups g
        CROSS JOIN taxes t
        WHERE g.reference = 'uae_vat' AND t.name = 'VAT'
        """
    )
    op.execute(
        """
        UPDATE products
        SET tax_group_id = (SELECT id FROM tax_groups WHERE reference = 'uae_vat')
        WHERE tax_group_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM tax_group_taxes WHERE tax_group_id IN "
        "(SELECT id FROM tax_groups WHERE reference = 'uae_vat')"
    )
    op.execute("DELETE FROM tax_groups WHERE reference = 'uae_vat'")
    op.execute("DELETE FROM taxes WHERE name = 'VAT'")

    op.drop_index("ix_products_tax_group_id", table_name="products")
    op.drop_constraint("fk_products_tax_group_id", "products", type_="foreignkey")
    op.drop_column("products", "walking_minutes_to_burn_calories")
    op.drop_column("products", "is_non_revenue")
    op.drop_column("products", "costing_method")
    op.drop_column("products", "pricing_method")
    op.drop_column("products", "tax_group_id")
    op.drop_column("products", "name_localized")
