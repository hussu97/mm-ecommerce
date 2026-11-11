"""P2-12 / P2-14 at each private-upload path.

Every path into the private finance bucket — employee photo and documents,
supplier and legal-entity paperwork, purchase-order invoices from the admin
(raw body) and the till (base64, on its own and inside a till-created PO) —
stores what the bytes really are: a disguised file is refused before any GCS
call, a mislabelled genuine scan is stored under its detected type and
extension. The purchase-order paths also run their blocking GCS calls through
`asyncio.to_thread` instead of on the event loop.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import inventory
from app.core import private_documents
from app.core.exceptions import BadRequestError
from app.services.inventory import supplier_service
from app.services.orders import legal_entity_documents
from app.services.people import employee_service

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32
HTML = b"<!DOCTYPE html><html><script>alert(document.cookie)</script></html>"


class _Storage:
    """Records GCS calls instead of making them."""

    def __init__(self):
        self.uploads: list[dict] = []
        self.deletes: list[str] = []

    def upload_object(self, **kwargs):
        self.uploads.append(kwargs)

    def delete_object(self, *, bucket, key):
        self.deletes.append(key)

    def signed_url(self, *, bucket, key, expires_seconds=3600):
        return f"https://signed/{bucket}/{key}"


class _RecordingToThread:
    """Runs the call inline, recording that it was dispatched via to_thread."""

    def __init__(self):
        self.funcs: list = []

    async def __call__(self, func, *args, **kwargs):
        self.funcs.append(func)
        return func(*args, **kwargs)


def _db():
    return SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())


# ─── Employees (photo: images only; documents: images or PDF) ────────────────


@pytest.fixture
def employee_storage(monkeypatch):
    uploads: list[tuple] = []

    async def fake_upload(key, body, content_type, label):
        uploads.append((key, content_type))

    monkeypatch.setattr(private_documents, "upload", fake_upload)
    monkeypatch.setattr(private_documents, "delete_quietly", AsyncMock())
    return uploads


def _employee():
    return SimpleNamespace(
        id=uuid.uuid4(), photo_object_key=None, photo_content_type=None
    )


async def test_employee_photo_mislabelled_png_is_stored_as_png(employee_storage):
    employee = _employee()
    await employee_service.store_photo(_db(), employee, PNG, "image/jpeg")
    assert employee_storage == [(f"employees/{employee.id}/photo.png", "image/png")]
    assert employee.photo_object_key.endswith("photo.png")
    assert employee.photo_content_type == "image/png"


@pytest.mark.parametrize("body", [HTML, PDF])
async def test_employee_photo_refuses_non_images(employee_storage, body):
    with pytest.raises(BadRequestError, match="Photo"):
        await employee_service.store_photo(_db(), _employee(), body, "image/png")
    assert employee_storage == []


# ─── Legal-entity paperwork ──────────────────────────────────────────────────


@pytest.fixture
def entity_storage(monkeypatch):
    fake = _Storage()
    monkeypatch.setattr(legal_entity_documents, "object_storage", fake)
    return fake


def _entity():
    return SimpleNamespace(
        id=uuid.uuid4(),
        reference="fatema",
        vat_certificate_object_key=None,
        vat_certificate_content_type=None,
    )


async def test_vat_certificate_disguised_html_is_refused(entity_storage):
    with pytest.raises(BadRequestError, match="VAT certificate"):
        await legal_entity_documents.store_vat_certificate(
            _db(), _entity(), HTML, "application/pdf"
        )
    assert entity_storage.uploads == []


async def test_vat_certificate_mislabelled_pdf_is_stored_as_pdf(entity_storage):
    entity = _entity()
    await legal_entity_documents.store_vat_certificate(_db(), entity, PDF, "image/jpeg")
    (upload,) = entity_storage.uploads
    assert upload["key"] == "legal-entities/fatema/vat-certificate.pdf"
    assert upload["content_type"] == "application/pdf"
    assert entity.vat_certificate_content_type == "application/pdf"


# ─── Supplier paperwork ──────────────────────────────────────────────────────


@pytest.fixture
def supplier_storage(monkeypatch):
    fake = _Storage()
    monkeypatch.setattr(supplier_service, "object_storage", fake)
    return fake


def _supplier():
    return SimpleNamespace(
        id=uuid.uuid4(),
        trn_certificate_object_key=None,
        trn_certificate_content_type=None,
        trade_license_object_key=None,
        trade_license_content_type=None,
    )


async def test_supplier_document_disguised_html_is_refused(supplier_storage):
    with pytest.raises(BadRequestError, match="Trade licence"):
        await supplier_service.store_document(
            _db(), _supplier(), "trade_license", HTML, "image/png"
        )
    assert supplier_storage.uploads == []


async def test_supplier_document_mislabelled_jpeg_is_stored_as_jpeg(supplier_storage):
    supplier = _supplier()
    await supplier_service.store_document(
        _db(), supplier, "trn_certificate", JPEG, "image/png"
    )
    (upload,) = supplier_storage.uploads
    assert upload["key"] == f"suppliers/{supplier.id}/trn_certificate.jpg"
    assert upload["content_type"] == "image/jpeg"
    assert supplier.trn_certificate_content_type == "image/jpeg"


# ─── Purchase-order invoices (admin raw body, till base64, till-created PO) ──


@pytest.fixture
def po_storage(monkeypatch):
    fake = _Storage()
    monkeypatch.setattr(inventory, "object_storage", fake)
    recorder = _RecordingToThread()
    monkeypatch.setattr(asyncio, "to_thread", recorder)
    return SimpleNamespace(storage=fake, to_thread=recorder)


def _po(previous_key=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        invoice_object_key=previous_key,
        invoice_content_type=None,
    )


class _Request:
    def __init__(self, body: bytes, content_type: str):
        self.headers = {"content-type": content_type, "content-length": str(len(body))}
        self._body = body

    async def body(self) -> bytes:
        return self._body


async def test_admin_invoice_upload_retypes_and_runs_off_the_loop(po_storage):
    po = _po(previous_key="purchase-orders/b/p/invoice.jpg")
    await inventory._store_invoice_from_request(
        _db(), po, _Request(PDF, "image/jpeg; charset=binary")
    )
    (upload,) = po_storage.storage.uploads
    assert upload["key"].endswith("/invoice.pdf")
    assert upload["content_type"] == "application/pdf"
    assert po.invoice_content_type == "application/pdf"
    # The old .jpg object is deleted; both GCS calls went through to_thread.
    assert po_storage.storage.deletes == ["purchase-orders/b/p/invoice.jpg"]
    assert po_storage.to_thread.funcs == [
        po_storage.storage.upload_object,
        po_storage.storage.delete_object,
    ]


async def test_admin_invoice_upload_refuses_disguised_html(po_storage):
    po = _po()
    with pytest.raises(BadRequestError, match="Invoice"):
        await inventory._store_invoice_from_request(
            _db(), po, _Request(HTML, "application/pdf")
        )
    assert po_storage.storage.uploads == []
    assert po.invoice_object_key is None


async def test_till_invoice_base64_retypes_a_mislabelled_png(po_storage):
    po = _po()
    await inventory._store_invoice_from_base64(
        _db(), po, base64.b64encode(PNG).decode(), "image/jpeg"
    )
    (upload,) = po_storage.storage.uploads
    assert upload["key"].endswith("/invoice.png")
    assert po.invoice_content_type == "image/png"
    assert po_storage.to_thread.funcs == [po_storage.storage.upload_object]


async def test_till_invoice_base64_refuses_disguised_html(po_storage):
    with pytest.raises(BadRequestError, match="Invoice"):
        await inventory._store_invoice_from_base64(
            _db(), _po(), base64.b64encode(HTML).decode(), "image/jpeg"
        )
    assert po_storage.storage.uploads == []


def _patch_pos_create(monkeypatch):
    created = {}

    async def fake_create(db, **kwargs):
        created.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4()), None

    monkeypatch.setattr(inventory.crud_service, "get_or_404", AsyncMock())
    monkeypatch.setattr(inventory.access_service, "assert_branch_access", AsyncMock())
    monkeypatch.setattr(
        inventory.inventory_service, "create_pos_purchase_order", fake_create
    )
    monkeypatch.setattr(inventory, "_load_po", AsyncMock())
    monkeypatch.setattr(inventory, "_refresh_vat_ledger_for_po", AsyncMock())
    monkeypatch.setattr(inventory, "_serialise_po", AsyncMock(return_value="ok"))
    return created


def _pos_create_data(body: bytes, content_type: str):
    return SimpleNamespace(
        branch_id=uuid.uuid4(),
        supplier_id=uuid.uuid4(),
        warehouse_id=None,
        invoice_image_base64=base64.b64encode(body).decode(),
        invoice_content_type=content_type,
    )


async def test_till_created_po_stores_the_detected_invoice_type(
    monkeypatch, po_storage
):
    created = _patch_pos_create(monkeypatch)
    data = _pos_create_data(PNG, "image/jpeg")
    await inventory.pos_create_purchase_order(data, db=_db(), user=object())
    (upload,) = po_storage.storage.uploads
    assert upload["key"].endswith("/invoice.png")
    assert upload["content_type"] == "image/png"
    assert created["invoice_object_key"] == upload["key"]
    # The PO row records the real type, not the iPad's label.
    assert created["invoice_content_type"] == "image/png"
    assert po_storage.to_thread.funcs == [po_storage.storage.upload_object]


async def test_till_created_po_refuses_a_disguised_invoice(monkeypatch, po_storage):
    created = _patch_pos_create(monkeypatch)
    with pytest.raises(BadRequestError, match="Invoice"):
        await inventory.pos_create_purchase_order(
            _pos_create_data(HTML, "image/jpeg"), db=_db(), user=object()
        )
    assert po_storage.storage.uploads == []
    assert created == {}


# ─── Aggregator statement archives (P2-14 only — portal bytes, not user input) ─


async def test_pushed_finance_parse_and_archive_run_off_the_loop(monkeypatch):
    """Parsing a pushed Deliveroo/Keeta finance payload uploads its VAT document
    to GCS synchronously, so the request coroutine hands the whole parse to a
    thread rather than blocking the API's event loop on the upload."""
    from app.services.aggregators import ingest
    from app.services.providers import deliveroo_provider, keeta_provider

    recorder = _RecordingToThread()
    monkeypatch.setattr(asyncio, "to_thread", recorder)
    monkeypatch.setattr(
        deliveroo_provider.provider, "parse_pushed_finance", lambda payload: None
    )
    monkeypatch.setattr(
        keeta_provider.provider,
        "parse_finance",
        lambda payload: SimpleNamespace(
            statements=[], payouts=[], truncation_note=None
        ),
    )
    assert await ingest.ingest_deliveroo_finance_payloads(_db(), [{}]) == (0, 0)
    assert await ingest.ingest_keeta_finance_payloads([{}]) == (0, 0)
    assert len(recorder.funcs) == 2
