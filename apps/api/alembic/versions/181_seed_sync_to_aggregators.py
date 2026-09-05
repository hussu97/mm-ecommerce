"""Seed `products.sync_to_aggregators` for the products that are live on the marketplaces.

`catalog_sync.build_mm_menu` selects `Product.sync_to_aggregators == True` as the
desired (MM) menu. Migration 171 added the column defaulting **false** and never
seeded it, so live production has zero synced products — meaning the desired menu is
empty and the whole sync would read as "delete everything". This is the initial
load.

The set is "what is actually live on the marketplaces today" (audited 2026-09-05),
so the first diff against each portal is near-empty — the safe way to switch the
feature on:
- the 46 SKUs in the Foodics **Grubtech price tag** (the aggregator menu that
  cascades to the two integrated branches, Barsha + Sharjah), read live from the
  console on 2026-09-05; plus
- **FG0052 (Brookies)**, which is carried on the portal-direct branches (Karama/DSO)
  but is *not* in the Grubtech tag — an audited coverage gap. Including it makes MM's
  desired set the union of what is live anywhere.

Guarded so it cannot fight the admin: it only flips a row that is still `false`
(the exact value it means to replace), matched by the account-stable `FG####` SKU.
Once a human toggles a product's sync flag in the console, that row is no longer
`false` and a re-run (or a restored dump) matches nothing for it. The `admin` PUT
`/products/{id}/sync` route owns the flag from here on.

Revision ID: 181_seed_sync_to_aggregators
Revises: 180_foodics_branch_map_seed
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "181_seed_sync_to_aggregators"
down_revision: Union[str, None] = "180_foodics_branch_map_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 46 Grubtech price-tag SKUs (live 2026-09-05) + FG0052 (Brookies, portal-direct
# only). Account-stable FG#### product SKUs; matched by value, not by name.
_SKUS: tuple[str, ...] = (
    "FG0018",
    "FG0019",
    "FG0020",
    "FG0021",
    "FG0022",
    "FG0023",
    "FG0024",
    "FG0025",
    "FG0026",
    "FG0027",
    "FG0028",
    "FG0029",
    "FG0030",
    "FG0031",
    "FG0032",
    "FG0033",
    "FG0034",
    "FG0035",
    "FG0036",
    "FG0037",
    "FG0038",
    "FG0039",
    "FG0040",
    "FG0041",
    "FG0042",
    "FG0043",
    "FG0044",
    "FG0045",
    "FG0046",
    "FG0047",
    "FG0048",
    "FG0049",
    "FG0051",
    "FG0052",
    "FG0053",
    "FG0054",
    "FG0055",
    "FG0056",
    "FG0057",
    "FG0118",
    "FG0125",
    "FG0126",
    "FG0127",
    "FG0128",
    "FG0129",
    "FG0130",
    "FG0131",
)

# Comma-joined + parsed with string_to_array so the bind is one text value and the
# array type is created in-SQL (no asyncpg array-param typing ambiguity — 152's lesson).
_SKUS_CSV = ",".join(_SKUS)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE products
               SET sync_to_aggregators = true
             WHERE sync_to_aggregators = false
               AND sku = ANY(string_to_array(CAST(:skus AS text), ','))
            """
        ),
        {"skus": _SKUS_CSV},
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Revert the seeded set to off (its pre-seed state). A row a human has since
    # opted out of is already false and unaffected.
    conn.execute(
        text(
            """
            UPDATE products
               SET sync_to_aggregators = false
             WHERE sku = ANY(string_to_array(CAST(:skus AS text), ','))
            """
        ),
        {"skus": _SKUS_CSV},
    )
