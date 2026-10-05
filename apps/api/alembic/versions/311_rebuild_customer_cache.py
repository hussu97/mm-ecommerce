"""Rebuild the customer directory once, without Keeta's masked contacts.

Keeta sent ``***`` for the customer name and ``52*****98`` for the number on
1,675 orders from July to early September 2026. The cache read those as real
identities: they became 534 ``***`` "customers", each merging unrelated people
whose four visible digits matched, in the admin customer list and the
delivery-area map. `customer_service` now reads a masked value as absent.

Marking the cache dirty makes the next directory read rebuild every row (the
customers, their order membership and the delivery-area points) under that
rule, rather than waiting for the next order to dirty it. Nothing here edits a
source record; the downgrade has nothing to undo.

Revision ID: 311_rebuild_customer_cache
Revises: 310_po_invoice_date
Create Date: 2026-10-05
"""

from typing import Sequence, Union

from alembic import op

revision: str = "311_rebuild_customer_cache"
down_revision: Union[str, None] = "310_po_invoice_date"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE customer_cache_state SET dirty = true WHERE id IS TRUE")


def downgrade() -> None:
    pass
