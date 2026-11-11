"""A legal entity's registration paperwork: its trade-licence cycles and its VAT
certificate.

A trade licence is renewed under the same number every year or few, and each
renewal is a new document. Every issue→expiry cycle is kept as its own row
(`legal_entity_trade_licenses`) with its own scan, so the licence that was valid
on any date is always on file. The cycle that expires last is the current one,
and `sync_current_license` mirrors its number and expiry onto the entity — the
only writer of `legal_entities.trade_license_number/expiry`, which the invoice
and the admin list read.

Documents (licence scans, the VAT certificate) live in the private finance
bucket under a deterministic key per entity, signed on read and never public —
the same handling a supplier's registration documents get. A key is
`legal-entities/<reference>/…`, so a human browsing the bucket can tell whose
document it is.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import file_sniff, object_storage
from app.core import private_documents as storage
from app.core.config import settings
from app.core.exceptions import (
    BadGatewayError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.models.legal_entity import LegalEntity, LegalEntityTradeLicense

logger = logging.getLogger(__name__)

DOCUMENT_MAX_BYTES = file_sniff.MAX_BYTES


# ─── Keys ─────────────────────────────────────────────────────────────────────


def vat_certificate_key(entity: LegalEntity, ext: str) -> str:
    return f"legal-entities/{entity.reference}/vat-certificate{ext}"


def trade_license_key(entity: LegalEntity, expiry: date, ext: str) -> str:
    """One object per cycle, named by the cycle's expiry (unique per entity)."""
    return f"legal-entities/{entity.reference}/trade-licenses/{expiry.isoformat()}{ext}"


# ─── Trade-licence cycles ─────────────────────────────────────────────────────


async def list_licenses(
    db: AsyncSession, entity: LegalEntity
) -> list[LegalEntityTradeLicense]:
    """Every cycle for the entity, newest expiry first (the current one leads)."""
    rows = await db.execute(
        select(LegalEntityTradeLicense)
        .where(LegalEntityTradeLicense.legal_entity_id == entity.id)
        .order_by(LegalEntityTradeLicense.expiry_date.desc())
    )
    return list(rows.scalars())


async def get_license(
    db: AsyncSession, entity: LegalEntity, license_id: uuid.UUID
) -> LegalEntityTradeLicense:
    row = await db.get(LegalEntityTradeLicense, license_id)
    if row is None or row.legal_entity_id != entity.id:
        raise NotFoundError("Trade licence not found")
    return row


async def sync_current_license(db: AsyncSession, entity: LegalEntity) -> None:
    """Mirror the cycle that expires last onto the entity (or clear it)."""
    cycles = await list_licenses(db, entity)
    current = cycles[0] if cycles else None
    entity.trade_license_number = current.license_number if current else None
    entity.trade_license_expiry = current.expiry_date if current else None
    await db.flush()


async def _expiry_taken(
    db: AsyncSession, entity: LegalEntity, expiry: date, *, except_id: uuid.UUID | None
) -> bool:
    stmt = select(LegalEntityTradeLicense.id).where(
        LegalEntityTradeLicense.legal_entity_id == entity.id,
        LegalEntityTradeLicense.expiry_date == expiry,
    )
    if except_id is not None:
        stmt = stmt.where(LegalEntityTradeLicense.id != except_id)
    return (await db.execute(stmt.limit(1))).first() is not None


async def create_license(
    db: AsyncSession, entity: LegalEntity, data: dict
) -> LegalEntityTradeLicense:
    if await _expiry_taken(db, entity, data["expiry_date"], except_id=None):
        raise ConflictError("This entity already has a trade licence expiring that day")
    row = LegalEntityTradeLicense(legal_entity_id=entity.id, **data)
    db.add(row)
    await db.flush()
    await sync_current_license(db, entity)
    await db.refresh(row)
    return row


async def update_license(
    db: AsyncSession, entity: LegalEntity, row: LegalEntityTradeLicense, data: dict
) -> LegalEntityTradeLicense:
    merged_issue = data.get("issue_date", row.issue_date)
    merged_expiry = data.get("expiry_date") or row.expiry_date
    if merged_issue is not None and merged_issue > merged_expiry:
        raise BadRequestError("The issue date must be on or before the expiry date")
    if merged_expiry != row.expiry_date and await _expiry_taken(
        db, entity, merged_expiry, except_id=row.id
    ):
        raise ConflictError("This entity already has a trade licence expiring that day")
    for field, value in data.items():
        # `None` for a required column means "not sent" in a partial update.
        if value is None and field in {
            "license_number",
            "issuing_authority",
            "emirate",
            "expiry_date",
        }:
            continue
        setattr(row, field, value)
    await db.flush()
    await sync_current_license(db, entity)
    await db.refresh(row)
    return row


