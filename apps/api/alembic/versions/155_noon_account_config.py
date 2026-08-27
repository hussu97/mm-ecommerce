"""Seed Noon aggregator account extras (RMS brand/project scope).

The noon httpx provider reads `restaurant_code`, `project`, and `locale` from
the captured session. Stashing the brand-level ids on `aggregator_account.extras`
documents the expected RMS scope for operators and survives session loss until
warm/login re-captures cookies. Guarded: only fills missing keys so an admin
edit is never overwritten.

Revision ID: 155_noon_account_config
Revises: 154_agg_account
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "155_noon_account_config"
down_revision: Union[str, None] = "154_agg_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same brand/company ids as migration 152 branch-map seed.
_NOON_EXTRAS = {
    "restaurant_code": "R5967280642376629909871448A",
    "project": "PRJ135208",
    "locale": "en-ae",
}


def upgrade() -> None:
    conn = op.get_bind()
    for key, value in _NOON_EXTRAS.items():
        conn.execute(
            text(
                """
                UPDATE aggregator_account
                SET extras = COALESCE(extras, '{}'::jsonb)
                    || jsonb_build_object(:key, :value),
                    updated_at = now()
                WHERE channel = 'noon'
                  AND account_ref = ''
                  AND (
                    extras IS NULL
                    OR extras->>:key IS NULL
                    OR extras->>:key = ''
                  )
                """
            ),
            {"key": key, "value": value},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for key in _NOON_EXTRAS:
        conn.execute(
            text(
                """
                UPDATE aggregator_account
                SET extras = extras - :key,
                    updated_at = now()
                WHERE channel = 'noon'
                  AND account_ref = ''
                  AND extras ? :key
                """
            ),
            {"key": key},
        )
