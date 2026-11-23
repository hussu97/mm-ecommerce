"""A sponsor outside our legal entities: `employees.sponsor_name`.

The employee's visa sponsor was only ever one of our own trade licences
(`legal_entity_id`). The console also holds people sponsored elsewhere — the
owner keeps his own papers here, sponsored by his employer — and those
sponsors are not legal entities we book orders under, so they are a name.
At most one of the two is set.

Schema only: no rows change.

Revision ID: 331_employee_sponsor_name
Revises: 330_legal_entity_documents
Create Date: 2026-11-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "331_employee_sponsor_name"
down_revision: Union[str, None] = "330_legal_entity_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("sponsor_name", sa.String(200), nullable=True))
    op.create_check_constraint(
        "ck_employees_one_sponsor",
        "employees",
        "legal_entity_id IS NULL OR sponsor_name IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_employees_one_sponsor", "employees", type_="check")
    op.drop_column("employees", "sponsor_name")
