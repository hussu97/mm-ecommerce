"""Other papers kept against an employee: `employee_files`.

A contract, a certificate, a warning letter — anything without the fixed
fields and expiry of the versioned `employee_documents`. Each row is a named
file with a free-text type, any number per employee, its scan in the private
finance bucket under `employees/<id>/files/`. Super-admin only, like the rest.

Schema only: no rows.

Revision ID: 329_employee_files
Revises: 328_employee_insurance_wps
Create Date: 2026-11-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "329_employee_files"
down_revision: Union[str, None] = "328_employee_insurance_wps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_files",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "employee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("file_object_key", sa.String(255), nullable=True),
        sa.Column("file_content_type", sa.String(100), nullable=True),
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
    op.create_index("ix_employee_files_employee", "employee_files", ["employee_id"])


def downgrade() -> None:
    # Dropping the table orphans any scans in the bucket; refuse while rows
    # exist so nobody loses the index to files they cannot then find.
    held = op.get_bind().exec_driver_sql("SELECT count(*) FROM employee_files").scalar()
    if held:
        raise RuntimeError(
            f"{held} employee file(s) exist — remove them in the console "
            "before downgrading past 329_employee_files"
        )
    op.drop_index("ix_employee_files_employee", table_name="employee_files")
    op.drop_table("employee_files")
