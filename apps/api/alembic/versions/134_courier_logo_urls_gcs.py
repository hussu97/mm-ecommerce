"""Point courier logos at the GCS image bucket, where they actually live.

Migration 133 seeded `couriers.logo_url` with a Cloudflare R2 URL, on the
assumption the storefront's images sit in R2. They do not: production serves its
product images from the GCS bucket `mm-product-images`
(`storage.googleapis.com/mm-product-images/...`), and the R2 credentials on the
box are not even valid. The courier badges are therefore uploaded to
`mm-product-images/couriers/` (a subfolder, kept clear of `menu/`) and served
from the same public GCS host as every other product image.

Guarded so it cannot fight a hand-edit: only a row still carrying the old R2 URL
(or none at all) is moved. Once someone sets a logo by hand — or on a database
restored from an older dump where the value differs — this matches nothing and
does nothing.

Revision ID: 134_courier_logos_gcs
Revises: 133_courier_logos_agg
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "134_courier_logos_gcs"
down_revision: Union[str, None] = "133_courier_logos_agg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The public GCS home for the badges, where product images already live.
_GCS = "https://storage.googleapis.com/mm-product-images/couriers"

#: What 133 seeded — the URL we are correcting away from.
_OLD_PREFIX = "https://media.meltingmomentscakes.com/couriers/"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE couriers "
            f"SET logo_url = '{_GCS}/' || code || '.png' "
            "WHERE logo_url IS NULL "
            f"OR logo_url LIKE '{_OLD_PREFIX}%'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE couriers "
            f"SET logo_url = '{_OLD_PREFIX}' || code || '.png' "
            f"WHERE logo_url LIKE '{_GCS}/%'"
        )
    )
