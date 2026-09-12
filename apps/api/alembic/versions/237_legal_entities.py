"""A normalised legal-entity record: trade licence + optional VAT registration.

VAT treatment and printed identity were loose strings on the order and the
per-channel config (`234`–`236`). That could not answer "what VAT does each
trade licence owe" and let each branch inherit a different name. This introduces
the entity everything rolls up to: a legal name (the trade licence holder), a
registered brand name shown on the receipt, whether it is VAT-registered, its
TRN, the document title, and its logo.

Two entities, agreed with the owner:
  * Fatema Cake Sweets — brand "Melting Moments Cakes", VAT-registered, TRN
    104711479600003, "Tax Invoice". The Melting Moments logo.
  * Najm AlShamal Coffee Shop LLC — brand "Attibassi Coffee", NOT registered,
    no TRN, "Invoice". The Attibassi logo.

The receipt shows the BRAND + TRN (UAE lets a registrant invoice under a
registered trade name — why a Nike receipt shows the brand, not the LLC); the
legal name is for records and the VAT return. Logos are served from the public
`mm-product-images` bucket, not bundled in the app.

`reference` is a stable slug so `238`/`239` and any later migration can find an
entity without hard-coding a UUID.

Revision ID: 237_legal_entities
Revises: 236_barsha_counter_unregistered
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "237_legal_entities"
down_revision: Union[str, None] = "236_barsha_counter_unregistered"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "legal_entities"
_MM_LOGO = "https://storage.googleapis.com/mm-product-images/logos/melting-moments.png"
_ATTIBASSI_LOGO = "https://storage.googleapis.com/mm-product-images/logos/attibassi.jpg"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # A stable slug to look the entity up by, so backfills never guess a UUID.
        sa.Column("reference", sa.String(50), nullable=False),
        # The trade-licence holder's legal name — records / VAT return, not the
        # receipt header.
        sa.Column("legal_name", sa.String(200), nullable=False),
        # The registered trade name shown to the customer (Melting Moments Cakes,
        # Attibassi Coffee).
        sa.Column("brand_name", sa.String(200), nullable=False),
        sa.Column(
            "vat_registered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        # TRN — present only when registered.
        sa.Column("tax_number", sa.String(50), nullable=True),
        sa.Column(
            "invoice_title",
            sa.String(120),
            nullable=False,
            server_default="Tax Invoice",
        ),
        sa.Column("trade_license_number", sa.String(100), nullable=True),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.UniqueConstraint("reference", name="uq_legal_entities_reference"),
        if_not_exists=True,
    )

    # Seed the two entities. Guarded on `reference` so a re-run inserts nothing
    # and an admin edit is never overwritten.
    op.execute(
        f"""
        INSERT INTO {_TABLE}
            (id, reference, legal_name, brand_name, vat_registered, tax_number,
             invoice_title, logo_url)
        VALUES
            (gen_random_uuid(), 'fatema', 'Fatema Cake Sweets',
             'Melting Moments Cakes', true, '104711479600003', 'Tax Invoice',
             '{_MM_LOGO}'),
            (gen_random_uuid(), 'najm', 'Najm AlShamal Coffee Shop LLC',
             'Attibassi Coffee', false, NULL, 'Invoice', '{_ATTIBASSI_LOGO}')
        ON CONFLICT (reference) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table(_TABLE, if_exists=True)
