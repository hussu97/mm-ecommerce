"""Emirates ID joins an employee's versioned documents.

Widens the `employee_documents.doc_type` CHECK to admit `emirates_id`. Like the
visa, labour card and OHC, the card expires and a lapsed one is the employer's
problem, so the model tracks it in the expiring-paperwork warning — that list
lives in code (`EXPIRY_TRACKED_TYPES`), not here.

Schema only: no rows. The downgrade refuses to run while any Emirates ID row
exists rather than delete scans somebody entered.

Revision ID: 327_employee_emirates_id
Revises: 326_employee_user_link
Create Date: 2026-11-12
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "327_employee_emirates_id"
down_revision: Union[str, None] = "326_employee_user_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Spelled out rather than imported, so this revision keeps meaning what it
#: meant when the model's list later grows. Mirrors `EMPLOYEE_DOCUMENT_TYPES`.
_DOC_TYPES = ("passport", "visa", "emirates_id", "labour_card", "ohc")
_PREVIOUS_DOC_TYPES = ("passport", "visa", "labour_card", "ohc")

_CONSTRAINT = "ck_employee_documents_doc_type"


def _in_list(values: tuple[str, ...]) -> str:
    return f"doc_type IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "employee_documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "employee_documents", _in_list(_DOC_TYPES))


def downgrade() -> None:
    held = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT count(*) FROM employee_documents WHERE doc_type = 'emirates_id'"
        )
        .scalar()
    )
    if held:
        raise RuntimeError(
            f"{held} Emirates ID record(s) exist — remove them in the console "
            "before downgrading past 327_employee_emirates_id"
        )
    op.drop_constraint(_CONSTRAINT, "employee_documents", type_="check")
    op.create_check_constraint(
        _CONSTRAINT, "employee_documents", _in_list(_PREVIOUS_DOC_TYPES)
    )
