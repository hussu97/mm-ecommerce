"""Point each (branch, channel) tax config at a legal entity.

`branch_channel_tax_configs` carried the VAT flag and identity as inline strings
(`234`). The legal entity now owns those, so the config just references one, plus
its existing optional `tax_group_id` override. Repoint the seeded rows — Barsha's
counter to Najm AlShamal, every other row to Fatema — then drop the inline
columns.

Revision ID: 238_config_legal_entity
Revises: 237_legal_entities
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "238_config_legal_entity"
down_revision: Union[str, None] = "237_legal_entities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "branch_channel_tax_configs"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "legal_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_entities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        f"ix_{_TABLE}_legal_entity_id", _TABLE, ["legal_entity_id"], if_not_exists=True
    )

    # Barsha's counter → Najm AlShamal; everything else → Fatema. Env-agnostic:
    # entities by their stable slug, Barsha by branch name.
    op.execute(
        f"""
        UPDATE {_TABLE} AS c
        SET legal_entity_id = (SELECT id FROM legal_entities WHERE reference = 'najm')
        FROM branches AS b
        WHERE c.branch_id = b.id
          AND b.name ILIKE '%barsha%'
          AND c.channel_class = 'counter'
        """
    )
    op.execute(
        f"""
        UPDATE {_TABLE}
        SET legal_entity_id = (SELECT id FROM legal_entities WHERE reference = 'fatema')
        WHERE legal_entity_id IS NULL
        """
    )

    # Every config row now names an entity.
    op.alter_column(_TABLE, "legal_entity_id", nullable=False)

    # The entity owns these now.
    op.drop_column(_TABLE, "vat_registered")
    op.drop_column(_TABLE, "tax_number")
    op.drop_column(_TABLE, "tax_registration_name")
    op.drop_column(_TABLE, "invoice_title")


def downgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "vat_registered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(_TABLE, sa.Column("tax_number", sa.String(50), nullable=True))
    op.add_column(
        _TABLE, sa.Column("tax_registration_name", sa.String(200), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("invoice_title", sa.String(120), nullable=True))
    # Restore the one bit the readers actually need from the entity.
    op.execute(
        f"""
        UPDATE {_TABLE} AS c
        SET vat_registered = e.vat_registered,
            tax_number = e.tax_number,
            tax_registration_name = e.legal_name,
            invoice_title = e.invoice_title
        FROM legal_entities AS e
        WHERE c.legal_entity_id = e.id
        """
    )
    op.drop_index(f"ix_{_TABLE}_legal_entity_id", table_name=_TABLE, if_exists=True)
    op.drop_column(_TABLE, "legal_entity_id")
