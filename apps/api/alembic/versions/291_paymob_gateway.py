"""Paymob joins `payment_gateways` as the third card gateway — inactive.

The plumbing for Paymob is code (`paymob_provider`); the decision to send it a
single card is not, and this migration does not make it. The row ships the way
Ziina's did in `089`: present, **inactive**, and behind two more locks that
fail independently — `PAYMOB_ENABLED` defaults false, and the provider reports
itself unconfigured until every credential the money path needs is present.
Production stays on Stripe until people decide otherwise, in the admin, where
that decision can be unmade the same way.

Priority 3, behind Stripe (1) and Ziina (2), so activating it without touching
the priorities makes it a standby rather than the primary. The AED 2.00 floor
matches the other two, so an order is never routable to one card gateway and
refused by the next for being too small.

`fee_percent` / `fee_fixed` are left to their column defaults (2.9% + AED 1, from
`096`). They are an estimate the admin labels as one, and Paymob's real signed
pricing must be set on this row at go-live — it is on the PRODUCTION.md
checklist.

`ON CONFLICT (code) DO NOTHING`, so a row an operator already created (or a
database restored from a dump that has one) is left exactly as they set it.

Revision ID: 291_paymob_gateway
Revises: 290_promotion_usage_limit
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "291_paymob_gateway"
down_revision: Union[str, None] = "290_promotion_usage_limit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every bound parameter is cast explicitly. The deploy runs this under
    # asyncpg, which infers a type per parameter and refuses (or mis-binds) the
    # ambiguous ones — `089` hit exactly this with `min_amount` as a string.
    # `id`, `created_at` and `updated_at` have server defaults on this table
    # (`089`), and `supports_failover` defaults true.
    op.execute(
        sa.text(
            """
            INSERT INTO payment_gateways
                (code, name, is_active, priority, min_amount, test_mode)
            VALUES (
                CAST(:code AS VARCHAR),
                CAST(:name AS VARCHAR),
                CAST(:is_active AS BOOLEAN),
                CAST(:priority AS INTEGER),
                CAST(:min_amount AS NUMERIC),
                CAST(:test_mode AS BOOLEAN)
            )
            ON CONFLICT (code) DO NOTHING
            """
        ).bindparams(
            code="paymob",
            name="Paymob",
            is_active=False,
            priority=3,
            min_amount="2.00",
            test_mode=True,
        )
    )


def downgrade() -> None:
    # Only a row nothing has used. Once a single attempt has gone through
    # Paymob, its `payment_transactions` rows name this gateway, and deleting
    # the row would leave them pointing at a processor the admin no longer
    # knows about — so it stays, and the previous release simply never routes
    # to a code it does not recognise.
    op.execute(
        """
        DELETE FROM payment_gateways
         WHERE code = 'paymob'
           AND NOT EXISTS (
                 SELECT 1 FROM payment_transactions WHERE gateway = 'paymob'
               )
        """
    )
