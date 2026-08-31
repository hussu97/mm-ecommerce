"""Resolve the aggregator item-map name-variants the fuzzy matcher left unmapped.

The catalog sync resolves an aggregator item to an MM product through the
*approved* rows of `external_item_map` (the same map the order-promotion resolver
uses). A cross-check of production found the map already carries the ingest's
proposals — ~78 product rows matched by exact name, plus a handful the matcher
could not place because the aggregator names a size differently than MM does:

  careem    "nutella cookie melt (serves 3-5)"  -> Nutella Cookie Melt (500 grams)
  deliveroo "kinder cookie melt (serves 3-5)"   -> Kinder Cookie Melt (500 grams)
  talabat   "fudge brownies [1 3 pieces]"       -> Fudge Brownies

"(serves 3-5)" is MM's 500 g product and "(serves 1-2)" the 250 g one (MM's own
slugs: `kinder-cookie-melt-serves-3-5` = the 500 g row); the "[1 3 pieces]" suffix
is Talabat's variant label on the base Fudge Brownies. Those three are
unambiguous, so this seeds their `product_id`.

**Deliberately NOT touched:** the three size-less Talabat cookie melts
("nutella/kinder/brookie cookie melt") are ambiguous between the 250 g and 500 g
products and are left for a human to place in the item-mappings console; and this
migration does **not** approve anything — approval stays the human gate the resolver
requires. Guarded so it cannot fight the admin: it only fills a row whose
`product_id` is still NULL (matched by the exact scraped `external_ref`), so once an
operator maps or edits one, a re-run matches nothing. Marks the resolution `manual`,
like an operator edit.

Revision ID: 172_agg_item_map_seed
Revises: 171_catalog_sync_scaffold
Create Date: 2026-09-01
"""

from __future__ import annotations

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "172_agg_item_map_seed"
down_revision: Union[str, None] = "171_catalog_sync_scaffold"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (system, scraped external_ref, MM product name) — each unambiguous.
_RESOLVE = [
    ("careem", "nutella cookie melt (serves 3-5)", "Nutella Cookie Melt (500 grams)"),
    ("deliveroo", "kinder cookie melt (serves 3-5)", "Kinder Cookie Melt (500 grams)"),
    ("talabat", "fudge brownies [1 3 pieces]", "Fudge Brownies"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for system, external_ref, product_name in _RESOLVE:
        conn.execute(
            text(
                """
                UPDATE external_item_map
                   SET product_id = (
                           SELECT id FROM products WHERE name = :product_name LIMIT 1
                       ),
                       match_method = 'manual'
                 WHERE system = :system
                   AND external_ref = :external_ref
                   AND mm_kind = 'product'
                   AND product_id IS NULL
                   AND EXISTS (SELECT 1 FROM products WHERE name = :product_name)
                """
            ),
            {
                "system": system,
                "external_ref": external_ref,
                "product_name": product_name,
            },
        )


def downgrade() -> None:
    # Undo only the rows this seed filled (still unapproved, marked manual by us).
    conn = op.get_bind()
    for system, external_ref, _ in _RESOLVE:
        conn.execute(
            text(
                """
                UPDATE external_item_map
                   SET product_id = NULL
                 WHERE system = :system
                   AND external_ref = :external_ref
                   AND mm_kind = 'product'
                   AND approved IS FALSE
                """
            ),
            {"system": system, "external_ref": external_ref},
        )
