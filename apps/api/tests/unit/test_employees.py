"""Employees, their versioned documents, and the expiring-paperwork warning.

Super-admin only, end to end: every `/employees` and `/compliance` route must
carry `require_superadmin()`, which no role slug satisfies. A document is one
issue→expiry cycle; the one expiring last per type is current, and only the
current tracked document (visa, Emirates ID, labour card, OHC, medical
insurance, WPS card) of an active employee (plus each active legal
entity's current trade licence) feeds the 15-day warning.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.v1 import compliance, employees
from app.core.exceptions import ForbiddenError
from app.core.permissions import SUPERADMIN, require_superadmin
from app.models.employee import (
    EMPLOYEE_DOCUMENT_SPECS,
    EMPLOYEE_DOCUMENT_TYPES,
    EMPLOYEE_STATUSES,
    EXPIRY_TRACKED_TYPES,
    EmployeeDocument,
)
from app.models.role import ALL_PERMISSIONS
from app.schemas.employee import EmployeeDocumentCreate, EmployeeDocumentUpdate
from app.services.people import employee_service

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION = VERSIONS / "325_employees.py"
#: The revisions that widened the document-type CHECK, oldest first; the last
#: one must match the model.
DOC_TYPES_MIGRATIONS = [
    MIGRATION,
    VERSIONS / "327_employee_emirates_id.py",
    VERSIONS / "328_employee_insurance_wps.py",
]


def _migration(path: Path = MIGRATION):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── Access ──────────────────────────────────────────────────────────────────


def _routes(router):
    return [r for r in router.routes if isinstance(r, APIRoute)]


@pytest.mark.parametrize("router", [employees.router, compliance.router])
def test_every_route_is_superadmin_only(router):
    routes = _routes(router)
    assert routes
    for route in routes:
        stamps = {
            getattr(dep.call, "permission", None)
            for dep in route.dependant.dependencies
        }
        assert SUPERADMIN in stamps, (
            f"{route.methods} {route.path} is not super-admin only"
        )


def test_the_superadmin_stamp_is_not_a_grantable_permission():
    assert SUPERADMIN not in ALL_PERMISSIONS


async def test_require_superadmin_refuses_a_role_holding_every_slug():
    check = require_superadmin()
    role = SimpleNamespace(is_super_admin=False, permissions=list(ALL_PERMISSIONS))
    manager = SimpleNamespace(is_superadmin=False, is_admin=False, role=role)
    with pytest.raises(ForbiddenError):
        await check(user=manager)


async def test_require_superadmin_admits_a_superadmin():
    check = require_superadmin()
    owner = SimpleNamespace(is_superadmin=True)
    assert await check(user=owner) is owner


# ─── Vocabularies agree ──────────────────────────────────────────────────────


def test_migration_lists_match_the_model():
    assert _migration()._STATUSES == EMPLOYEE_STATUSES
    revisions = [_migration(p) for p in DOC_TYPES_MIGRATIONS]
    assert revisions[-1]._DOC_TYPES == EMPLOYEE_DOCUMENT_TYPES
    # Each widening restores exactly what the one before it created.
    for before, after in zip(revisions, revisions[1:], strict=False):
        assert after._PREVIOUS_DOC_TYPES == before._DOC_TYPES


def test_every_type_has_a_spec_and_tracked_types_exclude_the_passport():
    assert set(EMPLOYEE_DOCUMENT_SPECS) == set(EMPLOYEE_DOCUMENT_TYPES)
    assert set(EXPIRY_TRACKED_TYPES) == {
        "visa",
        "emirates_id",
        "labour_card",
        "ohc",
        "medical_insurance",
        "wps_card",
    }


async def test_document_types_name_the_authority_per_type():
    specs = {s.doc_type: s for s in await employees.document_types(_=None)}
    assert specs["medical_insurance"].authority_label == "Insurer"
    assert specs["wps_card"].authority_label == "Bank / exchange house"
    assert specs["visa"].authority_label == "Issuing authority"
    assert (
        specs["wps_card"].expiry_tracked and specs["medical_insurance"].expiry_tracked
    )


# ─── Schemas ─────────────────────────────────────────────────────────────────


def test_document_details_drop_blanks_and_refuse_unknown_keys():
    doc = EmployeeDocumentCreate(
        doc_type="labour_card",
        expiry_date=date(2028, 7, 16),
        details={"personal_number": " 30424018582909 ", "profession": ""},
    )
    assert doc.details == {"personal_number": "30424018582909"}
    with pytest.raises(ValidationError):
        EmployeeDocumentCreate(
            doc_type="labour_card",
            expiry_date=date(2028, 7, 16),
            details={"uid_number": "1"},
        )


def test_emirates_id_details_are_accepted():
    payload = EmployeeDocumentCreate(
        doc_type="emirates_id",
        document_number="784-1990-1234567-1",
        expiry_date=date(2028, 1, 31),
        details={"card_number": "123456789", "occupation": "Baker", "employer": " "},
    )
    assert payload.details == {"card_number": "123456789", "occupation": "Baker"}


@pytest.mark.parametrize(
    "number", ["4111 1111 1111 1111", "4111-1111-1111-1111", "5123456789012"]
)
def test_a_full_salary_card_number_is_refused(number):
    from app.core.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        employee_service._check_number("wps_card", number)


@pytest.mark.parametrize("number", [None, "", "1234", "•••• 1234"])
def test_a_salary_card_keeps_its_last_four(number):
    employee_service._check_number("wps_card", number)


def test_other_types_may_carry_long_numbers():
    # An insurance member number can run long; only the salary card is a PAN.
    employee_service._check_number("medical_insurance", "1234567890123456")


def test_issue_after_expiry_is_refused():
    with pytest.raises(ValidationError):
        EmployeeDocumentCreate(
            doc_type="ohc", issue_date=date(2027, 9, 3), expiry_date=date(2027, 9, 2)
        )
    with pytest.raises(ValidationError):
        EmployeeDocumentUpdate(
            issue_date=date(2027, 9, 3), expiry_date=date(2027, 9, 2)
        )


def test_current_by_type_is_the_cycle_expiring_last():
    old = EmployeeDocument(
        id=uuid.uuid4(), doc_type="visa", expiry_date=date(2025, 1, 1)
    )
    new = EmployeeDocument(
        id=uuid.uuid4(), doc_type="visa", expiry_date=date(2027, 1, 1)
    )
    ohc = EmployeeDocument(
        id=uuid.uuid4(), doc_type="ohc", expiry_date=date(2026, 1, 1)
    )
    current = employee_service.current_by_type([new, ohc, old])
    assert current == {"visa": new, "ohc": ohc}


def test_object_keys_are_per_employee_and_cycle():
    eid = uuid.uuid4()
    row = EmployeeDocument(
        employee_id=eid, doc_type="visa", expiry_date=date(2027, 3, 4)
    )
    assert (
        employee_service.document_key(row, ".pdf")
        == f"employees/{eid}/visa/2027-03-04.pdf"
    )


# ─── Real Postgres ───────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@db_required
async def test_versions_and_the_expiry_warning(monkeypatch):
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import private_documents
    from app.core.exceptions import ConflictError
    from app.models import Employee, LegalEntity
    from app.services.people import expiry_service

    uploads: list[str] = []
    deleted: list[str] = []

    async def fake_upload(key, body, content_type, label):
        uploads.append(key)

    async def fake_delete(key):
        deleted.append(key)

    monkeypatch.setattr(private_documents, "upload", fake_upload)
    monkeypatch.setattr(private_documents, "delete_quietly", fake_delete)

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    today = date(2026, 11, 10)
    ids: list[uuid.UUID] = []
    entity_id: uuid.UUID | None = None
    try:
        async with Session() as db:
            adar = await employee_service.create_employee(
                db, {"full_name": f"Adar {tag}", "status": "active"}
            )
            gone = await employee_service.create_employee(
                db, {"full_name": f"Gone {tag}", "status": "left"}
            )
            ids += [adar.id, gone.id]

            def doc(doc_type, days):
                return {
                    "doc_type": doc_type,
                    "expiry_date": today + timedelta(days=days),
                    "details": {},
                }

            # Two visa cycles: the lapsed one is history, the renewal is current.
            # OHC lapsing in 10 days is warned; the labour card in 16 not yet;
            # a passport lapsing tomorrow is not tracked at all; an Emirates ID
            # lapsing in 3 days is, and so is a health policy lapsing in 7; a
            # salary card lapsing in 20 is not yet.
            for doc_type, days in (
                ("visa", -30),
                ("visa", 700),
                ("emirates_id", 3),
                ("medical_insurance", 7),
                ("wps_card", 20),
                ("ohc", 10),
                ("labour_card", 16),
                ("passport", 1),
            ):
                row = await employee_service.create_document(
                    db, adar, doc(doc_type, days)
                )
                if doc_type == "ohc":
                    ohc = row
            # Someone who has left is not warned about.
            await employee_service.create_document(db, gone, doc("visa", 2))
            # A second cycle with the same expiry is the same cycle.
            with pytest.raises(ConflictError):
                await employee_service.create_document(db, adar, doc("ohc", 10))

            ohc = await employee_service.store_document_file(
                db, ohc, b"%PDF-1.4", "application/pdf"
            )
            assert (
                ohc.file_object_key
                == f"employees/{adar.id}/ohc/{(today + timedelta(days=10)).isoformat()}.pdf"
            )

            entity = LegalEntity(
                reference=f"t-{tag}",
                legal_name=f"Entity {tag}",
                brand_name="E",
                trade_license_number="778179",
                trade_license_expiry=today + timedelta(days=5),
            )
            db.add(entity)
            await db.commit()
            entity_id = entity.id

        async with Session() as db:
            result = await expiry_service.expiring_documents(db, today=today)
            mine = [
                (i.owner_name, i.doc_type, i.days_left)
                for i in result.items
                if tag in i.owner_name
            ]
            assert mine == [
                (f"Adar {tag}", "emirates_id", 3),
                (f"Entity {tag}", "trade_license", 5),
                (f"Adar {tag}", "medical_insurance", 7),
                (f"Adar {tag}", "ohc", 10),
            ]

            full = await employee_service.get_employee(db, adar.id)
            response = employee_service.to_response(full)
            visas = [d for d in response.documents if d.doc_type == "visa"]
            assert [d.is_current for d in visas] == [True, False]

            await employee_service.delete_employee(db, full)
            await db.commit()
            assert ohc.file_object_key in deleted
    finally:
        async with Session() as db:
            await db.execute(delete(Employee).where(Employee.id.in_(ids)))
            if entity_id:
                await db.execute(delete(LegalEntity).where(LegalEntity.id == entity_id))
            await db.commit()
        await engine.dispose()


@db_required
async def test_an_account_links_to_one_employee_at_most():
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.exceptions import BadRequestError, ConflictError
    from app.models import Employee
    from app.models.user import User

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    user_id = None
    try:
        async with Session() as db:
            user = User(email=f"till-{tag}@example.com", is_active=True, is_staff=True)
            db.add(user)
            await db.flush()
            user_id = user.id
            adar = await employee_service.create_employee(
                db, {"full_name": f"Adar {tag}", "user_id": user.id}
            )
            assert employee_service.to_response(adar).user_email == user.email
            with pytest.raises(ConflictError, match=f"Adar {tag}"):
                await employee_service.create_employee(
                    db, {"full_name": f"Other {tag}", "user_id": user.id}
                )
            with pytest.raises(BadRequestError):
                await employee_service.update_employee(
                    db, adar, {"user_id": uuid.uuid4()}
                )
            # Re-saving the same link on the same employee is not a conflict.
            await employee_service.update_employee(db, adar, {"user_id": user.id})
            await employee_service.update_employee(db, adar, {"user_id": None})
            assert employee_service.to_response(adar).user_email is None
            await db.commit()
    finally:
        async with Session() as db:
            await db.execute(
                delete(Employee).where(Employee.full_name.like(f"% {tag}"))
            )
            if user_id:
                await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await engine.dispose()
