"""A legal entity's other registration documents, and its other files.

`legal_entity_documents` — versioned issue→expiry cycles of the paperwork an
entity holds besides its trade licence and VAT certificate: the immigration
establishment card, the MOHRE labour company card, the Chamber of Commerce
membership, the Corporate Tax certificate and MoA (issued once, no expiry), and
the documents of the one shop each entity manages (tenancy contract,
municipality food permit, Civil Defence certificate, signage permit,
pest-control contract). The cycle expiring last per (entity, type) is current;
the tracked ones join the super-admin's expiring-paperwork warning.

`legal_entity_files` — anything else, a named file with a free-text type, like
`employee_files`.

The lists in the CHECKs mirror `app.models.legal_entity`. Schema only: no rows.

Revision ID: 330_legal_entity_documents
Revises: 329_employee_files
Create Date: 2026-11-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "330_legal_entity_documents"
down_revision: Union[str, None] = "329_employee_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOC_TYPES = (
    "establishment_card",
    "mohre_card",
    "chamber_certificate",
    "corporate_tax",
    "moa",
    "tenancy_contract",
    "food_permit",
    "civil_defence",
    "signage_permit",
    "pest_control",
)
NON_EXPIRING = ("corporate_tax", "moa")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "legal_entity_documents",
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
        sa.Column("doc_type", sa.String(30), nullable=False),
        sa.Column("document_number", sa.String(100), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("issuing_authority", sa.String(120), nullable=True),
        sa.Column(
            "details", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("file_object_key", sa.String(255), nullable=True),
        sa.Column("file_content_type", sa.String(100), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"doc_type IN ({_quoted(DOC_TYPES)})",
            name="ck_legal_entity_documents_doc_type",
        ),
        sa.CheckConstraint(
            f"expiry_date IS NOT NULL OR doc_type IN ({_quoted(NON_EXPIRING)})",
            name="ck_legal_entity_documents_expiry",
        ),
        # One cycle per (entity, type, expiry) — and the index that finds an
        # entity's documents. A type issued once has no expiry; NULLs never
        # collide.
        sa.UniqueConstraint(
            "legal_entity_id",
            "doc_type",
            "expiry_date",
            name="uq_legal_entity_documents_cycle",
        ),
        sa.CheckConstraint(
            "issue_date IS NULL OR expiry_date IS NULL OR issue_date <= expiry_date",
            name="ck_legal_entity_documents_dates",
        ),
    )
    op.create_index(
        "ix_legal_entity_documents_expiry", "legal_entity_documents", ["expiry_date"]
    )

    op.create_table(
        "legal_entity_files",
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
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("file_object_key", sa.String(255), nullable=True),
        sa.Column("file_content_type", sa.String(100), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_legal_entity_files_entity", "legal_entity_files", ["legal_entity_id"]
    )


def downgrade() -> None:
    # Dropping the tables orphans the scans in the bucket; refuse while rows
    # exist so nobody loses the index to files they cannot then find.
    bind = op.get_bind()
    held = sum(
        bind.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() or 0
        for table in ("legal_entity_documents", "legal_entity_files")
    )
    if held:
        raise RuntimeError(
            f"{held} legal-entity document(s)/file(s) exist — remove them in the "
            "console before downgrading past 330_legal_entity_documents"
        )
    op.drop_index("ix_legal_entity_files_entity", table_name="legal_entity_files")
    op.drop_table("legal_entity_files")
    op.drop_index(
        "ix_legal_entity_documents_expiry", table_name="legal_entity_documents"
    )
    op.drop_table("legal_entity_documents")
