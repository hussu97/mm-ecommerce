"""Driving licences and vehicle licences join an employee's documents.

Widens the `employee_documents.doc_type` CHECK to admit `driving_license` and
`vehicle_license` (the mulkiya). Both expire and are tracked by the
expiring-paperwork warning (`EXPIRY_TRACKED_TYPES`, in code). Neither is held
by everyone (`OPTIONAL_TYPES`).

A person can hold several vehicle licences at once, one per vehicle, so the
plate number — not the type — names a vehicle licence's run of renewals. The
one-cycle-per-(type, expiry) unique constraint becomes two partial unique
indexes: as before for every other type, and per (plate, expiry) for vehicle
licences, whose plate is then required. The plate is compared as upper-case
letters and digits only (`NUMBER_SERIES_SQL` in the model).

Schema only: no rows. The downgrade refuses to run while any row of either
type exists rather than delete scans somebody entered.

Revision ID: 332_employee_driving_vehicle
Revises: 331_employee_sponsor_name
Create Date: 2026-11-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "332_employee_driving_vehicle"
down_revision: Union[str, None] = "331_employee_sponsor_name"
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
    "driving_license",
    "vehicle_license",
)
_PREVIOUS_DOC_TYPES = (
    "passport",
    "visa",
    "emirates_id",
    "labour_card",
    "ohc",
    "medical_insurance",
    "wps_card",
)
_ADDED = tuple(t for t in _DOC_TYPES if t not in _PREVIOUS_DOC_TYPES)

_CONSTRAINT = "ck_employee_documents_doc_type"
_SERIES = "upper(regexp_replace(document_number, '[^A-Za-z0-9]', '', 'g'))"


def _in_list(values: tuple[str, ...]) -> str:
    return f"doc_type IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "employee_documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "employee_documents", _in_list(_DOC_TYPES))
    op.create_check_constraint(
        "ck_employee_documents_vehicle_plate",
        "employee_documents",
        "doc_type <> 'vehicle_license' OR coalesce(document_number, '') <> ''",
    )
    op.drop_constraint(
        "uq_employee_documents_cycle", "employee_documents", type_="unique"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_employee_documents_cycle ON employee_documents "
        "(employee_id, doc_type, expiry_date) WHERE doc_type <> 'vehicle_license'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_employee_documents_vehicle_cycle ON employee_documents "
        f"(employee_id, ({_SERIES}), expiry_date) WHERE doc_type = 'vehicle_license'"
    )


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
            f"{held} driving / vehicle licence record(s) exist — remove them "
            "in the console before downgrading past 332_employee_driving_vehicle"
        )
    op.execute("DROP INDEX uq_employee_documents_vehicle_cycle")
    op.execute("DROP INDEX uq_employee_documents_cycle")
    op.create_unique_constraint(
        "uq_employee_documents_cycle",
        "employee_documents",
        ["employee_id", "doc_type", "expiry_date"],
    )
    op.drop_constraint(
        "ck_employee_documents_vehicle_plate", "employee_documents", type_="check"
    )
    op.drop_constraint(_CONSTRAINT, "employee_documents", type_="check")
    op.create_check_constraint(
        _CONSTRAINT, "employee_documents", _in_list(_PREVIOUS_DOC_TYPES)
    )
