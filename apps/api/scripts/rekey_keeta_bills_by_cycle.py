"""Re-key stored Keeta billing-report data onto one statement per settlement cycle.

Until 2026-09-29 every Keeta download was its own statement (`KEETA_BILL_{shop}_
{downloadStart}_{downloadEnd}`) with its own copy of each order's lines, and the
downloads overlap: one shop held 22–31 Jul, 23–31 Jul and 23 Jul–1 Aug, and Keeta
lists the same export under several task ids. Summed, the lines overstated what
Keeta paid by up to 3x; one copy per cycle matches the Invoice Details transfer
to the fils in every cycle checked (31 of 31). The parser now keys each row on
its settlement cycle (`keeta_provider._parse_bill_xlsx`); this moves the data
already stored onto the same keys.

For every stored `KEETA_BILL_*` line: its cycle comes from its transaction date
(1–7, 8–14, 15–21, 22–end of month — `keeta_provider._keeta_cycle`, which matched
every row of 49 real bills). One line is kept per `(cycle, order, category)` —
the copies are identical — and moved onto the cycle statement. Cycle statements
are created with the days their old downloads covered; order and payout links are
re-pointed; the old download statements are deleted (including one with no lines
at all, a PDF statement of account that read as "May–Jul billed"). Header totals
and the vendor-fault refund roll-up are then re-derived from the de-duplicated
lines.

Idempotent: a second run finds every line already on its cycle key and changes
nothing. Previews in a rolled-back transaction unless `--apply`.

    docker exec -w /app -e PYTHONPATH=/app <api> python scripts/rekey_keeta_bills_by_cycle.py [--apply]
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from sqlalchemy import text

from app.core.database import AsyncSessionFactory
from app.services.aggregators import ingest

APPLY = "--apply" in sys.argv

# Every stored Keeta bill line, with the cycle its transaction day falls in.
_MAP = """
CREATE TEMP TABLE keeta_rekey ON COMMIT DROP AS
WITH l AS (
  SELECT id, statement_id AS old_sid, split_part(statement_id, '_', 3) AS shop,
         line_date::date AS d, external_order_id, fee_category, updated_at
  FROM aggregator_statement_line
  WHERE channel = 'keeta' AND statement_id LIKE 'KEETA_BILL\\_%'
    AND line_date ~ '^\\d{4}-\\d{2}-\\d{2}$' AND external_order_id IS NOT NULL
), c AS (
  SELECT *, (date_trunc('month', d)::date
             + CASE WHEN extract(day FROM d) <= 7 THEN 0
                    WHEN extract(day FROM d) <= 14 THEN 7
                    WHEN extract(day FROM d) <= 21 THEN 14 ELSE 21 END) AS cs
  FROM l
)
SELECT *,
  CASE WHEN extract(day FROM cs) = 22
       THEN (date_trunc('month', cs) + interval '1 month - 1 day')::date
       ELSE cs + 6 END AS ce
FROM c
"""

_KEYS = """
ALTER TABLE keeta_rekey ADD COLUMN new_sid text, ADD COLUMN new_key text, ADD COLUMN keep boolean;
UPDATE keeta_rekey SET new_sid = 'KEETA_BILL_' || shop || '_' || cs || '_' || ce;
UPDATE keeta_rekey SET new_key = new_sid || ':' || external_order_id || ':' || fee_category;
UPDATE keeta_rekey k SET keep = r.rn = 1
FROM (SELECT id, row_number() OVER (PARTITION BY new_key ORDER BY updated_at DESC, id) AS rn
      FROM keeta_rekey) r
WHERE r.id = k.id
"""

_SUMMARY = """
SELECT count(*) AS lines, count(*) FILTER (WHERE NOT keep) AS duplicates,
       count(*) FILTER (WHERE keep AND old_sid <> new_sid) AS moved,
       count(DISTINCT new_sid) AS cycles, count(DISTINCT old_sid) AS old_statements
