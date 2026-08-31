"""Remove the static aggregator commission seed — fees now come only from scraping.

The one seeded static contract (`noon_food` 25% / 2%, from migration 137) is the
last static aggregator fee value in the database. Aggregator fees now come solely
from the marketplace's own scraped statement — no code reads the configured
courier rate any more — so this nulls that seed for data hygiene.

Guarded per the content-migration rule: it matches the EXACT seeded pair, so once
a human has edited the rate in the console (or the row was never seeded) it
matches nothing and does nothing — including on a database restored from an older
dump. The `couriers` commission columns themselves are left in place; retiring
those vestigial columns (and their admin surfaces) is a separate structural change.

Revision ID: 169_static_fee_removal
Revises: 168_agg_marketing_fee
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "169_static_fee_removal"
down_revision: Union[str, None] = "168_agg_marketing_fee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = NULL,
               payment_fee_percent = NULL
         WHERE code = 'noon_food'
           AND commission_percent = 25.00
           AND payment_fee_percent = 2.00
        """
    )


def downgrade() -> None:
    # Restore the historical seed, itself guarded so it cannot clobber a value a
    # human has since typed (mirrors migration 137's own guard).
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = 25.00,
               payment_fee_percent = 2.00
         WHERE code = 'noon_food'
           AND commission_percent IS NULL
           AND payment_fee_percent IS NULL
        """
    )
