"""Seed Noon aggregator account extras (RMS brand/project scope).

The noon httpx provider reads `restaurant_code`, `project`, and `locale` from
the captured session. Stashing the brand-level ids on `aggregator_account.extras`
documents the expected RMS scope for operators and survives session loss until
warm/login re-captures cookies.

Brand and company come from `aggregator_branch_map` (migration 152), not
re-stated here — one source of truth for Noon RMS scope. Guarded: only fills
missing keys so an admin edit is never overwritten.

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

# Locale is not stored on branch maps; noon RMS defaults to en-ae.
_DEFAULT_LOCALE = "en-ae"

_NOON_SCOPE_FROM_MAP = """
    SELECT DISTINCT external_brand_id, external_company_id
    FROM aggregator_branch_map
    WHERE channel = 'noon'
      AND is_active = true
      AND external_brand_id IS NOT NULL
      AND external_brand_id <> ''
      AND external_company_id IS NOT NULL
      AND external_company_id <> ''
    LIMIT 1
"""


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE aggregator_account AS aa
            SET extras = COALESCE(aa.extras, '{}'::jsonb)
                || CASE
                    WHEN aa.extras->>'restaurant_code' IS NULL
                      OR aa.extras->>'restaurant_code' = ''
                    THEN jsonb_build_object('restaurant_code', m.external_brand_id)
                    ELSE '{}'::jsonb
                   END
                || CASE
                    WHEN aa.extras->>'project' IS NULL
                      OR aa.extras->>'project' = ''
                    THEN jsonb_build_object('project', m.external_company_id)
                    ELSE '{}'::jsonb
                   END
                || CASE
                    WHEN aa.extras->>'locale' IS NULL
                      OR aa.extras->>'locale' = ''
                    THEN jsonb_build_object('locale', '"""
            + _DEFAULT_LOCALE
            + """')
                    ELSE '{}'::jsonb
                   END,
                updated_at = now()
            FROM (
                """
            + _NOON_SCOPE_FROM_MAP
            + """
            ) AS m
            WHERE aa.channel = 'noon'
              AND aa.account_ref = ''
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            f"""
            UPDATE aggregator_account AS aa
            SET extras = aa.extras - 'restaurant_code',
                updated_at = now()
            FROM ({_NOON_SCOPE_FROM_MAP}) AS m
            WHERE aa.channel = 'noon'
              AND aa.account_ref = ''
              AND aa.extras->>'restaurant_code' = m.external_brand_id
            """
        )
    )
    conn.execute(
        text(
            f"""
            UPDATE aggregator_account AS aa
            SET extras = aa.extras - 'project',
                updated_at = now()
            FROM ({_NOON_SCOPE_FROM_MAP}) AS m
            WHERE aa.channel = 'noon'
              AND aa.account_ref = ''
              AND aa.extras->>'project' = m.external_company_id
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE aggregator_account
            SET extras = extras - 'locale',
                updated_at = now()
            WHERE channel = 'noon'
              AND account_ref = ''
              AND extras->>'locale' = '"""
            + _DEFAULT_LOCALE
            + """'
            """
        )
    )
