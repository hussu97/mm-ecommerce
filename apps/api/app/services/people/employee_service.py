"""Employee records and their versioned documents.

A document is one issue→expiry cycle of one type (passport, visa, labour card,
OHC). A renewal is a new row, never an edit of the old one, so every scan that
was valid on some date stays on file; the row expiring last per type is the
current one. Scans and the photo live in the private finance bucket under
`employees/<id>/…`, signed on read.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import private_documents as storage
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.search import contains
from app.models.employee import (
    EMPLOYEE_DOCUMENT_SPECS,
    Employee,
    EmployeeDocument,
)
from app.models.user import User
from app.schemas.employee import (
    EmployeeDocumentResponse,
    EmployeeDocumentSummary,
    EmployeeListItem,
    EmployeeResponse,
)

# ─── Keys ─────────────────────────────────────────────────────────────────────


def photo_key(employee: Employee, ext: str) -> str:
    return f"employees/{employee.id}/photo{ext}"


def document_key(row: EmployeeDocument, ext: str) -> str:
    """One object per cycle — (type, expiry) is unique per employee."""
    return (
        f"employees/{row.employee_id}/{row.doc_type}/{row.expiry_date.isoformat()}{ext}"
    )


# ─── Read models ──────────────────────────────────────────────────────────────


def current_by_type(documents: list[EmployeeDocument]) -> dict[str, EmployeeDocument]:
    """The cycle expiring last for each type."""
    current: dict[str, EmployeeDocument] = {}
    for doc in documents:
        held = current.get(doc.doc_type)
        if held is None or doc.expiry_date > held.expiry_date:
            current[doc.doc_type] = doc
    return current


def to_list_item(employee: Employee) -> EmployeeListItem:
    item = EmployeeListItem.model_validate(employee)
    item.current_documents = {
        t: EmployeeDocumentSummary.model_validate(d)
        for t, d in current_by_type(employee.documents).items()
    }
    return item


def to_document_response(
    row: EmployeeDocument, current: dict[str, EmployeeDocument]
) -> EmployeeDocumentResponse:
    payload = EmployeeDocumentResponse.model_validate(row)
    held = current.get(row.doc_type)
    payload.is_current = held is not None and held.id == row.id
    return payload


def to_response(employee: Employee) -> EmployeeResponse:
    current = current_by_type(employee.documents)
    payload = EmployeeResponse.model_validate(employee, from_attributes=True)
    payload.documents = [
        to_document_response(d, current)
        for d in sorted(
            employee.documents, key=lambda d: (d.doc_type, d.expiry_date), reverse=True
        )
    ]
    return payload


# ─── Employees ────────────────────────────────────────────────────────────────


async def list_employees(
    db: AsyncSession, *, status: str | None, search: str | None
) -> list[Employee]:
    stmt = select(Employee).order_by(func.lower(Employee.full_name))
    if status:
        stmt = stmt.where(Employee.status == status)
    if search and search.strip():
        term = search.strip()
        stmt = stmt.where(
            or_(
                contains(Employee.full_name, term),
                contains(Employee.full_name_ar, term),
                contains(Employee.personal_number, term),
                contains(Employee.job_title, term),
            )
        )
    return list((await db.execute(stmt)).scalars().all())


async def get_employee(db: AsyncSession, employee_id: uuid.UUID) -> Employee:
    employee = await db.get(Employee, employee_id)
    if employee is None:
        raise NotFoundError("Employee not found")
    return employee


async def _check_user(
    db: AsyncSession, user_id: uuid.UUID | None, *, employee_id: uuid.UUID | None
) -> None:
    """The linked account must exist and not already belong to another employee."""
    if user_id is None:
        return
    if await db.get(User, user_id) is None:
        raise BadRequestError("That account does not exist")
    stmt = select(Employee.full_name).where(Employee.user_id == user_id)
    if employee_id is not None:
        stmt = stmt.where(Employee.id != employee_id)
    other = (await db.execute(stmt)).scalar_one_or_none()
    if other is not None:
        raise ConflictError(f"That account is already linked to {other}")


async def create_employee(db: AsyncSession, data: dict) -> Employee:
    await _check_user(db, data.get("user_id"), employee_id=None)
    employee = Employee(**data)
    db.add(employee)
    await db.flush()
    await db.refresh(employee, ["documents", "user"])
    return employee


async def update_employee(db: AsyncSession, employee: Employee, data: dict) -> Employee:
    if "user_id" in data:
        await _check_user(db, data["user_id"], employee_id=employee.id)
    for field, value in data.items():
        # `None` for a required column means "not sent" in a partial update.
        if value is None and field in {"full_name", "status"}:
            continue
        setattr(employee, field, value)
    await db.flush()
    await db.refresh(employee, ["user"])
    return employee


async def delete_employee(db: AsyncSession, employee: Employee) -> None:
    keys = [d.file_object_key for d in employee.documents if d.file_object_key]
    if employee.photo_object_key:
        keys.append(employee.photo_object_key)
    await db.delete(employee)
    await db.flush()
    for key in keys:
        await storage.delete_quietly(key)


async def store_photo(
    db: AsyncSession, employee: Employee, body: bytes, content_type: str
) -> Employee:
    content_type, ext = storage.validate(
        body, content_type, "Photo", allowed=storage.IMAGE_EXTENSIONS
    )
    key = photo_key(employee, ext)
    await storage.upload(key, body, content_type, "photo")
    previous = employee.photo_object_key
    employee.photo_object_key = key
    employee.photo_content_type = content_type
    await db.flush()
    if previous and previous != key:
        await storage.delete_quietly(previous)
    return employee


async def remove_photo(db: AsyncSession, employee: Employee) -> Employee:
    key = employee.photo_object_key
    employee.photo_object_key = None
    employee.photo_content_type = None
    await db.flush()
    if key:
        await storage.delete_quietly(key)
    return employee


async def photo_file(employee: Employee) -> storage.SignedFile:
    if not employee.photo_object_key:
        raise NotFoundError("This employee has no photo")
    return await storage.sign_file(
        employee.photo_object_key,
        title=f"{employee.full_name} - Photo",
        content_type=employee.photo_content_type,
    )


# ─── Documents ────────────────────────────────────────────────────────────────


def _check_details(doc_type: str, details: dict[str, str]) -> None:
    allowed = EMPLOYEE_DOCUMENT_SPECS[doc_type]["details"]
    unknown = sorted(set(details) - set(allowed))
    if unknown:
        raise BadRequestError(f"Unknown {doc_type} field(s): {', '.join(unknown)}")


async def _cycle_taken(
    db: AsyncSession,
    employee_id: uuid.UUID,
    doc_type: str,
    expiry: date,
    *,
    except_id: uuid.UUID | None,
) -> bool:
    stmt = select(EmployeeDocument.id).where(
        EmployeeDocument.employee_id == employee_id,
        EmployeeDocument.doc_type == doc_type,
        EmployeeDocument.expiry_date == expiry,
    )
    if except_id is not None:
        stmt = stmt.where(EmployeeDocument.id != except_id)
    return (await db.execute(stmt)).first() is not None


def _duplicate(doc_type: str) -> ConflictError:
    label = EMPLOYEE_DOCUMENT_SPECS[doc_type]["label"].lower()
    return ConflictError(f"This employee already has a {label} expiring that day")


async def get_document(
    db: AsyncSession, employee: Employee, document_id: uuid.UUID
) -> EmployeeDocument:
    row = await db.get(EmployeeDocument, document_id)
    if row is None or row.employee_id != employee.id:
        raise NotFoundError("Document not found")
    return row


async def create_document(
    db: AsyncSession, employee: Employee, data: dict
) -> EmployeeDocument:
    if await _cycle_taken(
        db, employee.id, data["doc_type"], data["expiry_date"], except_id=None
    ):
        raise _duplicate(data["doc_type"])
    row = EmployeeDocument(employee_id=employee.id, **data)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    await db.refresh(employee, ["documents"])
    return row


async def update_document(
    db: AsyncSession, employee: Employee, row: EmployeeDocument, data: dict
) -> EmployeeDocument:
    merged_issue = data.get("issue_date", row.issue_date)
    merged_expiry = data.get("expiry_date") or row.expiry_date
    if merged_issue is not None and merged_issue > merged_expiry:
        raise BadRequestError("The issue date must be on or before the expiry date")
    if merged_expiry != row.expiry_date and await _cycle_taken(
        db, employee.id, row.doc_type, merged_expiry, except_id=row.id
    ):
        raise _duplicate(row.doc_type)
    if data.get("details") is not None:
        _check_details(row.doc_type, data["details"])
    for field, value in data.items():
        if value is None and field in {"expiry_date", "details"}:
            continue
        setattr(row, field, value)
    await db.flush()
    await db.refresh(row)
    await db.refresh(employee, ["documents"])
    return row


async def delete_document(
    db: AsyncSession, employee: Employee, row: EmployeeDocument
) -> None:
    key = row.file_object_key
    await db.delete(row)
    await db.flush()
    await db.refresh(employee, ["documents"])
    if key:
        await storage.delete_quietly(key)


async def store_document_file(
    db: AsyncSession, row: EmployeeDocument, body: bytes, content_type: str
) -> EmployeeDocument:
    label = EMPLOYEE_DOCUMENT_SPECS[row.doc_type]["label"]
    content_type, ext = storage.validate(body, content_type, label)
    key = document_key(row, ext)
    await storage.upload(key, body, content_type, label.lower())
    previous = row.file_object_key
    row.file_object_key = key
    row.file_content_type = content_type
    await db.flush()
    if previous and previous != key:
        await storage.delete_quietly(previous)
    return row


async def remove_document_file(
    db: AsyncSession, row: EmployeeDocument
) -> EmployeeDocument:
    key = row.file_object_key
    row.file_object_key = None
    row.file_content_type = None
    await db.flush()
    if key:
        await storage.delete_quietly(key)
    return row


async def document_file(
    employee: Employee, row: EmployeeDocument
) -> storage.SignedFile:
    if not row.file_object_key:
        raise NotFoundError("This document has no file")
    label = EMPLOYEE_DOCUMENT_SPECS[row.doc_type]["label"]
    return await storage.sign_file(
        row.file_object_key,
        title=f"{employee.full_name} - {label} - expires {row.expiry_date.isoformat()}",
        content_type=row.file_content_type,
    )
