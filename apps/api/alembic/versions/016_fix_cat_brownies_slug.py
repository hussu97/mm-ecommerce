"""Replace /cat-brownies with /all-products in CMS page content

Revision ID: 016_fix_cat_brownies_slug
Revises: 015_fix_brownies_slug
Create Date: 2026-03-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "016_fix_cat_brownies_slug"
down_revision: Union[str, None] = "015_fix_brownies_slug"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE cms_pages
        SET content = replace(content::text, '"/cat-brownies"', '"/all-products"')::jsonb
        WHERE content::text LIKE '%"/cat-brownies"%'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE cms_pages
        SET content = replace(content::text, '"/all-products"', '"/cat-brownies"')::jsonb
        WHERE content::text LIKE '%"/all-products"%'
        """
    )
