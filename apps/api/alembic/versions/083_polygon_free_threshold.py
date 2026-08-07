"""Per-polygon free-delivery threshold.

One national threshold only works while every zone costs about the same to
reach. Ours do not, and the gap is not marginal: a noon Send bike run inside
Sharjah costs AED 13.60 against a Lalamove car to Dubai Investment Park at
AED 59. A single number was therefore withholding an offer that is cheap to
honour in Sharjah while funding one that is not out at the edge — both at once.

The column is nullable and NULL means "use `delivery_settings`". That is exactly
what every zone meant before this existed, so this migration changes no
behaviour on its own; the new map that follows it sets real values. Zero is a
distinct, legitimate value meaning "free at any basket" — which is why the
service tests `IS NULL` rather than falsiness.

Revision ID: 083_polygon_free_threshold
Revises: 082_search_trigram_indexes
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "083_polygon_free_threshold"
down_revision: Union[str, None] = "082_search_trigram_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "delivery_polygons",
        sa.Column("free_delivery_threshold", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("delivery_polygons", "free_delivery_threshold")
