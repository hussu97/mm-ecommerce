"""Replace /brownies with /all-products in CMS page content

Revision ID: 015_fix_brownies_slug
Revises: 014_email_logs
Create Date: 2026-03-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "015_fix_brownies_slug"
down_revision: Union[str, None] = "014_email_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Replace the URL slug in all CMS JSONB content in one pass.
    # The JSON string value "/brownies" (always double-quoted in JSON text)
    # is safely distinct from the word "brownies" in prose.
    op.execute(
        """
        UPDATE cms_pages
        SET content = replace(content::text, '"/brownies"', '"/all-products"')::jsonb
        WHERE content::text LIKE '%"/brownies"%'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE cms_pages
        SET content = replace(content::text, '"/all-products"', '"/brownies"')::jsonb
        WHERE content::text LIKE '%"/all-products"%'
        """
    )