async def delete_license(
    db: AsyncSession, entity: LegalEntity, row: LegalEntityTradeLicense
) -> None:
    key = row.document_object_key
    await db.delete(row)
    await db.flush()
    await sync_current_license(db, entity)
    if key:
        await _delete_quietly(key)


async def store_license_document(
    db: AsyncSession,
    entity: LegalEntity,
    row: LegalEntityTradeLicense,
    body: bytes,
    content_type: str,
) -> LegalEntityTradeLicense:
    content_type, ext = file_sniff.resolve(body, content_type, "Trade licence")
    key = trade_license_key(entity, row.expiry_date, ext)
    await _upload(key, body, content_type, "trade licence")
    previous = row.document_object_key
    row.document_object_key = key
    row.document_content_type = content_type
    await db.flush()
    if previous and previous != key:
        await _delete_quietly(previous)
    await db.refresh(row)
    return row


async def remove_license_document(
    db: AsyncSession, row: LegalEntityTradeLicense
) -> LegalEntityTradeLicense:
    key = row.document_object_key
    row.document_object_key = None
    row.document_content_type = None
    await db.flush()
    if key:
        await _delete_quietly(key)
    await db.refresh(row)
    return row


async def license_document(
    entity: LegalEntity, row: LegalEntityTradeLicense
) -> storage.SignedFile:
    if not row.document_object_key:
        raise NotFoundError("This trade licence has no document")
    return await storage.sign_file(
        row.document_object_key,
        title=(
            f"{entity.legal_name} - Trade licence {row.license_number}"
            f" - expires {row.expiry_date.isoformat()}"
        ),
        content_type=row.document_content_type,
    )


# ─── VAT certificate ──────────────────────────────────────────────────────────


async def store_vat_certificate(
    db: AsyncSession, entity: LegalEntity, body: bytes, content_type: str
) -> LegalEntity:
    content_type, ext = file_sniff.resolve(body, content_type, "VAT certificate")
    key = vat_certificate_key(entity, ext)
    await _upload(key, body, content_type, "VAT certificate")
    previous = entity.vat_certificate_object_key
    entity.vat_certificate_object_key = key
    entity.vat_certificate_content_type = content_type
    await db.flush()
    if previous and previous != key:
        await _delete_quietly(previous)
    await db.refresh(entity)
    return entity


async def remove_vat_certificate(db: AsyncSession, entity: LegalEntity) -> LegalEntity:
    key = entity.vat_certificate_object_key
    entity.vat_certificate_object_key = None
    entity.vat_certificate_content_type = None
    await db.flush()
    if key:
        await _delete_quietly(key)
    await db.refresh(entity)
    return entity


async def vat_certificate(entity: LegalEntity) -> storage.SignedFile:
    if not entity.vat_certificate_object_key:
        raise NotFoundError("This entity has no VAT certificate on file")
    return await storage.sign_file(
        entity.vat_certificate_object_key,
        title=f"{entity.legal_name} - VAT certificate",
        content_type=entity.vat_certificate_content_type,
    )


# ─── Storage plumbing ─────────────────────────────────────────────────────────


async def _upload(key: str, body: bytes, content_type: str, label: str) -> None:
    try:
        # Blocking GCS network I/O — keep it off the event loop.
        await asyncio.to_thread(
            object_storage.upload_object,
            bucket=settings.GCS_INVOICE_BUCKET,
            key=key,
            body=body,
            content_type=content_type,
            cache_control="private, no-store",
        )
    except Exception as exc:
        raise BadGatewayError(f"Failed to store the {label}") from exc


async def _delete_quietly(key: str) -> None:
    """Delete an object we no longer point at. The row is already right; a failed
    delete only leaves an unreferenced object in a private bucket, so log it
    rather than fail the request."""
    try:
        await asyncio.to_thread(
            object_storage.delete_object, bucket=settings.GCS_INVOICE_BUCKET, key=key
        )
    except Exception:
        logger.warning("could not delete legal-entity document %s", key, exc_info=True)