FROM keeta_rekey
"""

_STEPS = [
    # Drop the copies, then move the survivors onto their cycle key.
    "DELETE FROM aggregator_statement_line l USING keeta_rekey k WHERE l.id = k.id AND NOT k.keep",
    """UPDATE aggregator_statement_line l SET statement_id = k.new_sid, source_key = k.new_key,
              updated_at = now()
       FROM keeta_rekey k WHERE l.id = k.id AND k.keep
         AND (l.statement_id <> k.new_sid OR l.source_key <> k.new_key)""",
    # One statement per cycle, its period the days its downloads covered.
    """INSERT INTO aggregator_statement (channel, statement_id, external_outlet_id, period_start,
                                        period_end, currency, raw)
       SELECT 'keeta', k.new_sid, k.shop,
              min(greatest(s.period_start::date, k.cs))::text,
              max(least(s.period_end::date, k.ce))::text, 'AED',
              jsonb_build_object('rekeyed_from', to_jsonb(array_agg(DISTINCT k.old_sid)))
       FROM keeta_rekey k JOIN aggregator_statement s
         ON s.channel = 'keeta' AND s.statement_id = k.old_sid
       GROUP BY k.new_sid, k.shop
       ON CONFLICT ON CONSTRAINT uq_aggregator_statement DO UPDATE SET
         period_start = least(aggregator_statement.period_start, excluded.period_start),
         period_end = greatest(aggregator_statement.period_end, excluded.period_end),
         external_outlet_id = coalesce(aggregator_statement.external_outlet_id,
                                       excluded.external_outlet_id),
         updated_at = now()""",
    # Each order onto the cycle of its earliest line.
    """UPDATE aggregator_order a SET statement_id = x.new_sid
       FROM (SELECT DISTINCT ON (external_order_id) external_order_id, new_sid
             FROM keeta_rekey WHERE keep ORDER BY external_order_id, d) x
       WHERE a.channel = 'keeta' AND a.external_order_id = x.external_order_id
         AND a.statement_id IS DISTINCT FROM x.new_sid""",
    # A cycle's payout is `KEETA_BILL_{shop}_{cycleEnd}`: couple both ways.
    """UPDATE aggregator_payout p SET statement_id = s.statement_id
       FROM aggregator_statement s
       WHERE p.channel = 'keeta' AND s.channel = 'keeta'
         AND s.statement_id = ANY (SELECT DISTINCT new_sid FROM keeta_rekey)
         AND p.transfer_id = 'KEETA_BILL_' || s.external_outlet_id || '_' || split_part(s.statement_id, '_', 5)
         AND p.statement_id IS DISTINCT FROM s.statement_id""",
    """UPDATE aggregator_statement s SET payout_transfer_id = p.transfer_id
       FROM aggregator_payout p
       WHERE s.channel = 'keeta' AND p.channel = 'keeta' AND p.statement_id = s.statement_id
         AND s.payout_transfer_id IS DISTINCT FROM p.transfer_id""",
    # The download statements are now empty (or always were): remove them.
    """DELETE FROM aggregator_statement s
       WHERE s.channel = 'keeta' AND s.statement_id LIKE 'KEETA_BILL\\_%'
         AND s.statement_id <> ALL (SELECT DISTINCT new_sid FROM keeta_rekey)
         AND NOT EXISTS (SELECT 1 FROM aggregator_statement_line l
                         WHERE l.channel = 'keeta' AND l.statement_id = s.statement_id)""",
    # Totals are re-derived from the de-duplicated lines below.
    """UPDATE aggregator_statement SET gross_sales = NULL, net_payable = NULL,
              total_fees = NULL, total_vat = NULL
       WHERE channel = 'keeta' AND statement_id = ANY (SELECT DISTINCT new_sid FROM keeta_rekey)""",
]


async def main() -> None:
    async with AsyncSessionFactory() as db:
        await db.execute(text("SET LOCAL lock_timeout = '5s'"))
        await db.execute(text(_MAP))
        for stmt in _KEYS.strip().split(";\n"):
            await db.execute(text(stmt))
        print(dict((await db.execute(text(_SUMMARY))).mappings().one()))
        for step in _STEPS:
            result = await db.execute(text(step))
            print(
                f"{result.rowcount:>6}  {step.split()[0]} {step.split()[1]} {step.split()[2]}"
            )
        cycles = [
            row[0]
            for row in await db.execute(
                text("SELECT DISTINCT new_sid FROM keeta_rekey ORDER BY 1")
            )
        ]
        for statement_id in cycles:
            await ingest._fill_statement_totals_from_lines(
                db,
                "keeta",
                SimpleNamespace(
                    statement_id=statement_id,
                    gross_sales=None,
                    net_payable=None,
                    total_fees=None,
                    total_vat=None,
                ),
            )
        orders = {
            row[0]
            for row in await db.execute(
                text("SELECT DISTINCT external_order_id FROM keeta_rekey WHERE keep")
            )
        }
        refunds = await ingest.apply_statement_refunds(db, "keeta", orders)
        print(
            f"{len(cycles)} cycle statements re-totalled; {refunds} refund roll-ups rewritten"
        )
        if APPLY:
            await db.commit()
            print("COMMITTED")
        else:
            await db.rollback()
            print("ROLLED BACK (preview — pass --apply)")


if __name__ == "__main__":
    asyncio.run(main())
