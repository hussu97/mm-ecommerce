"""Legal-entity registration paperwork: VAT/e-invoicing fields, the VAT
certificate, and the trade-licence history.

The emirate and authority lists live in the model, the migration's spelled-out
CHECKs and the schema Literals, which must agree. Documents go to the private
finance bucket under `legal-entities/<reference>/…`, never public; a licence is
one row per issue→expiry cycle, and the one expiring last is mirrored onto the
entity.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.v1 import legal_entity_documents as routes
from app.core import object_storage, private_documents
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.inventory import TRADE_LICENSE_AUTHORITIES
from app.models.legal_entity import (
    EMIRATES,
    TRADE_LICENSE_TYPES,
    LegalEntity,
    LegalEntityTradeLicense,
)
from app.schemas.pos import (
    LegalEntityCreate,
    LegalEntityResponse,
    LegalEntityUpdate,
    TradeLicenseCreate,
    TradeLicenseUpdate,
)
from app.services.orders import legal_entity_documents as docs

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "324_legal_entity_documents.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("m324", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── Vocabularies agree ──────────────────────────────────────────────────────


def test_migration_lists_match_the_model():
    m = _migration()
    assert m._EMIRATES == EMIRATES
    assert m._AUTHORITIES == tuple(TRADE_LICENSE_AUTHORITIES)
    assert m._LICENSE_TYPES == tuple(TRADE_LICENSE_TYPES)


def test_model_checks_name_every_emirate_and_authority():
    checks = {
        c.name: str(c.sqltext)
        for c in LegalEntityTradeLicense.__table__.constraints
        if hasattr(c, "sqltext")
    }
    for emirate in EMIRATES:
        assert f"'{emirate}'" in checks["ck_legal_entity_trade_licenses_emirate"]
    for code in TRADE_LICENSE_TYPES:
        assert f"'{code}'" in checks["ck_legal_entity_trade_licenses_type"]
    for code in TRADE_LICENSE_AUTHORITIES:
        assert f"'{code}'" in checks["ck_legal_entity_trade_licenses_authority"]
    entity_checks = {
        c.name: str(c.sqltext)
        for c in LegalEntity.__table__.constraints
        if hasattr(c, "sqltext")
    }
    assert "ck_legal_entities_vat_emirate" in entity_checks
    assert "ck_legal_entities_vat_tin_format" in entity_checks


def test_migration_seed_matches_the_documents():
    m = _migration()
    by_ref = {row[0]: row for row in m._LICENSES}
    assert by_ref["fatema"][1:7] == (
        "778179",
        "193868",
        "sharjah_sedd",
        "Sharjah",
        "2020-11-02",
        "2027-11-02",
    )
    assert by_ref["najm"][1:7] == (
        "1081755",
        "1764353",
        "dubai_det",
        "Dubai",
        "2022-07-19",
        "2027-07-18",
    )
    assert by_ref["fatema"][8:] == ("commercial", "Services Agency")
    assert by_ref["najm"][8:] == (
        "commercial",
        "Limited Liability Company - Single Owner (LLC - SO)",
    )
    for ref, *_rest, key, _kind, _form in m._LICENSES:
        assert key.startswith(f"legal-entities/{ref}/trade-licenses/")


# ─── Schemas ─────────────────────────────────────────────────────────────────


def test_vat_fields_validate_tin_peppol_and_emirate():
    ok = LegalEntityUpdate(
        vat_tin="1047114796", peppol_id="0235:1047114796", vat_emirate="Sharjah"
    )
    assert ok.peppol_id == "0235:1047114796"
    for bad in (
        {"vat_tin": "104711479600003"},  # the TRN, not the TIN
        {"vat_tin": "10471147"},
        {"peppol_id": "1047114796"},  # missing the scheme
        {"vat_emirate": "Muscat"},
    ):
        with pytest.raises(ValidationError):
            LegalEntityUpdate(**bad)


def test_trade_license_number_is_not_writable_on_the_entity():
    # It mirrors the current licence cycle; edits go through /trade-licenses.
    assert "trade_license_number" not in LegalEntityCreate.model_fields
    assert "trade_license_number" not in LegalEntityUpdate.model_fields
    assert "trade_license_expiry" in LegalEntityResponse.model_fields


def test_response_reports_certificate_presence_not_keys():
    fields = LegalEntityResponse.model_fields
    assert "has_vat_certificate" in fields
    assert not any(name.endswith("_object_key") for name in fields)


def test_trade_license_schema():
    data = TradeLicenseCreate(
        license_number="778179",
        issuing_authority="sharjah_sedd",
        emirate="Sharjah",
        issue_date=date(2020, 11, 2),
        expiry_date=date(2027, 11, 2),
    )
    assert data.emirate == "Sharjah"
    assert data.license_type is None
    assert (
        TradeLicenseUpdate(license_type="professional").license_type == "professional"
    )
    with pytest.raises(ValidationError):
        TradeLicenseUpdate(license_type="wizardry")
    with pytest.raises(ValidationError):
        TradeLicenseCreate(
            license_number="1",
            issuing_authority="atlantis",
            emirate="Dubai",
            expiry_date=date(2027, 1, 1),
        )
    with pytest.raises(ValidationError):
        TradeLicenseCreate(
            license_number="1",
            issuing_authority="dubai_det",
            emirate="Dubai",
            issue_date=date(2028, 1, 1),
            expiry_date=date(2027, 1, 1),
        )
    with pytest.raises(ValidationError):
        TradeLicenseUpdate(issue_date=date(2028, 1, 1), expiry_date=date(2027, 1, 1))


def test_routes_are_declared():
    paths = {(r.path, tuple(sorted(r.methods))) for r in routes.router.routes}
    assert ("/{entity_id}/trade-licenses", ("GET",)) in paths
    assert ("/{entity_id}/trade-licenses", ("POST",)) in paths
    assert ("/{entity_id}/trade-licenses/{license_id}", ("PATCH",)) in paths
    assert ("/{entity_id}/trade-licenses/{license_id}/document", ("POST",)) in paths
    assert ("/{entity_id}/vat-certificate", ("GET",)) in paths


# ─── Storage + history ───────────────────────────────────────────────────────


class _Storage:
    def __init__(self):
        self.uploads: list[dict] = []
        self.deletes: list[str] = []

    def upload_object(self, **kwargs):
        self.uploads.append(kwargs)

    def delete_object(self, *, bucket, key):
        self.deletes.append(key)

    def signed_url(self, *, bucket, key, expires_seconds=3600, download_filename=None):
        suffix = f"?download={download_filename}" if download_filename else ""
        return f"https://signed/{bucket}/{key}{suffix}"


@pytest.fixture
def storage(monkeypatch):
    fake = _Storage()
    monkeypatch.setattr(docs, "object_storage", fake)
    monkeypatch.setattr(private_documents, "object_storage", fake)
    return fake


def _entity(**kw):
    return SimpleNamespace(
        id=uuid.uuid4(),
        reference="fatema",
        legal_name="Fatema Sweets L.L.C",
        vat_certificate_object_key=kw.get("vat"),
        vat_certificate_content_type=None,
        trade_license_number=None,
        trade_license_expiry=None,
    )


def _license(entity, expiry, **kw):
    return SimpleNamespace(
        id=uuid.uuid4(),
        legal_entity_id=entity.id,
        license_number=kw.get("number", "778179"),
        issue_date=kw.get("issued"),
        expiry_date=expiry,
        document_object_key=kw.get("key"),
        document_content_type=None,
    )


def _db():
    return SimpleNamespace(
        flush=AsyncMock(), refresh=AsyncMock(), add=lambda _: None, delete=AsyncMock()
    )


async def test_vat_certificate_goes_to_the_private_bucket(storage):
    entity = _entity(vat="legal-entities/fatema/vat-certificate.jpg")
    await docs.store_vat_certificate(_db(), entity, b"%PDF", "application/pdf")
    (upload,) = storage.uploads
    assert upload["bucket"] == docs.settings.GCS_INVOICE_BUCKET
    assert upload["key"] == "legal-entities/fatema/vat-certificate.pdf"
    assert upload["cache_control"] == "private, no-store"
    assert entity.vat_certificate_object_key == upload["key"]
    # A re-upload with another extension does not orphan the old object.
    assert storage.deletes == ["legal-entities/fatema/vat-certificate.jpg"]
    signed = await docs.vat_certificate(entity)
    assert signed.url.endswith("legal-entities/fatema/vat-certificate.pdf")
    # A second link forces a download under a readable name.
    assert signed.filename == "Fatema Sweets L.L.C - VAT certificate.pdf"
    assert signed.download_url.endswith("?download=" + signed.filename)


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"x", "application/zip"),
        (b"", "application/pdf"),
        (b"x" * (docs.DOCUMENT_MAX_BYTES + 1), "application/pdf"),
    ],
)
async def test_refuses_bad_uploads(storage, body, content_type):
    with pytest.raises(BadRequestError):
        await docs.store_vat_certificate(_db(), _entity(), body, content_type)
    assert storage.uploads == []


async def test_each_cycle_has_its_own_document_key(storage):
    entity = _entity()
    old = _license(entity, date(2026, 11, 2))
    new = _license(entity, date(2027, 11, 2))
    await docs.store_license_document(_db(), entity, old, b"%PDF", "application/pdf")
    await docs.store_license_document(_db(), entity, new, b"%PDF", "application/pdf")
    assert [u["key"] for u in storage.uploads] == [
        "legal-entities/fatema/trade-licenses/2026-11-02.pdf",
        "legal-entities/fatema/trade-licenses/2027-11-02.pdf",
    ]
    assert storage.deletes == []
    with pytest.raises(NotFoundError):
        await docs.license_document(entity, _license(entity, date(2025, 1, 1)))


async def test_sync_mirrors_the_cycle_expiring_last(monkeypatch):
    entity = _entity()
    newest = _license(entity, date(2027, 11, 2), number="778179")
    older = _license(entity, date(2026, 11, 2), number="778179-old")
    monkeypatch.setattr(docs, "list_licenses", AsyncMock(return_value=[newest, older]))
    await docs.sync_current_license(_db(), entity)
    assert entity.trade_license_number == "778179"
    assert entity.trade_license_expiry == date(2027, 11, 2)
    monkeypatch.setattr(docs, "list_licenses", AsyncMock(return_value=[]))
    await docs.sync_current_license(_db(), entity)
    assert entity.trade_license_number is None
    assert entity.trade_license_expiry is None


async def test_create_refuses_a_duplicate_cycle(monkeypatch):
    monkeypatch.setattr(docs, "_expiry_taken", AsyncMock(return_value=True))
    with pytest.raises(ConflictError):
        await docs.create_license(
            _db(), _entity(), {"expiry_date": date(2027, 11, 2), "license_number": "1"}
        )


async def test_update_checks_dates_against_the_stored_row(monkeypatch):
    entity = _entity()
    row = _license(entity, date(2027, 11, 2), issued=date(2020, 11, 2))
    with pytest.raises(BadRequestError):
        await docs.update_license(_db(), entity, row, {"issue_date": date(2028, 1, 1)})
    monkeypatch.setattr(docs, "sync_current_license", AsyncMock())
    await docs.update_license(
        _db(), entity, row, {"registration_number": "193868", "license_number": None}
    )
    assert row.registration_number == "193868"
    assert row.license_number == "778179"  # a None for a required field is ignored


async def test_deleting_a_cycle_deletes_its_document(storage, monkeypatch):
    monkeypatch.setattr(docs, "sync_current_license", AsyncMock())
    entity = _entity()
    row = _license(
        entity,
        date(2027, 11, 2),
        key="legal-entities/fatema/trade-licenses/2027-11-02.pdf",
    )
    await docs.delete_license(_db(), entity, row)
    assert storage.deletes == ["legal-entities/fatema/trade-licenses/2027-11-02.pdf"]


def test_download_name_is_safe_and_keeps_the_extension():
    name = private_documents.download_name
    assert (
        name("Fatema: VAT / cert", "a/b.pdf", "application/pdf")
        == "Fatema VAT cert.pdf"
    )
    # No content type on file (an older upload): the key's extension stands in.
    assert name("Licence", "legal-entities/x/2026-11-02.jpg", None) == "Licence.jpg"
    assert name("  ", "k", None) == "document"


def test_attachment_disposition_survives_a_non_ascii_name():
    value = object_storage._attachment('فاطمة "Sweets".pdf')
    assert value.startswith('attachment; filename=" Sweets.pdf";')
    assert "filename*=UTF-8''%D9%81" in value
