"""Barsha's counter trades under a non-VAT-registered license.

The commercial fact the shop has agreed: Barsha's counter sales are made under
"Najm Al Shamal Coffee Shop", a trade license under the VAT threshold and so not
VAT-registered — while its website and aggregator sales stay under the
VAT-registered Melting Moments license. Migration `234` seeded every branch a
VAT-registered, identity-inheriting row per channel; this flips only Barsha's
counter row.

Guarded twice so it can never fight the admin console or a re-run: it matches a
Barsha branch by name (references differ per environment, names do not) and only
the still-default seeded row (VAT-registered, no name set). Once an operator
edits it, or on a second run, the guard matches nothing and this does nothing —
including on a database restored from an older dump. If no Barsha branch exists
(e.g. a environment that has not onboarded it) it is a no-op, and the config is
set from the admin console instead.

Revision ID: 236_barsha_counter_unregistered
Revises: 235_orders_tax_identity
Create Date: 2026-09-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "236_barsha_counter_unregistered"
down_revision: Union[str, None] = "235_orders_tax_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE branch_channel_tax_configs AS c
        SET vat_registered = false,
            tax_number = NULL,
            tax_registration_name = 'Najm Al Shamal Coffee Shop',
            invoice_title = 'Invoice',
            updated_at = now()
        FROM branches AS b
        WHERE c.branch_id = b.id
          AND b.name ILIKE '%barsha%'
          AND c.channel_class = 'counter'
          -- Only the untouched seeded default, so an admin edit is never fought.
          AND c.vat_registered = true
          AND c.tax_registration_name IS NULL
        """
    )


def downgrade() -> None:
    # Return Barsha's counter row to the inherited, VAT-registered default —
    # guarded to the exact values this migration set, so it never reverts an
    # admin edit made afterwards.
    op.execute(
        """
        UPDATE branch_channel_tax_configs AS c
        SET vat_registered = true,
            tax_number = NULL,
            tax_registration_name = NULL,
            invoice_title = NULL,
            updated_at = now()
        FROM branches AS b
        WHERE c.branch_id = b.id
          AND b.name ILIKE '%barsha%'
          AND c.channel_class = 'counter'
          AND c.vat_registered = false
          AND c.tax_registration_name = 'Najm Al Shamal Coffee Shop'
        """
    )
