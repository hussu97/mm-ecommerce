"""A legal entity's registration documents beyond the trade licence and VAT
certificate, its other files, and their place in the expiring-paperwork warning.

A document is one issue→expiry cycle; the one expiring last per type is
current — each entity manages one shop, so its premises documents are one per
entity too — and a type issued once (Corporate Tax, MoA) has no expiry and its
latest issued is current. Every route is admin-only (`admin.settings.manage`), like the
trade-licence routes beside them.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1 import legal_entity_documents as routes
from app.models.legal_entity import (
    ENTITY_EXPIRY_TRACKED_TYPES,
    LEGAL_ENTITY_DOCUMENT_SPECS,
    LEGAL_ENTITY_DOCUMENT_TYPES,
    NON_EXPIRING_ENTITY_DOCUMENT_TYPES,
    LegalEntityDocument,
)
from app.schemas.legal_entity_paperwork import (
    LegalEntityDocumentCreate,
    LegalEntityDocumentTypeSpec,
    LegalEntityFileCreate,
)
from app.services.people import legal_entity_paperwork as paper

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "330_legal_entity_documents.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(MIGRATION.stem, MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── Shape ───────────────────────────────────────────────────────────────────


def test_migration_lists_match_the_model():
    m = _migration()
    assert m.DOC_TYPES == LEGAL_ENTITY_DOCUMENT_TYPES
    assert m.NON_EXPIRING == NON_EXPIRING_ENTITY_DOCUMENT_TYPES
    assert len(m.revision) <= 32


def test_the_owner_asked_for_types_are_there_and_tracked():
    for doc_type in ("establishment_card", "mohre_card", "chamber_certificate"):
        assert doc_type in ENTITY_EXPIRY_TRACKED_TYPES
    # Issued once: nothing to warn about.
    assert not set(NON_EXPIRING_ENTITY_DOCUMENT_TYPES) & set(
        ENTITY_EXPIRY_TRACKED_TYPES
    )


def test_every_spec_fits_the_served_shape():
    for doc_type, spec in LEGAL_ENTITY_DOCUMENT_SPECS.items():
        LegalEntityDocumentTypeSpec(doc_type=doc_type, **spec)


# ─── Access + routing ────────────────────────────────────────────────────────


def test_every_paperwork_route_needs_settings_manage():
    found = [r for r in routes.router.routes if isinstance(r, APIRoute)]
    assert found
    for route in found:
        stamps = {
            getattr(dep.call, "permission", None)
            for dep in route.dependant.dependencies
        }
        assert "admin.settings.manage" in stamps, f"{route.methods} {route.path}"


def test_static_paths_are_not_swallowed_by_the_crud_id_route():
    """The CRUD router's `/{id}` would 422 on `document-types` (not a UUID) for a
    signed-in admin. Mounted after the paperwork router, it never sees it."""
    from app.api.v1.router import api_router
    from app.core.deps import get_current_active_user

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        can=lambda _: True
    )
    response = TestClient(app).get("/legal-entities/document-types")
    assert response.status_code == 200, response.text
    assert [t["doc_type"] for t in response.json()] == list(LEGAL_ENTITY_DOCUMENT_TYPES)


# ─── Validation ──────────────────────────────────────────────────────────────


def test_an_expiring_type_needs_its_expiry():
    with pytest.raises(ValidationError, match="expiry date is required"):
        LegalEntityDocumentCreate(doc_type="establishment_card")


def test_a_type_issued_once_drops_any_expiry():
    doc = LegalEntityDocumentCreate(
        doc_type="corporate_tax", expiry_date=date(2030, 1, 1)
    )
    assert doc.expiry_date is None


def test_details_drop_blanks_and_refuse_unknown_keys():
    doc = LegalEntityDocumentCreate(
        doc_type="chamber_certificate",
        expiry_date=date(2027, 1, 1),
        details={"grade": " Gold ", "legal_form": "  "},
    )
    assert doc.details == {"grade": "Gold"}
    with pytest.raises(ValidationError, match="Unknown"):
        LegalEntityDocumentCreate(
            doc_type="chamber_certificate",
            expiry_date=date(2027, 1, 1),
            details={"landlord": "x"},
        )


def test_issue_after_expiry_is_refused():
    with pytest.raises(ValidationError, match="issue date"):
        LegalEntityDocumentCreate(
            doc_type="mohre_card",
            issue_date=date(2027, 2, 1),
            expiry_date=date(2027, 1, 1),
        )


def test_other_files_share_the_employee_trimming():
    f = LegalEntityFileCreate(title="  Power of attorney ", category=" ")
    assert (f.title, f.category) == ("Power of attorney", None)


# ─── Current per type ────────────────────────────────────────────────────────


def _doc(doc_type, *, expiry=None, issued=None, created=0):
    return LegalEntityDocument(
        id=uuid.uuid4(),
        legal_entity_id=uuid.uuid4(),
        doc_type=doc_type,
        expiry_date=expiry,
        issue_date=issued,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(seconds=created),
    )


def test_current_is_the_latest_expiry_and_issued_once_types_take_the_latest_issue():
    old = _doc("tenancy_contract", expiry=date(2026, 1, 1))
    new = _doc("tenancy_contract", expiry=date(2027, 1, 1))
    moa_first = _doc("moa", issued=date(2019, 1, 1))
    moa_amended = _doc("moa", issued=date(2024, 5, 1))
    current = paper.current_by_type([new, old, moa_amended, moa_first])
    assert current["tenancy_contract"] is new
    assert current["moa"] is moa_amended


def test_object_keys_are_per_row_under_the_entity():
    entity = SimpleNamespace(reference="fatema")
    row = _doc("establishment_card", expiry=date(2027, 3, 4))
    assert (
        paper.document_key(entity, row, ".pdf")
        == f"legal-entities/fatema/establishment_card/{row.id}.pdf"
    )


# ─── Real Postgres ───────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@db_required
async def test_cycles_and_the_expiry_warning(monkeypatch):
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import private_documents
    from app.core.exceptions import ConflictError
    from app.models import LegalEntity
    from app.services.people import expiry_service

    deleted: list[str] = []

    async def fake_upload(key, body, content_type, label):
        return None

    async def fake_delete(key):
        deleted.append(key)

    monkeypatch.setattr(private_documents, "upload", fake_upload)
    monkeypatch.setattr(private_documents, "delete_quietly", fake_delete)

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    today = date(2026, 11, 15)
    entity_id: uuid.UUID | None = None
    try:
        async with Session() as db:
            entity = LegalEntity(
                reference=f"t-{tag}", legal_name=f"Entity {tag}", brand_name="E"
            )
            db.add(entity)
            await db.flush()
            entity_id = entity.id

            def doc(doc_type, days=None, **kw):
                return {
                    "doc_type": doc_type,
                    "expiry_date": None
                    if days is None
                    else today + timedelta(days=days),
                    "details": {},
                    **kw,
                }

            # Establishment card: lapsed cycle is history, renewal in 9 days is
            # current and warned. Chamber in 40 days is not yet. Tenancy in 3
            # days is warned. The MoA has no expiry and never warns.
            await paper.create_document(db, entity, doc("establishment_card", -400))
            card = await paper.create_document(db, entity, doc("establishment_card", 9))
            await paper.create_document(db, entity, doc("chamber_certificate", 40))
            await paper.create_document(db, entity, doc("tenancy_contract", 3))
            await paper.create_document(db, entity, doc("moa", issue_date=today))
            with pytest.raises(ConflictError):
                await paper.create_document(db, entity, doc("establishment_card", 9))

            card = await paper.store_document_file(
                db, entity, card, b"%PDF-1.4", "application/pdf"
            )
            assert card.file_object_key == (
                f"legal-entities/t-{tag}/establishment_card/{card.id}.pdf"
            )
            await db.commit()

        async with Session() as db:
            result = await expiry_service.expiring_documents(db, today=today)
            mine = [
                (i.doc_type, i.days_left)
                for i in result.items
                if i.owner_id == entity_id
            ]
            assert mine == [
                ("tenancy_contract", 3),
                ("establishment_card", 9),
            ]

            entity = await db.get(LegalEntity, entity_id)
            listing = await paper.paperwork(db, entity)
            cards = [d for d in listing.documents if d.doc_type == "establishment_card"]
            assert [d.is_current for d in cards] == [True, False]
            overview = [
                o for o in await paper.overview(db) if o.legal_entity_id == entity_id
            ]
            assert {d.doc_type for d in overview[0].current_documents} == {
                "establishment_card",
                "chamber_certificate",
                "tenancy_contract",
                "moa",
            }

            row = await paper.get_document(db, entity, card.id)
            await paper.delete_document(db, row)
            await db.commit()
            assert card.file_object_key in deleted
    finally:
        async with Session() as db:
            if entity_id:
                await db.execute(delete(LegalEntity).where(LegalEntity.id == entity_id))
            await db.commit()
        await engine.dispose()
