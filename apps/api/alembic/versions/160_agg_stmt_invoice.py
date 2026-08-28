"""Persist statement invoice documents for VAT claims.

Adds object-storage pointers on `aggregator_statement` so finance can download
the marketplace's settlement invoice (PDF/CSV/XLSX/ZIP) from our private archive
instead of the portal. Bytes live under R2 prefix `aggregator-statements/…`
(see `statement_docs.py`); this migration only adds the DB columns.

Revision ID: 160_agg_stmt_invoice
Revises: 159_agg_depth_enrich
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "160_agg_stmt_invoice"
down_revision: Union[str, None] = "159_agg_depth_enrich"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_statement",
        sa.Column("invoice_object_key", sa.String(512), nullable=True),
    )
    op.add_column(
        "aggregator_statement",
        sa.Column("invoice_content_type", sa.String(120), nullable=True),
    )
    op.add_column(
        "aggregator_statement",
        sa.Column("invoice_original_filename", sa.String(255), nullable=True),
    )
    op.add_column(
        "aggregator_statement",
        sa.Column("invoice_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "aggregator_statement",
        sa.Column(
            "invoice_attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("aggregator_statement", "invoice_attachments")
    op.drop_column("aggregator_statement", "invoice_fetched_at")
    op.drop_column("aggregator_statement", "invoice_original_filename")
    op.drop_column("aggregator_statement", "invoice_content_type")
    op.drop_column("aggregator_statement", "invoice_object_key")
