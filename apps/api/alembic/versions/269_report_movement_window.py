"""Sequence-tiled shift-report movement windows.

A shift/raw-materials report froze its movement window at ``base_posting_sequence``
(captured when the report was OPENED) with no lower sequence bound, and
``post_report`` never recomputed the columns. Any movement that posted between the
snapshot and the report's posting — most often during the submit→approval delay —
was counted in no report at all, while the physical count reflected it, producing
a phantom "Difference" and an inflated Opening.

The window is now ``(previous same-scope report's posting_cutoff_sequence, this
report's posting_cutoff_sequence]``: every closed movement lands in exactly one
report and a report's own reconciliation postings (above its cutoff) land in none.
``post_report`` recomputes the columns through the pre-post high-water and stamps
the cutoff plus each line's closing on-hand.

This migration adds the two carrier columns and backfills already-posted reports
so the tiling is continuous across the cutover.

Revision ID: 269_report_movement_window
Revises: 268_agg_order_display_ref_idx
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "269_report_movement_window"
down_revision: Union[str, None] = "268_agg_order_display_ref_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shift_inventory_reports",
        sa.Column("posting_cutoff_sequence", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "shift_inventory_report_lines",
        sa.Column("closing_quantity", sa.Numeric(20, 6), nullable=True),
    )

    # Backfill the cutoff for already-posted reports: the high-water among the
    # report's OWN postings (its count/movement transactions), which is exactly the
    # boundary the next same-scope report should open after. Guarded to posted rows
    # with a null cutoff, so a re-run — or a restore from an older dump — is a
    # no-op and never fights a value a later post has already written.
    op.execute(
        """
        UPDATE shift_inventory_reports AS r
        SET posting_cutoff_sequence = sub.max_seq
        FROM (
            SELECT t.source_id AS report_id, MAX(t.posting_sequence) AS max_seq
            FROM inventory_transactions AS t
            WHERE t.source_type = 'shift_inventory_report'
              AND t.posting_sequence IS NOT NULL
            GROUP BY t.source_id
        ) AS sub
        WHERE r.id = sub.report_id::uuid
          AND r.status = 'posted'
          AND r.posting_cutoff_sequence IS NULL
        """
    )

    # Backfill each posted line's closing from its recorded system closing
    # (``expected_quantity``) — the best historical value; Opening is derived, so
    # this is an audit/record field rather than a driver of future arithmetic.
    op.execute(
        """
        UPDATE shift_inventory_report_lines AS l
        SET closing_quantity = l.expected_quantity
        FROM shift_inventory_reports AS r
        WHERE l.report_id = r.id
          AND r.status = 'posted'
          AND l.closing_quantity IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("shift_inventory_report_lines", "closing_quantity")
    op.drop_column("shift_inventory_reports", "posting_cutoff_sequence")
