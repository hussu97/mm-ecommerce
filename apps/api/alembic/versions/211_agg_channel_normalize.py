"""Canonicalise `orders.aggregator_channel` to the courier-catalog code (F-AGG-9).

Migration 183 widened the aggregator idempotency key to
`(source, aggregator_channel, external_reference)`. The two order writers then
spelled the channel differently — the promotion path stored a display label
("Noon Food", "Careem"), the GrubOps ingest stored GrubTech's raw
`foodAggregatorName` ("Noon", "Careem Now") — so one sale could satisfy the key
twice and file two MM orders (prod: 3 Deliveroo pairs). The paired code change
makes BOTH writers store `courier_catalog.code_for_channel(raw) or raw`, i.e. the
canonical code ("deliveroo", "noon_food", "careem", "talabat", "keeta"). This
migration folds the rows already in the table onto that same code so the widened
key holds one spelling everywhere, matching what the code now writes.

Collision-safe: a row is canonicalised ONLY when no OTHER aggregator order already
holds the target code with the same (non-null) `external_reference` — otherwise the
fold would violate `uq_orders_source_channel_external_reference`. The rows it skips
are the genuine duplicate PAIRS that F-AGG-9 is about; they are cleared by F-AGG-4's
cancellations (an ops step, run before the operational backfill), not here. The
count left non-canonical is logged so that backlog is visible.

Idempotent: it touches only aggregator rows whose channel is not already one of the
five codes, so a re-run — or a run over a restored dump — changes nothing new.

Downgrade is a no-op: the canonical code is a strictly better, self-consistent
state, the pre-fold capitalisation/aliases are lossy to restore (careem vs
"Careem Now"), and un-folding could itself collide — so, like the 207 email fold,
it does not pretend to reverse.

Revision ID: 211_agg_channel_normalize
Revises: 209_auth_session_revocation
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "211_agg_channel_normalize"
down_revision: Union[str, None] = "210_fk_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The five canonical codes (courier_catalog.AGGREGATOR_CODES). A row already on one
#: of these is left untouched — the WHERE both keeps the fold idempotent and skips
#: anything unrecognised (stored verbatim, matching the writers' `or raw`).
_CODES = ("talabat", "keeta", "noon_food", "deliveroo", "careem")

#: lower(trim(raw)) → code, mirroring courier_catalog._CHANNEL_ALIASES for the
#: spellings that reach `orders.aggregator_channel` in production.
_MAP_SQL = """
    CASE lower(btrim(aggregator_channel))
        WHEN 'talabat'     THEN 'talabat'
        WHEN 'keeta'       THEN 'keeta'
        WHEN 'keeta 2.0'   THEN 'keeta'
        WHEN 'keeta2.0'    THEN 'keeta'
        WHEN 'noon'        THEN 'noon_food'
        WHEN 'noon food'   THEN 'noon_food'
        WHEN 'noonfood'    THEN 'noon_food'
        WHEN 'deliveroo'   THEN 'deliveroo'
        WHEN 'careem'      THEN 'careem'
        WHEN 'careem now'  THEN 'careem'
        WHEN 'careemnow'   THEN 'careem'
        ELSE aggregator_channel
    END
"""


def upgrade() -> None:
    result = op.execute(
        f"""
        UPDATE orders o
        SET aggregator_channel = c.code
        FROM (
            SELECT id, ({_MAP_SQL}) AS code
            FROM orders
            WHERE source = 'aggregator'
              AND aggregator_channel IS NOT NULL
              AND aggregator_channel NOT IN {_CODES!r}
        ) c
        WHERE o.id = c.id
          AND c.code <> o.aggregator_channel
          AND NOT (
              o.external_reference IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM orders x
                  WHERE x.source = 'aggregator'
                    AND x.aggregator_channel = c.code
                    AND x.external_reference = o.external_reference
                    AND x.id <> o.id
              )
          )
        """
    )
    rowcount = getattr(result, "rowcount", None)
    if rowcount is not None:
        print(f"211: canonicalised {rowcount} aggregator order channel(s)")

    # Anything still non-canonical is either a genuine duplicate pair the fold had
    # to skip (F-AGG-4 clears these) or an unrecognised channel left verbatim.
    left = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
            SELECT count(*) FROM orders
            WHERE source = 'aggregator'
              AND aggregator_channel IS NOT NULL
              AND aggregator_channel NOT IN {_CODES!r}
            """
            )
        )
        .scalar()
    )
    if left:
        print(
            f"211: {left} aggregator order(s) left non-canonical "
            "(duplicate pairs awaiting F-AGG-4 cancellation, or unknown channels)"
        )


def downgrade() -> None:
    # The canonical code is a strictly better, self-consistent state; the pre-fold
    # spelling is not recorded and is lossy to guess. Like the 207 email fold, this
    # is one-way and does not pretend to reverse.
    pass
