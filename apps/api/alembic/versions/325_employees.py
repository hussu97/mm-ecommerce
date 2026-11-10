"""Employees and their versioned right-to-work documents.

`employees` holds the people the shop employs (not console/till accounts — that
is `users`), and `employee_documents` one row per issue→expiry cycle of each
person's passport, residence visa, MOHRE labour card and Occupational Health
Card, with its scan in the private finance bucket. The row expiring last per
(employee, type) is the current one. Super-admin only, end to end.

Schema only: no rows are seeded, the owner enters each employee from their
documents in the console.

Revision ID: 325_employees
Revises: 324_legal_entity_documents
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "325_employees"
down_revision: Union[str, None] = "324_legal_entity_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Spelled out rather than imported, so this revision keeps meaning what it
#: meant when the model's lists later grow.
_STATUSES = ("active", "left")
_DOC_TYPES = ("passport", "visa", "labour_card", "ohc")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


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


def _id() -> sa.Column:
    return sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "employees",
        _id(),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("full_name_ar", sa.String(200), nullable=True),
        sa.Column("nationality", sa.String(80), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(1), nullable=True),
        sa.Column("job_title", sa.String(120), nullable=True),
        sa.Column("personal_number", sa.String(30), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column(
            "legal_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("joined_on", sa.Date(), nullable=True),
        sa.Column("left_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("photo_object_key", sa.String(255), nullable=True),
        sa.Column("photo_content_type", sa.String(100), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in_list("status", _STATUSES), name="ck_employees_status"),
        sa.CheckConstraint(
            "gender IS NULL OR gender IN ('M', 'F')", name="ck_employees_gender"
        ),
    )
    op.create_index("ix_employees_status", "employees", ["status"])
    op.create_index("ix_employees_legal_entity", "employees", ["legal_entity_id"])
    op.create_index("ix_employees_branch", "employees", ["branch_id"])

    op.create_table(
        "employee_documents",
        _id(),
        sa.Column(
            "employee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("document_number", sa.String(100), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("issuing_authority", sa.String(120), nullable=True),
        sa.Column(
            "details", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("file_object_key", sa.String(255), nullable=True),
        sa.Column("file_content_type", sa.String(100), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            _in_list("doc_type", _DOC_TYPES), name="ck_employee_documents_doc_type"
        ),
        sa.CheckConstraint(
            "issue_date IS NULL OR issue_date <= expiry_date",
            name="ck_employee_documents_dates",
        ),
        sa.UniqueConstraint(
            "employee_id", "doc_type", "expiry_date", name="uq_employee_documents_cycle"
        ),
    )
    op.create_index(
        "ix_employee_documents_employee",
        "employee_documents",
        ["employee_id", "doc_type"],
    )
    op.create_index(
        "ix_employee_documents_expiry", "employee_documents", ["expiry_date"]
    )


def downgrade() -> None:
    op.drop_table("employee_documents")
    op.drop_table("employees")
