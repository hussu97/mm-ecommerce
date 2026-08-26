"""Seed the aggregator branch map (and the two aggregator-only branches) as data.

The outlet↔branch mapping is DB data, not a constant in the app: it changes when
an outlet is re-onboarded or a branch opens, and that should be a row edit (or an
admin action), never a code deploy. This migration is the initial load — the
values the deploy has to carry — and it is guarded so it cannot fight an operator:
branches are created only if absent (matched by name), and every mapping is
`ON CONFLICT DO NOTHING`, so once a human edits a row here or in the admin, a
re-run changes nothing.

Coverage is the operator's outlet table — Sharjah(Majaz)/Barsha/DSO/Karama across
Noon/Talabat/Careem/Deliveroo/Keeta, with the gaps real (Careem has no Sharjah,
and Careem/Deliveroo do not reach Karama).

Branch ids are resolved by a name/city/reference hint rather than hardcoded, so
this works whatever the local references are (the lesson migration 131 records).

Revision ID: 152_aggregator_branch_map_seed
Revises: 151_aggregator_ingestion
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "152_aggregator_branch_map_seed"
down_revision: Union[str, None] = "151_aggregator_ingestion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The two aggregator-only kitchens, created only if a branch of that name is not
# already present. Reference is a sensible default; the name is the guard.
_NEW_BRANCHES = [
    {"name": "Dubai Silicon Oasis", "reference": "DSO", "city": "Dubai"},
    {"name": "Al Karama", "reference": "KRM", "city": "Dubai"},
]

# outlet-key → the hint matched against a branch's name / city / reference.
_HINT = {
    "sharjah": "sharjah",
    "barsha_heights": "barsha",
    "dso": "silicon oasis",
    "karama": "karama",
}

# Noon's account-level ids, the same on every outlet: the RMS restaurant id is
# its brand, the project code its company.
_NOON_BRAND = "R5967280642376629909871448A"
_NOON_COMPANY = "PRJ135208"

# (channel, outlet_key, external_outlet_id, brand_id, company_id, is_active)
_MAPPINGS = [
    ("noon", "sharjah", "MLTNGM1GBF", _NOON_BRAND, _NOON_COMPANY, True),
    ("noon", "barsha_heights", "MLTNGM9FCH", _NOON_BRAND, _NOON_COMPANY, True),
    ("noon", "dso", "MLTNGMG2B1", _NOON_BRAND, _NOON_COMPANY, True),
    ("noon", "karama", "MLTNGMTB9M", _NOON_BRAND, _NOON_COMPANY, True),
    # Talabat's brand is the restaurant id; Karama is a separate Talabat account.
    ("talabat", "sharjah", "711571", "666733", None, True),
    ("talabat", "barsha_heights", "728173", "666733", None, True),
    ("talabat", "karama", "793319", "715778", None, True),
    ("careem", "barsha_heights", "1067984", "1029671", "1026653", True),
    ("careem", "dso", "1069463", "1029671", "1026653", True),
    ("deliveroo", "sharjah", "693360", None, None, True),
    ("deliveroo", "barsha_heights", "693359", None, None, True),
    ("deliveroo", "dso", "693361", None, None, True),
    ("keeta", "sharjah", "1644174206", None, None, True),
    ("keeta", "barsha_heights", "1644189187", None, None, True),
    ("keeta", "dso", "1644170195", None, None, True),
    ("keeta", "karama", "1644336388", None, None, True),
]


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Ensure the two aggregator-only branches exist (guarded by name).
    for b in _NEW_BRANCHES:
        conn.execute(
            text(
                """
                INSERT INTO branches (name, reference, city, type, timezone,
                    opening_from, opening_to)
                SELECT :name, :reference, :city, 'kitchen', 'Asia/Dubai',
                    '09:00', '23:00'
                WHERE NOT EXISTS (
                    SELECT 1 FROM branches WHERE lower(name) = lower(:name)
                )
                """
            ),
            b,
        )

    # 2. Insert the mappings, resolving the branch by hint. ON CONFLICT DO
    #    NOTHING so an operator's later edit is never overwritten by a re-run.
    for channel, outlet_key, outlet_id, brand_id, company_id, is_active in _MAPPINGS:
        hint = f"%{_HINT[outlet_key]}%"
        conn.execute(
            text(
                """
                INSERT INTO aggregator_branch_map (channel, branch_id,
                    external_outlet_id, external_brand_id, external_company_id,
                    is_active)
                SELECT :channel, b.id, :outlet_id, :brand_id, :company_id, :is_active
                FROM branches b
                WHERE (lower(b.name) LIKE :hint
                       OR lower(coalesce(b.city, '')) LIKE :hint
                       OR lower(b.reference) LIKE :hint)
                  AND b.deleted_at IS NULL
                ORDER BY b.display_order, b.reference
                LIMIT 1
                ON CONFLICT (channel, branch_id) DO NOTHING
                """
            ),
            {
                "channel": channel,
                "outlet_id": outlet_id,
                "brand_id": brand_id,
                "company_id": company_id,
                "is_active": is_active,
                "hint": hint,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    channels = tuple({m[0] for m in _MAPPINGS})
    conn.execute(
        text("DELETE FROM aggregator_branch_map WHERE channel = ANY(:channels)"),
        {"channels": list(channels)},
    )
    # Leave the branches: other data (orders, mappings a human added) may now
    # reference them, and dropping a branch is never a migration's call.
