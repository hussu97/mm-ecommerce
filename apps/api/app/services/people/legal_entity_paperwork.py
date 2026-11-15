"""A legal entity's registration documents (besides the trade licence and VAT
certificate) and its other files.

A document is one issue→expiry cycle of one type. A renewal is a new row, never
an edit of the old one; the cycle expiring last per type is current. Each
entity manages one shop, so its premises documents (tenancy, food permit, Civil
Defence…) are one per entity like the rest. A type issued once (Corporate Tax,
MoA) has no expiry; its latest issued is current.

Scans live in the private finance bucket under
`legal-entities/<reference>/…`, beside the trade-licence scans, signed on read.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import private_documents as storage
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.legal_entity import (
    LEGAL_ENTITY_DOCUMENT_SPECS,
    LegalEntity,
    LegalEntityDocument,
    LegalEntityFile,
)
from app.schemas.legal_entity_paperwork import (
    LegalEntityDocumentResponse,
    LegalEntityDocumentSummary,
    LegalEntityFileResponse,
    LegalEntityPaperwork,
    LegalEntityPaperworkOverview,
)

# ─── Keys ─────────────────────────────────────────────────────────────────────


def document_key(entity: LegalEntity, row: LegalEntityDocument, ext: str) -> str:
    """One object per row: a type issued once has no expiry to name it by."""
    return f"legal-entities/{entity.reference}/{row.doc_type}/{row.id}{ext}"


def file_key(entity: LegalEntity, row: LegalEntityFile, ext: str) -> str:
    return f"legal-entities/{entity.reference}/files/{row.id}{ext}"


# ─── Read models ──────────────────────────────────────────────────────────────


def _recency(doc: LegalEntityDocument) -> tuple:
    """Higher is more current: the latest expiry, else (issued once) the
    latest issued, then the latest entered."""
    return (
        doc.expiry_date or date.min,
        doc.issue_date or date.min,
        doc.created_at,
    )


def current_by_type(
    documents: list[LegalEntityDocument],
) -> dict[str, LegalEntityDocument]:
    current: dict[str, LegalEntityDocument] = {}
    for doc in documents:
        held = current.get(doc.doc_type)
        if held is None or _recency(doc) > _recency(held):
            current[doc.doc_type] = doc
    return current


def to_document_response(
    row: LegalEntityDocument,
    current: dict[str, LegalEntityDocument],
) -> LegalEntityDocumentResponse:
    payload = LegalEntityDocumentResponse.model_validate(row)
    held = current.get(row.doc_type)
    payload.is_current = held is not None and held.id == row.id
    return payload


async def _documents(
    db: AsyncSession, entity_ids: list[uuid.UUID] | None = None
) -> list[LegalEntityDocument]:
    stmt = select(LegalEntityDocument)
    if entity_ids is not None:
        stmt = stmt.where(LegalEntityDocument.legal_entity_id.in_(entity_ids))
    return list((await db.execute(stmt)).scalars())


async def documents_of(
    db: AsyncSession, entity: LegalEntity
) -> list[LegalEntityDocument]:
    return await _documents(db, [entity.id])


async def paperwork(db: AsyncSession, entity: LegalEntity) -> LegalEntityPaperwork:
    docs = await documents_of(db, entity)
    current = current_by_type(docs)
    files = (
        await db.execute(
            select(LegalEntityFile)
            .where(LegalEntityFile.legal_entity_id == entity.id)
            .order_by(LegalEntityFile.created_at.desc())
        )
    ).scalars()
    return LegalEntityPaperwork(
        documents=[
            to_document_response(d, current)
            for d in sorted(docs, key=lambda d: (d.doc_type, _recency(d)), reverse=True)
        ],
        files=[LegalEntityFileResponse.model_validate(f) for f in files],
    )


async def overview(db: AsyncSession) -> list[LegalEntityPaperworkOverview]:
    """The current cycle per type of every entity, for the list."""
    by_entity: dict[uuid.UUID, list[LegalEntityDocument]] = {}
    for doc in await _documents(db):
        by_entity.setdefault(doc.legal_entity_id, []).append(doc)
    return [
        LegalEntityPaperworkOverview(
            legal_entity_id=entity_id,
            current_documents=[
                LegalEntityDocumentSummary.model_validate(d)
                for d in current_by_type(docs).values()
            ],
        )
        for entity_id, docs in by_entity.items()
    ]


# ─── Documents ────────────────────────────────────────────────────────────────


def _label(doc_type: str) -> str:
    return LEGAL_ENTITY_DOCUMENT_SPECS[doc_type]["label"]


def _check_details(doc_type: str, details: dict[str, str]) -> None:
    allowed = LEGAL_ENTITY_DOCUMENT_SPECS[doc_type]["details"]
    unknown = sorted(set(details) - set(allowed))
    if unknown:
        raise BadRequestError(f"Unknown {doc_type} field(s): {', '.join(unknown)}")


async def _cycle_taken(
    db: AsyncSession,
    entity: LegalEntity,
    doc_type: str,
    expiry: date | None,
    *,
    except_id: uuid.UUID | None,
) -> bool:
    if expiry is None:
        return False
    stmt = select(LegalEntityDocument.id).where(
        LegalEntityDocument.legal_entity_id == entity.id,
        LegalEntityDocument.doc_type == doc_type,
        LegalEntityDocument.expiry_date == expiry,
    )
    if except_id is not None:
        stmt = stmt.where(LegalEntityDocument.id != except_id)
    return (await db.execute(stmt.limit(1))).first() is not None


def _duplicate(doc_type: str) -> ConflictError:
    return ConflictError(
        f"There is already a {_label(doc_type).lower()} expiring that day"
    )


async def get_document(
    db: AsyncSession, entity: LegalEntity, document_id: uuid.UUID
) -> LegalEntityDocument:
    row = await db.get(LegalEntityDocument, document_id)
    if row is None or row.legal_entity_id != entity.id:
        raise NotFoundError("Document not found")
    return row


async def create_document(
    db: AsyncSession, entity: LegalEntity, data: dict
) -> LegalEntityDocument:
    doc_type = data["doc_type"]
    _check_details(doc_type, data.get("details") or {})
    if await _cycle_taken(
        db, entity, doc_type, data.get("expiry_date"), except_id=None
    ):
        raise _duplicate(doc_type)
    row = LegalEntityDocument(legal_entity_id=entity.id, **data)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_document(
    db: AsyncSession, entity: LegalEntity, row: LegalEntityDocument, data: dict
) -> LegalEntityDocument:
    spec = LEGAL_ENTITY_DOCUMENT_SPECS[row.doc_type]
    # Partial update: a field left out keeps its value. `expiry_date` is
    # required for an expiring type, so a `None` there is "not sent".
    expiry = data.get("expiry_date") or row.expiry_date
    if not spec["expires"]:
        expiry = None
        data.pop("expiry_date", None)
    issue = data.get("issue_date", row.issue_date)
    if issue is not None and expiry is not None and issue > expiry:
        raise BadRequestError("The issue date must be on or before the expiry date")
    if expiry != row.expiry_date and await _cycle_taken(
        db, entity, row.doc_type, expiry, except_id=row.id
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
    return row


async def delete_document(db: AsyncSession, row: LegalEntityDocument) -> None:
    key = row.file_object_key
    await db.delete(row)
    await db.flush()
    if key:
        await storage.delete_quietly(key)


async def store_document_file(
    db: AsyncSession,
    entity: LegalEntity,
    row: LegalEntityDocument,
    body: bytes,
    content_type: str,
) -> LegalEntityDocument:
    label = _label(row.doc_type)
    content_type, ext = storage.validate(body, content_type, label)
    key = document_key(entity, row, ext)
    await storage.upload(key, body, content_type, label.lower())
    previous = row.file_object_key
    row.file_object_key = key
    row.file_content_type = content_type
    await db.flush()
    if previous and previous != key:
        await storage.delete_quietly(previous)
    await db.refresh(row)
    return row


async def remove_document_file(
    db: AsyncSession, row: LegalEntityDocument
) -> LegalEntityDocument:
    key = row.file_object_key
    row.file_object_key = None
    row.file_content_type = None
    await db.flush()
    if key:
        await storage.delete_quietly(key)
    await db.refresh(row)
    return row


async def document_file(
    entity: LegalEntity, row: LegalEntityDocument
) -> storage.SignedFile:
    if not row.file_object_key:
        raise NotFoundError("This document has no file")
    when = (
        f" - expires {row.expiry_date.isoformat()}"
        if row.expiry_date
        else (f" - issued {row.issue_date.isoformat()}" if row.issue_date else "")
    )
    return await storage.sign_file(
        row.file_object_key,
        title=f"{entity.legal_name} - {_label(row.doc_type)}{when}",
        content_type=row.file_content_type,
    )


# ─── Other files ──────────────────────────────────────────────────────────────


async def file_categories(db: AsyncSession) -> list[str]:
    """Every type already given to an entity file, most used first."""
    count = func.count(LegalEntityFile.id)
    rows = await db.execute(
        select(LegalEntityFile.category)
        .where(LegalEntityFile.category.is_not(None))
        .group_by(LegalEntityFile.category)
        .order_by(count.desc(), LegalEntityFile.category)
    )
    return [r for (r,) in rows]


async def get_file(
    db: AsyncSession, entity: LegalEntity, file_id: uuid.UUID
) -> LegalEntityFile:
    row = await db.get(LegalEntityFile, file_id)
    if row is None or row.legal_entity_id != entity.id:
        raise NotFoundError("File not found")
    return row


async def create_file(
    db: AsyncSession, entity: LegalEntity, data: dict
) -> LegalEntityFile:
    row = LegalEntityFile(legal_entity_id=entity.id, **data)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_file(
    db: AsyncSession, row: LegalEntityFile, data: dict
) -> LegalEntityFile:
    for field, value in data.items():
        # `None` for the required title means "not sent" in a partial update.
        if value is None and field == "title":
            continue
        setattr(row, field, value)
    await db.flush()
    await db.refresh(row)
    return row


async def delete_file(db: AsyncSession, row: LegalEntityFile) -> None:
    key = row.file_object_key
    await db.delete(row)
    await db.flush()
    if key:
        await storage.delete_quietly(key)


async def store_file(
    db: AsyncSession,
    entity: LegalEntity,
    row: LegalEntityFile,
    body: bytes,
    content_type: str,
) -> LegalEntityFile:
    content_type, ext = storage.validate(body, content_type, "File")
    key = file_key(entity, row, ext)
    await storage.upload(key, body, content_type, "file")
    previous = row.file_object_key
    row.file_object_key = key
    row.file_content_type = content_type
    await db.flush()
    if previous and previous != key:
        await storage.delete_quietly(previous)
    await db.refresh(row)
    return row


async def signed_file(entity: LegalEntity, row: LegalEntityFile) -> storage.SignedFile:
    if not row.file_object_key:
        raise NotFoundError("This record has no file")
    return await storage.sign_file(
        row.file_object_key,
        title=f"{entity.legal_name} - {row.title}",
        content_type=row.file_content_type,
    )
