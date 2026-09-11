"""Reviewer comments on shift inventory reports.

A new ``shift_inventory_report_comments`` table: one reviewer note per row, shown
back to the shop on the till. The report already had ``notes``/``deferred_reason``
(the shop's and the rejection's single fields); this is the conversation on top.

``author_name`` is snapshotted at write time so a note keeps its attribution after
the user row is removed — the FK then goes null (SET NULL) but the name stays.

Revision ID: 221_report_comments
Revises: 220_ui_translation_hand_edited
Create Date: 2026-09-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "221_report_comments"
down_revision: Union[str, None] = "220_ui_translation_hand_edited"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shift_inventory_report_comments",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "report_id",
            UUID(as_uuid=True),
            sa.ForeignKey("shift_inventory_reports.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "author_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("shift_inventory_report_comments")
