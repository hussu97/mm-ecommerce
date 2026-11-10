"""Legal-entity VAT/e-invoicing fields, VAT certificate, trade-licence history.

`legal_entities` gains what a VAT registration and UAE e-invoicing need: the
10-digit TIN (first ten digits of the TRN), the Peppol participant ID
(`0235:<TIN>`), the issuing authority, the emirate, the effective date, and the
VAT certificate (private finance bucket, signed on read). It also gains
`trade_license_expiry`, which with the existing `trade_license_number` mirrors
the current licence cycle.

`legal_entity_trade_licenses` keeps every issue→expiry cycle of an entity's
trade licence, each with its own document, so the history stays on file after a
renewal. The cycle that expires last is the current one.

Content (rule 7): the values on the documents the owner supplied on 2026-11-10 —
Fatema Cake Sweets (SEDD licence 778179, expiring 2027-11-02; FTA VAT
certificate, TRN 104711479600003, effective 2024-11-01) and Najm AlShamal
Coffee Shop LLC (Dubai DET licence 1081755, expiring 2027-07-18; not VAT
registered). Guarded: each update matches only a still-blank field on the entity
with the expected reference (and, for Fatema's VAT, the expected TRN), and a
cycle is inserted only if that entity has none for that expiry — so an edit made
in the console is never overwritten. The document keys point at the scans
uploaded to `gs://melting-moments-data/legal-entities/` the same day; on a
database whose bucket lacks them, "View" just fails to sign/open.

Revision ID: 324_legal_entity_documents
Revises: 323_last_counted_credible
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "324_legal_entity_documents"
down_revision: Union[str, None] = "323_last_counted_credible"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "legal_entity_trade_licenses"

#: Spelled out rather than imported, so this revision keeps meaning what it
#: meant when the model's lists later grow.
_EMIRATES = (
    "Abu Dhabi",
    "Dubai",
    "Sharjah",
    "Ajman",
    "Umm Al Quwain",
    "Ras Al Khaimah",
    "Fujairah",
)
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

_LICENSE_TYPES = (
    "commercial",
    "professional",
    "industrial",
    "tourism",
    "crafts",
    "agricultural",
    "e_trader",
    "freelance",
    "other",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IS NULL OR {column} IN ({allowed})"


#: (reference, license_number, registration_number, authority, emirate,
#:  issue_date, expiry_date, document key, licence type, legal form) — as
#:  printed on the licence. Fatema's is a Sharjah "Trading License" (commercial)
#:  whose legal status is "Services Agency"; Najm's a Dubai "Commercial License"
#:  for a single-owner LLC.
_LICENSES = (
    (
        "fatema",
        "778179",
        "193868",
        "sharjah_sedd",
        "Sharjah",
        "2020-11-02",
        "2027-11-02",
        "legal-entities/fatema/trade-licenses/2027-11-02.pdf",
        "commercial",
        "Services Agency",
    ),
    (
        "najm",
        "1081755",
        "1764353",
        "dubai_det",
        "Dubai",
        "2022-07-19",
        "2027-07-18",
        "legal-entities/najm/trade-licenses/2027-07-18.pdf",
        "commercial",
        "Limited Liability Company - Single Owner (LLC - SO)",
    ),
)


def upgrade() -> None:
    le = "legal_entities"
    op.add_column(le, sa.Column("trade_license_expiry", sa.Date(), nullable=True))
    op.add_column(le, sa.Column("vat_tin", sa.String(10), nullable=True))
    op.add_column(le, sa.Column("peppol_id", sa.String(50), nullable=True))
    op.add_column(le, sa.Column("vat_issuing_authority", sa.String(120), nullable=True))
    op.add_column(le, sa.Column("vat_emirate", sa.String(20), nullable=True))
    op.add_column(le, sa.Column("vat_effective_date", sa.Date(), nullable=True))
    op.add_column(
        le, sa.Column("vat_certificate_object_key", sa.String(255), nullable=True)
    )
    op.add_column(
        le, sa.Column("vat_certificate_content_type", sa.String(100), nullable=True)
    )
    op.create_check_constraint(
        "ck_legal_entities_vat_emirate", le, _in_list("vat_emirate", _EMIRATES)
    )
    op.create_check_constraint(
        "ck_legal_entities_vat_tin_format",
        le,
        "vat_tin IS NULL OR vat_tin ~ '^[0-9]{10}$'",
    )

    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "legal_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("license_number", sa.String(100), nullable=False),
        sa.Column("registration_number", sa.String(100), nullable=True),
        sa.Column("issuing_authority", sa.String(40), nullable=False),
        sa.Column("license_type", sa.String(30), nullable=True),
        sa.Column("legal_form", sa.String(120), nullable=True),
        sa.Column("emirate", sa.String(20), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("document_object_key", sa.String(255), nullable=True),
        sa.Column("document_content_type", sa.String(100), nullable=True),
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
        sa.UniqueConstraint(
            "legal_entity_id", "expiry_date", name="uq_legal_entity_trade_license_cycle"
        ),
        sa.CheckConstraint(
            _in_list("issuing_authority", _AUTHORITIES),
            name="ck_legal_entity_trade_licenses_authority",
        ),
        sa.CheckConstraint(
            _in_list("emirate", _EMIRATES),
            name="ck_legal_entity_trade_licenses_emirate",
        ),
        sa.CheckConstraint(
            _in_list("license_type", _LICENSE_TYPES),
            name="ck_legal_entity_trade_licenses_type",
        ),
        sa.CheckConstraint(
            "issue_date IS NULL OR issue_date <= expiry_date",
            name="ck_legal_entity_trade_licenses_dates",
        ),
    )
    op.create_index(
        "ix_legal_entity_trade_licenses_entity", _TABLE, ["legal_entity_id"]
    )

    _seed()


def _seed() -> None:
    # Literal SQL (fixed values, no user input) rather than bind parameters:
    # the deploy runs migrations under asyncpg, which cannot infer the type of
    # a bare parameter compared against NULL.
    for (
        ref,
        number,
        reg,
        authority,
        emirate,
        issued,
        expires,
        key,
        kind,
        form,
    ) in _LICENSES:
        op.execute(
            f"""
            INSERT INTO {_TABLE} (
                legal_entity_id, license_number, registration_number,
                issuing_authority, emirate, issue_date, expiry_date,
                document_object_key, document_content_type, license_type,
                legal_form
            )
            SELECT le.id, '{number}', '{reg}', '{authority}', '{emirate}',
                   DATE '{issued}', DATE '{expires}', '{key}', 'application/pdf',
                   '{kind}', '{form}'
              FROM legal_entities le
             WHERE le.reference = '{ref}'
               AND NOT EXISTS (
                   SELECT 1 FROM {_TABLE} t
                    WHERE t.legal_entity_id = le.id
                      AND t.expiry_date = DATE '{expires}'
               )
            """
        )
    # Mirror the current cycle (latest expiry) onto every entity whose mirror
    # is blank — the column was always empty in production until now.
    op.execute(
        f"""
        UPDATE legal_entities le
           SET trade_license_number = cur.license_number,
               trade_license_expiry = cur.expiry_date
          FROM (
                SELECT DISTINCT ON (legal_entity_id)
                       legal_entity_id, license_number, expiry_date
                  FROM {_TABLE}
              ORDER BY legal_entity_id, expiry_date DESC
               ) cur
         WHERE cur.legal_entity_id = le.id
           AND le.trade_license_expiry IS NULL
           AND (le.trade_license_number IS NULL OR le.trade_license_number = ''
                OR le.trade_license_number = cur.license_number)
        """
    )
    # Fatema's VAT registration, from the FTA certificate issued 2024-11-18.
    # The TIN is the TRN's first ten digits; the Peppol ID is the UAE scheme
    # 0235 plus the TIN.
    op.execute(
        """
        UPDATE legal_entities
           SET vat_tin = '1047114796',
               peppol_id = '0235:1047114796',
               vat_issuing_authority = 'Federal Tax Authority (FTA)',
               vat_emirate = 'Sharjah',
               vat_effective_date = DATE '2024-11-01',
               vat_certificate_object_key = 'legal-entities/fatema/vat-certificate.pdf',
               vat_certificate_content_type = 'application/pdf'
         WHERE reference = 'fatema'
           AND tax_number = '104711479600003'
           AND vat_tin IS NULL
           AND peppol_id IS NULL
           AND vat_certificate_object_key IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_legal_entity_trade_licenses_entity", table_name=_TABLE)
    op.drop_table(_TABLE)
    le = "legal_entities"
    op.drop_constraint("ck_legal_entities_vat_tin_format", le, type_="check")
    op.drop_constraint("ck_legal_entities_vat_emirate", le, type_="check")
    for col in (
        "vat_certificate_content_type",
        "vat_certificate_object_key",
        "vat_effective_date",
        "vat_emirate",
        "vat_issuing_authority",
        "peppol_id",
        "vat_tin",
        "trade_license_expiry",
    ):
        op.drop_column(le, col)
