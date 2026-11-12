"""Medical insurance and the WPS salary card join an employee's documents.

Widens the `employee_documents.doc_type` CHECK to admit `medical_insurance`
and `wps_card`. Both expire — a lapsed health policy is a fine for the
employer, a lapsed salary card is a payroll that cannot be paid out — so the
model tracks both in the expiring-paperwork warning (`EXPIRY_TRACKED_TYPES`,
in code, not here).

Schema only: no rows. The downgrade refuses to run while any row of either
type exists rather than delete scans somebody entered.

Revision ID: 328_employee_insurance_wps
Revises: 327_employee_emirates_id
Create Date: 2026-11-12
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "328_employee_insurance_wps"
down_revision: Union[str, None] = "327_employee_emirates_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Spelled out rather than imported, so this revision keeps meaning what it
#: meant when the model's list later grows. Mirrors `EMPLOYEE_DOCUMENT_TYPES`.
_DOC_TYPES = (
    "passport",
    "visa",
    "emirates_id",
    "labour_card",
    "ohc",
    "medical_insurance",
    "wps_card",
)
_PREVIOUS_DOC_TYPES = ("passport", "visa", "emirates_id", "labour_card", "ohc")
_ADDED = tuple(t for t in _DOC_TYPES if t not in _PREVIOUS_DOC_TYPES)

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
            "SELECT count(*) FROM employee_documents WHERE " + _in_list(_ADDED)
        )
        .scalar()
    )
    if held:
        raise RuntimeError(
            f"{held} medical insurance / WPS card record(s) exist — remove them "
            "in the console before downgrading past 328_employee_insurance_wps"
        )
    op.drop_constraint(_CONSTRAINT, "employee_documents", type_="check")
    op.create_check_constraint(
        _CONSTRAINT, "employee_documents", _in_list(_PREVIOUS_DOC_TYPES)
    )
