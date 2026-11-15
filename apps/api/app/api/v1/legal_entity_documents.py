"""A legal entity's registration paperwork: trade-licence history, VAT
certificate, the other registration documents (establishment card, labour
company card, chamber membership, Corporate Tax, MoA, the shop's permits) and
other files.

Mounted under `/legal-entities` *before* the generic CRUD router, so the static
paths here (`/document-types`, `/document-overview`, `/file-categories`) are
matched before its `/{id}`. Everything here is admin-only
(`admin.settings.manage`), reads included: a trade licence lists the owners'
Emirates ID / passport numbers, so — unlike the entity itself, which the till
reads at launch — none of it is served to the register.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import private_documents
from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.permissions import require
from app.models import LegalEntity, LegalEntityTradeLicense
from app.models.legal_entity import LEGAL_ENTITY_DOCUMENT_SPECS
from app.models.user import User
from app.schemas.legal_entity_paperwork import (
    LegalEntityDocumentCreate,
    LegalEntityDocumentResponse,
    LegalEntityDocumentTypeSpec,
    LegalEntityDocumentUpdate,
    LegalEntityFileCreate,
    LegalEntityFileResponse,
    LegalEntityFileUpdate,
    LegalEntityPaperwork,
    LegalEntityPaperworkOverview,
)
from app.schemas.pos import (
    LegalEntityDocumentUrl,
    LegalEntityResponse,
    TradeLicenseCreate,
    TradeLicenseResponse,
    TradeLicenseUpdate,
)
from app.services import audit_service, crud_service
from app.services.orders import legal_entity_documents as docs
from app.services.people import legal_entity_paperwork as paper

router = APIRouter()


def _signed(f: private_documents.SignedFile) -> LegalEntityDocumentUrl:
    return LegalEntityDocumentUrl(
        url=f.url,
        download_url=f.download_url,
        filename=f.filename,
        content_type=f.content_type,
    )


_MANAGE = "admin.settings.manage"


async def _entity(db: AsyncSession, entity_id: uuid.UUID) -> LegalEntity:
    return await crud_service.get_or_404(db, LegalEntity, entity_id)


def _response(
    entity: LegalEntity, row: LegalEntityTradeLicense
) -> TradeLicenseResponse:
    payload = TradeLicenseResponse.model_validate(row)
    payload.is_current = entity.trade_license_expiry == row.expiry_date
    return payload


async def _read_body(request: Request) -> tuple[bytes, str]:
    # Refuse an oversized upload before reading the body into memory.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > docs.DOCUMENT_MAX_BYTES:
        raise BadRequestError("Document file is too large (max 10 MB)")
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    return await request.body(), content_type


async def _audit(
    db: AsyncSession,
    request: Request,
    admin: User,
    entity: LegalEntity,
    action: str,
    changes: dict,
) -> None:
    await audit_service.log_action(
        db,
        action=action,
        entity_type="legal_entity",
        entity_id=str(entity.id),
        entity_label=entity.legal_name,
        admin=admin,
        changes=changes,
        request=request,
    )


# ─── Reference + overview (static paths: keep above every `/{entity_id}`) ─────


@router.get("/document-types", response_model=list[LegalEntityDocumentTypeSpec])
async def document_types(_: User = Depends(require(_MANAGE))):
    """The registration document types besides the trade licence and VAT
    certificate, their labels and fields, in the console's order."""
    return [
        LegalEntityDocumentTypeSpec(doc_type=t, **spec)
        for t, spec in LEGAL_ENTITY_DOCUMENT_SPECS.items()
    ]


