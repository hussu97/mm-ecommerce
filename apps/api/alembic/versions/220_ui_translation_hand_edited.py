"""Mark hand-edited UI translations so the seeder stops reverting them (F-ADM-5).

The i18n seeder runs on every boot and overwrites any `ui_translations` row
whose value differs from the source constant in `scripts/seed_i18n.py`. That
silently reverted every edit made on the admin Translations screen — the screen
said "Saved successfully" and the next deploy put the old text back. This adds
`hand_edited_at`: the console stamps it on save, and the seeder leaves a stamped
row's value alone. Null (every existing row) stays source-managed as before.

Revision ID: 220_ui_translation_hand_edited
Revises: 219_kitchen_ticket_seq_unique
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "220_ui_translation_hand_edited"
down_revision: Union[str, None] = "219_kitchen_ticket_seq_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ui_translations",
        sa.Column("hand_edited_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ui_translations", "hand_edited_at")
