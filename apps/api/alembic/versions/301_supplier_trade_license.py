"""Supplier trade licence: number, issuing authority, and two documents.

`suppliers` gains `trade_license_number`, `trade_license_authority` (a code from
`models.inventory.TRADE_LICENSE_AUTHORITIES`, held to that list by
`ck_supplier_trade_license_authority`), and an object key + content type for each
of the VAT (TRN) certificate and the trade licence, stored in the private finance
bucket. All nullable; existing suppliers are left blank.

Revision ID: 301_supplier_trade_license
Revises: 300_marketplace_cancellation_net
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "301_supplier_trade_license"
down_revision: Union[str, None] = "300_marketplace_cancellation_net"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Spelled out rather than imported, so this revision keeps meaning what it
#: meant when the model's list later grows.
_AUTHORITIES = (
    "dubai_det",
    "dmcc",
    "jafza",
    "dafz",
    "dso",
    "difc",
    "dda",
    "dubai_south",
    "ifza",
    "meydan",
    "dhcc",
    "dwtc",
    "dubai_commercity",
    "dubai_maritime",
    "abu_dhabi_ded",
    "adgm",
    "kezad",
    "twofour54",
    "masdar",
    "adafz",
    "sharjah_sedd",
    "saif_zone",
    "hamriyah",
    "shams",
    "spc",
    "srtip",
    "sharjah_healthcare",
    "ajman_ded",
    "ajman_free_zone",
    "ajman_media_city",
    "uaq_ded",
    "uaq_ftz",
    "rak_ded",
    "rakez",
    "fujairah_ded",
    "fujairah_free_zone",
    "creative_city",
    "other",
)


def upgrade() -> None:
    op.add_column(
        "suppliers", sa.Column("trade_license_number", sa.String(50), nullable=True)
    )
    op.add_column(
        "suppliers",
        sa.Column("trade_license_authority", sa.String(40), nullable=True),
    )
    for doc in ("trn_certificate", "trade_license"):
        op.add_column(
            "suppliers",
            sa.Column(f"{doc}_object_key", sa.String(255), nullable=True),
        )
        op.add_column(
            "suppliers",
            sa.Column(f"{doc}_content_type", sa.String(100), nullable=True),
        )
    op.create_check_constraint(
        "ck_supplier_trade_license_authority",
        "suppliers",
        "trade_license_authority IS NULL OR trade_license_authority IN ("
        + ", ".join(f"'{code}'" for code in _AUTHORITIES)
        + ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_supplier_trade_license_authority", "suppliers", type_="check"
    )
    for doc in ("trade_license", "trn_certificate"):
        op.drop_column("suppliers", f"{doc}_content_type")
        op.drop_column("suppliers", f"{doc}_object_key")
    op.drop_column("suppliers", "trade_license_authority")
    op.drop_column("suppliers", "trade_license_number")
