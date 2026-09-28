"""Supplier registration: trade licence number/authority and the two documents.

The authority list lives in three places that must agree — the model's
``TRADE_LICENSE_AUTHORITIES`` (source of the schema Literal), the migration that
spells out the DB CHECK, and the model's own ``__table_args__``. The documents
(VAT/TRN certificate, trade licence) go to the private finance bucket under a
deterministic key, never public, and a re-upload with another extension must not
orphan the old object.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.v1 import inventory as inventory_router
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.inventory import TRADE_LICENSE_AUTHORITIES, Supplier
from app.schemas.inventory import SupplierCreate, SupplierResponse, SupplierUpdate
from app.services.inventory import supplier_service

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "301_supplier_trade_license.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("m301", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_check_matches_the_model_list():
    assert _migration()._AUTHORITIES == tuple(TRADE_LICENSE_AUTHORITIES)


def test_model_check_constraint_names_every_authority():
    (check,) = [
        c
        for c in Supplier.__table__.constraints
        if c.name == "ck_supplier_trade_license_authority"
    ]
    for code in TRADE_LICENSE_AUTHORITIES:
        assert f"'{code}'" in str(check.sqltext)


def test_every_emirate_is_covered():
    labels = " ".join(TRADE_LICENSE_AUTHORITIES.values())
    for emirate in (
        "Dubai",
        "Abu Dhabi",
        "Sharjah",
        "Ajman",
        "Umm Al Quwain",
        "Ras Al Khaimah",
        "Fujairah",
    ):
        assert emirate in labels


def test_schema_accepts_known_authority_and_refuses_unknown():
    data = SupplierCreate(
        name="Acme", trade_license_number="CN-123", trade_license_authority="dmcc"
    )
    assert data.trade_license_authority == "dmcc"
    assert SupplierUpdate(trade_license_authority=None).trade_license_authority is None
    with pytest.raises(ValidationError):
        SupplierCreate(name="Acme", trade_license_authority="atlantis")


def test_response_reports_document_presence_not_keys():
    fields = SupplierResponse.model_fields
    assert "has_trn_certificate" in fields and "has_trade_license" in fields
    assert not any(name.endswith("_object_key") for name in fields)


def test_authorities_route_is_declared_before_the_id_route():
    paths = [route.path for route in inventory_router.suppliers_router.routes]
    assert paths.index("/trade-license-authorities") < paths.index("/{supplier_id}")


# ─── Document storage ────────────────────────────────────────────────────────


class _Storage:
    def __init__(self):
        self.uploads: list[dict] = []
        self.deletes: list[str] = []

    def upload_object(self, **kwargs):
        self.uploads.append(kwargs)

    def delete_object(self, *, bucket, key):
        self.deletes.append(key)

    def signed_url(self, *, bucket, key, expires_seconds=3600):
        return f"https://signed/{bucket}/{key}"


@pytest.fixture
def storage(monkeypatch):
    fake = _Storage()
    monkeypatch.setattr(supplier_service, "object_storage", fake)
    return fake


def _supplier(**keys):
    return SimpleNamespace(
        id=uuid.uuid4(),
        trn_certificate_object_key=keys.get("trn"),
        trn_certificate_content_type=None,
        trade_license_object_key=keys.get("license"),
        trade_license_content_type=None,
    )


def _db():
    return SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())


async def test_store_puts_document_in_private_bucket(storage):
    supplier = _supplier()
    await supplier_service.store_document(
        _db(), supplier, "trade_license", b"%PDF", "application/pdf"
    )
    (upload,) = storage.uploads
    assert upload["bucket"] == supplier_service.settings.GCS_INVOICE_BUCKET
    assert upload["key"] == f"suppliers/{supplier.id}/trade_license.pdf"
    assert upload["cache_control"] == "private, no-store"
    assert supplier.trade_license_object_key == upload["key"]
    assert supplier.trade_license_content_type == "application/pdf"
    assert supplier.trn_certificate_object_key is None


async def test_reupload_with_new_extension_deletes_old_object(storage):
    supplier = _supplier()
    old_key = f"suppliers/{supplier.id}/trn_certificate.pdf"
    supplier.trn_certificate_object_key = old_key
    await supplier_service.store_document(
        _db(), supplier, "trn_certificate", b"\xff\xd8", "image/jpeg"
    )
    assert storage.deletes == [old_key]
    assert supplier.trn_certificate_object_key.endswith("trn_certificate.jpg")


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"x", "application/zip"),
        (b"", "application/pdf"),
        (b"x" * (supplier_service.DOCUMENT_MAX_BYTES + 1), "application/pdf"),
    ],
)
async def test_store_refuses_bad_uploads(storage, body, content_type):
    with pytest.raises(BadRequestError):
        await supplier_service.store_document(
            _db(), _supplier(), "trade_license", body, content_type
        )
    assert storage.uploads == []


async def test_remove_clears_key_and_deletes_object(storage):
    supplier = _supplier(license="suppliers/x/trade_license.png")
    supplier.trade_license_content_type = "image/png"
    await supplier_service.remove_document(_db(), supplier, "trade_license")
    assert supplier.trade_license_object_key is None
    assert supplier.trade_license_content_type is None
    assert storage.deletes == ["suppliers/x/trade_license.png"]


async def test_document_url_signs_or_404s(storage):
    supplier = _supplier(trn="suppliers/x/trn_certificate.pdf")
    url = await supplier_service.document_url(supplier, "trn_certificate")
    assert url.endswith("suppliers/x/trn_certificate.pdf")
    with pytest.raises(NotFoundError):
        await supplier_service.document_url(supplier, "trade_license")
