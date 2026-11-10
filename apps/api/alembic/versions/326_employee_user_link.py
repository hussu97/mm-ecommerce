"""Link an employee to their console / till account.

`employees.user_id` → `users.id`, nullable, one employee per account (unique),
`ON DELETE SET NULL` so removing an account never takes the HR record with it.

Revision ID: 326_employee_user_link
Revises: 325_employees
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "326_employee_user_link"
down_revision: Union[str, None] = "325_employees"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_employees_user", "employees", ["user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_employees_user", "employees", type_="unique")
    op.drop_column("employees", "user_id")
