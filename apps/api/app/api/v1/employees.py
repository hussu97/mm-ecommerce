"""Employees and their versioned documents — super-admin only, reads included.

These records hold passport, visa, Emirates ID and labour-card numbers and scans, so every
route demands `require_superadmin()`: no role slug opens them, however broad.
Document field values are typed by the super-admin, usually prefilled by the
console's in-browser OCR of the scan — the API never reads the image itself.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import private_documents
from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.permissions import require_superadmin
from app.models.employee import (
    EMPLOYEE_DOCUMENT_SPECS,
    EMPLOYEE_DOCUMENT_TYPES,
    EXPIRY_TRACKED_TYPES,
    Employee,
)
from app.models.user import User
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeDocumentCreate,
    EmployeeDocumentResponse,
    EmployeeDocumentTypeSpec,
    EmployeeDocumentUpdate,
    EmployeeListItem,
    EmployeeResponse,
    EmployeeUpdate,
    SignedDocumentUrl,
)
from app.services import audit_service
from app.services.people import employee_service as svc

router = APIRouter()


def _signed(f: private_documents.SignedFile) -> SignedDocumentUrl:
    return SignedDocumentUrl(
        url=f.url,
        download_url=f.download_url,
        filename=f.filename,
        content_type=f.content_type,
    )


async def _read_body(request: Request) -> tuple[bytes, str]:
    # Refuse an oversized upload before reading the body into memory.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > private_documents.MAX_BYTES:
        raise BadRequestError("File is too large (max 10 MB)")
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    return await request.body(), content_type


async def _audit(
    db: AsyncSession,
    request: Request,
    admin: User,
    employee: Employee,
    action: str,
    changes: dict,
) -> None:
    await audit_service.log_action(
        db,
        action=action,
        entity_type="employee",
        entity_id=str(employee.id),
        entity_label=employee.full_name,
        admin=admin,
        changes=changes,
        request=request,
    )


# ─── Reference ────────────────────────────────────────────────────────────────


@router.get("/document-types", response_model=list[EmployeeDocumentTypeSpec])
async def document_types(_: User = Depends(require_superadmin())):
    """The document types, their labels and type-specific fields, in order."""
    return [
        EmployeeDocumentTypeSpec(
            doc_type=t,
            label=EMPLOYEE_DOCUMENT_SPECS[t]["label"],
            number_label=EMPLOYEE_DOCUMENT_SPECS[t]["number_label"],
            expiry_tracked=t in EXPIRY_TRACKED_TYPES,
            details=EMPLOYEE_DOCUMENT_SPECS[t]["details"],
        )
        for t in EMPLOYEE_DOCUMENT_TYPES
    ]


# ─── Employees ────────────────────────────────────────────────────────────────


@router.get("", response_model=list[EmployeeListItem])
async def list_employees(
    status_filter: Literal["active", "left", "all"] = Query("active", alias="status"),
    search: str | None = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin()),
):
    """Employees with each document type's current cycle. A handful of rows
    (one shop's staff), so not paginated."""
    rows = await svc.list_employees(
        db, status=None if status_filter == "all" else status_filter, search=search
    )
    return [svc.to_list_item(e) for e in rows]


@router.post("", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    data: EmployeeCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    employee = await svc.create_employee(db, data.model_dump())
    await _audit(
        db, request, admin, employee, "CREATE", {"full_name": employee.full_name}
    )
    return svc.to_response(employee)


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin()),
):
    """The employee and every cycle of every document, newest first per type."""
    return svc.to_response(await svc.get_employee(db, employee_id))


