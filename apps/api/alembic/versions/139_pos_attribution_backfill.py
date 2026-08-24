"""Backfill the cashier, terminal and close-time an aggregator or website order never carried.

A counter check records who closed it, on which device, and when. Aggregator and
website orders reach a completed state (`pos_status = closed`, or a website order
`delivered`) with `closer_id`, `device_id` and `closed_at` all NULL — nobody rings
them up — so the per-cashier, per-terminal and per-hour reports bucketed every one
as "Unknown". The reports now resolve this at query time (see
`pos_reports._covering_till` and `_COMPLETED_SALE`), so this is not required for the
screens to read correctly; it materialises the same answer onto the rows so other
consumers and exports see it too, and so a historical figure does not depend on a
till record surviving.

Best-effort and one-time, exactly as asked: "who was online at the POS" is read off
the till that was open at the order's branch across the moment it arrived, and where
no till covered it, the nearest shift at that branch stands in. `closed_at` falls
back to when the row was last written. Approximate on purpose — a two-month-old
aggregator order does not have an exact cashier to recover, and a good guess beats a
blank column.

Guarded three ways so a re-run is a no-op and the live register is never touched:
only `is_pos` orders, only ones already in a completed state, and only columns that
are still NULL. An *active* check has no cashier or close time yet and must keep its
NULLs — which is also why these columns stay nullable: the value does not exist until
the sale is finished, and for an online order it may never exist at all.

Revision ID: 139_pos_attr_backfill
Revises: 138_status_vocab_remainder
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "139_pos_attr_backfill"
down_revision: Union[str, None] = "138_status_vocab_remainder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# A completed POS sale: a closed till check, or a delivered website order. The
# same predicate the sales reports count on, kept in one string so the backfill
# and the reports cannot drift apart on what "done" means.
_COMPLETED = (
    "o.is_pos = true AND ("
    "o.pos_status = 'closed' "
    "OR (o.source = 'online' AND o.status = 'delivered'))"
)


def upgrade() -> None:
    # 1. Close-time first, so the till correlation below can key off it. When an
    #    order finished was not recorded, when the row was last written is the
    #    closest thing we have.
    op.execute(
        f"""
        UPDATE orders o
        SET closed_at = COALESCE(o.updated_at, o.created_at)
        WHERE {_COMPLETED}
          AND o.closed_at IS NULL
        """
    )

    # 2. Cashier and terminal from the till open across the order's moment — the
    #    literal "who was online at the POS". Most recently opened till wins when
    #    a branch was trading on more than one device.
    #    Correlated scalar subqueries rather than a LATERAL join: Postgres will
    #    not let a FROM-clause LATERAL see the UPDATE's own target row, but a
    #    subquery in SET can. Both subqueries share one WHERE, so they resolve to
    #    the same till.
    op.execute(
        f"""
        UPDATE orders o
        SET device_id = COALESCE(o.device_id, (
                SELECT t.device_id FROM tills t
                WHERE t.branch_id = o.branch_id
                  AND t.opened_at <= COALESCE(o.closed_at, o.created_at)
                  AND (t.closed_at IS NULL
                       OR t.closed_at >= COALESCE(o.closed_at, o.created_at))
                ORDER BY t.opened_at DESC
                LIMIT 1
            )),
            closer_id = COALESCE(o.closer_id, (
                SELECT t.user_id FROM tills t
                WHERE t.branch_id = o.branch_id
                  AND t.opened_at <= COALESCE(o.closed_at, o.created_at)
                  AND (t.closed_at IS NULL
                       OR t.closed_at >= COALESCE(o.closed_at, o.created_at))
                ORDER BY t.opened_at DESC
                LIMIT 1
            ))
        WHERE {_COMPLETED}
          AND (o.device_id IS NULL OR o.closer_id IS NULL)
        """
    )

    # 3. The default, for orders that came in while no till was open — a
    #    marketplace order at 3am, delivered next morning. The nearest shift at
    #    that branch is the honest approximation of who would have handled it.
    op.execute(
        f"""
        UPDATE orders o
        SET device_id = COALESCE(o.device_id, (
                SELECT t.device_id FROM tills t
                WHERE t.branch_id = o.branch_id
                ORDER BY ABS(EXTRACT(EPOCH FROM (
                    t.opened_at - COALESCE(o.closed_at, o.created_at)
                ))) ASC
                LIMIT 1
            )),
            closer_id = COALESCE(o.closer_id, (
                SELECT t.user_id FROM tills t
                WHERE t.branch_id = o.branch_id
                ORDER BY ABS(EXTRACT(EPOCH FROM (
                    t.opened_at - COALESCE(o.closed_at, o.created_at)
                ))) ASC
                LIMIT 1
            ))
        WHERE {_COMPLETED}
          AND (o.device_id IS NULL OR o.closer_id IS NULL)
        """
    )


def downgrade() -> None:
    # A best-effort backfill cannot be reversed: the rows it filled and the rows
    # that were always going to be NULL are indistinguishable afterwards, so
    # blanking them again would lose real counter attributions too. The columns
    # stay nullable, so there is nothing structural to undo.
    pass
