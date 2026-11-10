"""A legal entity's registration paperwork: trade-licence history + VAT certificate.

Mounted under `/legal-entities` beside the generic CRUD router. Everything here
is admin-only (`admin.settings.manage`), reads included: a trade licence lists
the owners' Emirates ID / passport numbers, so — unlike the entity itself, which
the till reads at launch — none of it is served to the register.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.permissions import require
from app.models import LegalEntity, LegalEntityTradeLicense
from app.models.user import User
from app.schemas.pos import (
    LegalEntityDocumentUrl,
    LegalEntityResponse,
    TradeLicenseCreate,
    TradeLicenseResponse,
    TradeLicenseUpdate,
)
from app.services import audit_service, crud_service
from app.services.orders import legal_entity_documents as docs

router = APIRouter()

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
    return LegalEntityDocumentUrl(
        url=await docs.license_document_url(row),
        content_type=row.document_content_type,
    )


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
    return LegalEntityDocumentUrl(
        url=await docs.vat_certificate_url(entity),
        content_type=entity.vat_certificate_content_type,
    )


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