@router.get("/document-overview", response_model=list[LegalEntityPaperworkOverview])
async def document_overview(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """Each entity's current document per type — the list's chips. An
    entity with nothing on file is absent."""
    return await paper.overview(db)


@router.get("/file-categories", response_model=list[str])
async def file_categories(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """The types already given to other entity files, most used first."""
    return await paper.file_categories(db)


# ─── Trade-licence cycles ─────────────────────────────────────────────────────


@router.get("/{entity_id}/trade-licenses", response_model=list[TradeLicenseResponse])
async def list_trade_licenses(
    entity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """Every issue→expiry cycle of the entity's trade licence, newest first."""
    entity = await _entity(db, entity_id)
    return [_response(entity, row) for row in await docs.list_licenses(db, entity)]


@router.post(
    "/{entity_id}/trade-licenses",
    response_model=TradeLicenseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_trade_license(
    entity_id: uuid.UUID,
    data: TradeLicenseCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Record a licence cycle (a renewal adds a new one; earlier ones are kept)."""
    entity = await _entity(db, entity_id)
    row = await docs.create_license(db, entity, data.model_dump())
    await _audit(
        db,
        request,
        admin,
        entity,
        "UPDATE",
        {"trade_license_added": data.model_dump(mode="json")},
    )
    return _response(entity, row)


@router.patch(
    "/{entity_id}/trade-licenses/{license_id}", response_model=TradeLicenseResponse
)
async def update_trade_license(
    entity_id: uuid.UUID,
    license_id: uuid.UUID,
    data: TradeLicenseUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await docs.get_license(db, entity, license_id)
    changes = data.model_dump(exclude_unset=True)
    row = await docs.update_license(db, entity, row, changes)
    await _audit(
        db,
        request,
        admin,
        entity,
        "UPDATE",
        {
            "trade_license_updated": str(license_id),
            "data": data.model_dump(mode="json", exclude_unset=True),
        },
    )
    return _response(entity, row)


@router.delete(
    "/{entity_id}/trade-licenses/{license_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_trade_license(
    entity_id: uuid.UUID,
    license_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Remove a cycle recorded by mistake, and its document."""
    entity = await _entity(db, entity_id)
    row = await docs.get_license(db, entity, license_id)
    summary = {
        "license_number": row.license_number,
        "expiry_date": row.expiry_date.isoformat(),
    }
    await docs.delete_license(db, entity, row)
    await _audit(
        db, request, admin, entity, "UPDATE", {"trade_license_deleted": summary}
    )


@router.post(
    "/{entity_id}/trade-licenses/{license_id}/document",
    response_model=TradeLicenseResponse,
)
async def upload_trade_license_document(
    entity_id: uuid.UUID,
    license_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Attach this cycle's licence scan. The body is the raw file bytes (JPEG,
    PNG, WebP or PDF, max 10 MB); the type comes from the request header."""
    entity = await _entity(db, entity_id)
    row = await docs.get_license(db, entity, license_id)
    body, content_type = await _read_body(request)
    row = await docs.store_license_document(db, entity, row, body, content_type)
    await _audit(
        db,
        request,
        admin,
        entity,
        "UPDATE",
        {"trade_license_document": str(license_id)},
    )
    return _response(entity, row)


@router.get(
    "/{entity_id}/trade-licenses/{license_id}/document",
    response_model=LegalEntityDocumentUrl,
)
async def get_trade_license_document(
    entity_id: uuid.UUID,
    license_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """A short-lived signed URL to view or download this cycle's licence."""
    entity = await _entity(db, entity_id)
    row = await docs.get_license(db, entity, license_id)
    return _signed(await docs.license_document(entity, row))


@router.delete(
    "/{entity_id}/trade-licenses/{license_id}/document",
    response_model=TradeLicenseResponse,
)
async def delete_trade_license_document(
    entity_id: uuid.UUID,
    license_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await docs.get_license(db, entity, license_id)
    row = await docs.remove_license_document(db, row)
    await _audit(
        db,
        request,
        admin,
        entity,
        "UPDATE",
        {"trade_license_document_removed": str(license_id)},
    )
    return _response(entity, row)


# ─── VAT certificate ──────────────────────────────────────────────────────────


@router.post("/{entity_id}/vat-certificate", response_model=LegalEntityResponse)
async def upload_vat_certificate(
    entity_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Attach the FTA VAT registration certificate, replacing any earlier one."""
    entity = await _entity(db, entity_id)
    body, content_type = await _read_body(request)
    entity = await docs.store_vat_certificate(db, entity, body, content_type)
    await _audit(db, request, admin, entity, "UPDATE", {"vat_certificate": "uploaded"})
    return entity


@router.get("/{entity_id}/vat-certificate", response_model=LegalEntityDocumentUrl)
async def get_vat_certificate(
    entity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    return _signed(await docs.vat_certificate(entity))


@router.delete("/{entity_id}/vat-certificate", response_model=LegalEntityResponse)
async def delete_vat_certificate(
    entity_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    entity = await docs.remove_vat_certificate(db, entity)
    await _audit(db, request, admin, entity, "UPDATE", {"vat_certificate": "removed"})
    return entity


# ─── Other registration documents ─────────────────────────────────────────────


def _doc_summary(row) -> dict:
    return {
        "doc_type": row.doc_type,
        "document_number": row.document_number,
        "expiry_date": row.expiry_date.isoformat() if row.expiry_date else None,
    }


async def _doc_response(
    db: AsyncSession, entity: LegalEntity, row
) -> LegalEntityDocumentResponse:
    current = paper.current_by_type(await paper.documents_of(db, entity))
    return paper.to_document_response(row, current)


@router.get("/{entity_id}/paperwork", response_model=LegalEntityPaperwork)
async def get_paperwork(
    entity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """Every cycle of every document, and the other files."""
    return await paper.paperwork(db, await _entity(db, entity_id))


@router.post(
    "/{entity_id}/documents",
    response_model=LegalEntityDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    entity_id: uuid.UUID,
    data: LegalEntityDocumentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Record a document cycle. A renewal adds a new one; earlier ones are kept."""
    entity = await _entity(db, entity_id)
    row = await paper.create_document(db, entity, data.model_dump())
    await _audit(
        db, request, admin, entity, "UPDATE", {"document_added": _doc_summary(row)}
    )
    return await _doc_response(db, entity, row)


@router.patch(
    "/{entity_id}/documents/{document_id}",
    response_model=LegalEntityDocumentResponse,
)
async def update_document(
    entity_id: uuid.UUID,
    document_id: uuid.UUID,
    data: LegalEntityDocumentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await paper.get_document(db, entity, document_id)
    row = await paper.update_document(
        db, entity, row, data.model_dump(exclude_unset=True)
    )
    await _audit(
        db,
        request,
        admin,
        entity,
        "UPDATE",
        {
            "document_updated": str(document_id),
            "data": data.model_dump(mode="json", exclude_unset=True),
        },
    )
    return await _doc_response(db, entity, row)


@router.delete(
    "/{entity_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document(
    entity_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Remove a cycle recorded by mistake, and its scan."""
    entity = await _entity(db, entity_id)
    row = await paper.get_document(db, entity, document_id)
    summary = _doc_summary(row)
    await paper.delete_document(db, row)
    await _audit(db, request, admin, entity, "UPDATE", {"document_deleted": summary})


@router.post(
    "/{entity_id}/documents/{document_id}/file",
    response_model=LegalEntityDocumentResponse,
)
async def upload_document_file(
    entity_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Attach this cycle's scan. The body is the raw file bytes (JPEG, PNG,
    WebP or PDF, max 10 MB); the type comes from the request header."""
    entity = await _entity(db, entity_id)
    row = await paper.get_document(db, entity, document_id)
    body, content_type = await _read_body(request)
    row = await paper.store_document_file(db, entity, row, body, content_type)
    await _audit(
        db, request, admin, entity, "UPDATE", {"document_file": str(document_id)}
    )
    return await _doc_response(db, entity, row)


@router.get(
    "/{entity_id}/documents/{document_id}/file",
    response_model=LegalEntityDocumentUrl,
)
async def get_document_file(
    entity_id: uuid.UUID,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await paper.get_document(db, entity, document_id)
    return _signed(await paper.document_file(entity, row))


@router.delete(
    "/{entity_id}/documents/{document_id}/file",
    response_model=LegalEntityDocumentResponse,
)
async def delete_document_file(
    entity_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await paper.get_document(db, entity, document_id)
    row = await paper.remove_document_file(db, row)
    await _audit(
        db,
        request,
        admin,
        entity,
        "UPDATE",
        {"document_file_removed": str(document_id)},
    )
    return await _doc_response(db, entity, row)


# ─── Other files ──────────────────────────────────────────────────────────────


def _file_summary(row) -> dict:
    return {"title": row.title, "category": row.category}


@router.post(
    "/{entity_id}/files",
    response_model=LegalEntityFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_file(
    entity_id: uuid.UUID,
    data: LegalEntityFileCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Record another paper kept against the entity; its file is uploaded next,
    to `/{entity_id}/files/{file_id}/file`."""
    entity = await _entity(db, entity_id)
    row = await paper.create_file(db, entity, data.model_dump())
    await _audit(
        db, request, admin, entity, "UPDATE", {"file_added": _file_summary(row)}
    )
    return row


@router.patch("/{entity_id}/files/{file_id}", response_model=LegalEntityFileResponse)
async def update_file(
    entity_id: uuid.UUID,
    file_id: uuid.UUID,
    data: LegalEntityFileUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await paper.get_file(db, entity, file_id)
    row = await paper.update_file(db, row, data.model_dump(exclude_unset=True))
    await _audit(
        db,
        request,
        admin,
        entity,
        "UPDATE",
        {
            "file_updated": str(file_id),
            "data": data.model_dump(mode="json", exclude_unset=True),
        },
    )
    return row


@router.delete("/{entity_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    entity_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await paper.get_file(db, entity, file_id)
    summary = _file_summary(row)
    await paper.delete_file(db, row)
    await _audit(db, request, admin, entity, "UPDATE", {"file_deleted": summary})


@router.post(
    "/{entity_id}/files/{file_id}/file", response_model=LegalEntityFileResponse
)
async def upload_file(
    entity_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Attach or replace the file. The body is the raw bytes (JPEG, PNG, WebP
    or PDF, max 10 MB); the type comes from the request header."""
    entity = await _entity(db, entity_id)
    row = await paper.get_file(db, entity, file_id)
    body, content_type = await _read_body(request)
    row = await paper.store_file(db, entity, row, body, content_type)
    await _audit(db, request, admin, entity, "UPDATE", {"file_uploaded": str(file_id)})
    return row


@router.get("/{entity_id}/files/{file_id}/file", response_model=LegalEntityDocumentUrl)
async def get_file(
    entity_id: uuid.UUID,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    entity = await _entity(db, entity_id)
    row = await paper.get_file(db, entity, file_id)
    return _signed(await paper.signed_file(entity, row))