@router.patch("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: uuid.UUID,
    data: EmployeeUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    employee = await svc.get_employee(db, employee_id)
    changes = data.model_dump(exclude_unset=True)
    employee = await svc.update_employee(db, employee, changes)
    await _audit(
        db,
        request,
        admin,
        employee,
        "UPDATE",
        data.model_dump(mode="json", exclude_unset=True),
    )
    return svc.to_response(employee)


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_employee(
    employee_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    """Remove an employee entered by mistake, with every document and scan.
    Someone who has left is marked `left` instead, which keeps the history."""
    employee = await svc.get_employee(db, employee_id)
    await _audit(
        db, request, admin, employee, "DELETE", {"full_name": employee.full_name}
    )
    await svc.delete_employee(db, employee)


# ─── Photo ────────────────────────────────────────────────────────────────────


@router.post("/{employee_id}/photo", response_model=EmployeeResponse)
async def upload_photo(
    employee_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    """The body is the raw image bytes (JPEG, PNG or WebP, max 10 MB)."""
    employee = await svc.get_employee(db, employee_id)
    body, content_type = await _read_body(request)
    employee = await svc.store_photo(db, employee, body, content_type)
    await _audit(db, request, admin, employee, "UPDATE", {"photo": "uploaded"})
    return svc.to_response(employee)


@router.get("/{employee_id}/photo", response_model=SignedDocumentUrl)
async def get_photo(
    employee_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin()),
):
    employee = await svc.get_employee(db, employee_id)
    return _signed(await svc.photo_file(employee))


@router.delete("/{employee_id}/photo", response_model=EmployeeResponse)
async def delete_photo(
    employee_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    employee = await svc.get_employee(db, employee_id)
    employee = await svc.remove_photo(db, employee)
    await _audit(db, request, admin, employee, "UPDATE", {"photo": "removed"})
    return svc.to_response(employee)


# ─── Documents ────────────────────────────────────────────────────────────────


def _doc_summary(row) -> dict:
    return {
        "doc_type": row.doc_type,
        "document_number": row.document_number,
        "expiry_date": row.expiry_date.isoformat(),
    }


@router.post(
    "/{employee_id}/documents",
    response_model=EmployeeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    employee_id: uuid.UUID,
    data: EmployeeDocumentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    """Record a document cycle. A renewal adds a new one; earlier ones are kept."""
    employee = await svc.get_employee(db, employee_id)
    row = await svc.create_document(db, employee, data.model_dump())
    await _audit(
        db, request, admin, employee, "UPDATE", {"document_added": _doc_summary(row)}
    )
    return svc.to_document_response(row, svc.current_by_type(employee.documents))


@router.patch(
    "/{employee_id}/documents/{document_id}", response_model=EmployeeDocumentResponse
)
async def update_document(
    employee_id: uuid.UUID,
    document_id: uuid.UUID,
    data: EmployeeDocumentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    employee = await svc.get_employee(db, employee_id)
    row = await svc.get_document(db, employee, document_id)
    row = await svc.update_document(
        db, employee, row, data.model_dump(exclude_unset=True)
    )
    await _audit(
        db,
        request,
        admin,
        employee,
        "UPDATE",
        {
            "document_updated": str(document_id),
            "data": data.model_dump(mode="json", exclude_unset=True),
        },
    )
    return svc.to_document_response(row, svc.current_by_type(employee.documents))


@router.delete(
    "/{employee_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document(
    employee_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    """Remove a cycle recorded by mistake, and its scan."""
    employee = await svc.get_employee(db, employee_id)
    row = await svc.get_document(db, employee, document_id)
    summary = _doc_summary(row)
    await svc.delete_document(db, employee, row)
    await _audit(db, request, admin, employee, "UPDATE", {"document_deleted": summary})


@router.post(
    "/{employee_id}/documents/{document_id}/file",
    response_model=EmployeeDocumentResponse,
)
async def upload_document_file(
    employee_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    """Attach this cycle's scan. The body is the raw file bytes (JPEG, PNG,
    WebP or PDF, max 10 MB); the type comes from the request header."""
    employee = await svc.get_employee(db, employee_id)
    row = await svc.get_document(db, employee, document_id)
    body, content_type = await _read_body(request)
    row = await svc.store_document_file(db, row, body, content_type)
    await _audit(
        db, request, admin, employee, "UPDATE", {"document_file": str(document_id)}
    )
    return svc.to_document_response(row, svc.current_by_type(employee.documents))


@router.get(
    "/{employee_id}/documents/{document_id}/file", response_model=SignedDocumentUrl
)
async def get_document_file(
    employee_id: uuid.UUID,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin()),
):
    employee = await svc.get_employee(db, employee_id)
    row = await svc.get_document(db, employee, document_id)
    return _signed(await svc.document_file(employee, row))


@router.delete(
    "/{employee_id}/documents/{document_id}/file",
    response_model=EmployeeDocumentResponse,
)
async def delete_document_file(
    employee_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superadmin()),
):
    employee = await svc.get_employee(db, employee_id)
    row = await svc.get_document(db, employee, document_id)
    row = await svc.remove_document_file(db, row)
    await _audit(
        db,
        request,
        admin,
        employee,
        "UPDATE",
        {"document_file_removed": str(document_id)},
    )
    return svc.to_document_response(row, svc.current_by_type(employee.documents))
