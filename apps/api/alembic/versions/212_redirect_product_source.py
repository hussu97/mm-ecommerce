"""Allow 'product_rename' as a url_redirects source (F-INV-12).

A product slug change now records a redirect the same way a category rename does,
but tagged `product_rename` so an operator can tell a product-leaf redirect from
a category prefix. The source CHECK constraint enumerated only the old vocabulary
('manual', 'category_rename', 'seed'), so widen it to include the new value.

Revision ID: 212_redirect_product_source
Revises: 209_auth_session_revocation
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op

revision: str = "212_redirect_product_source"
down_revision: Union[str, None] = "209_auth_session_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_url_redirects_source_allowed", "url_redirects", type_="check"
    )
    op.create_check_constraint(
        "ck_url_redirects_source_allowed",
        "url_redirects",
        "source IN ('manual', 'category_rename', 'product_rename', 'seed')",
    )


def downgrade() -> None:
    # Retire any rows written under the new source before narrowing the vocabulary
    # again, so the tighter constraint can be re-applied.
    op.execute("DELETE FROM url_redirects WHERE source = 'product_rename'")
    op.drop_constraint(
        "ck_url_redirects_source_allowed", "url_redirects", type_="check"
    )
    op.create_check_constraint(
        "ck_url_redirects_source_allowed",
        "url_redirects",
        "source IN ('manual', 'category_rename', 'seed')",
    )
