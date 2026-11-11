"""
Custom-order enquiry photos are private, and only our own uploads are accepted.

They used to go to the public image bucket (world-readable *and* listable) from
an unauthenticated endpoint, and the submit accepted any string at all as a
"photo URL" — which then rendered as an `<img src>`/`<a href>` in the admin and
as a link in the staff email. Now:

* the upload lands in the private bucket under `enquiries/pending/<uuid4>.<ext>`;
* the submit accepts only such a key (or its private-bucket URL, for a
  storefront deployed ahead of the API), checks it exists, and moves it to
  `enquiries/<enquiry_id>/…`, storing keys;
* the admin reads get signed links minted per request, mapping pre-private
  public URLs onto `enquiries/legacy/<name>`; anything else is dropped;
* the staff email links to the admin page and never to an image.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from app.api.v1 import custom_order_enquiries as routes
from app.core import private_documents
from app.core.config import settings
from app.core.exceptions import BadGatewayError, BadRequestError
from app.schemas.custom_order_enquiry import CustomOrderEnquiryCreate
from app.services import custom_order_enquiry_service as svc
from app.services import email_service

PRIVATE = settings.GCS_INVOICE_BUCKET
PUBLIC = settings.GCS_IMAGE_BUCKET
GCS = "https://storage.googleapis.com"
#: Fixed, so parametrised ids are identical on every xdist worker.
U = "3f2b8c4e-9a1d-4e6f-8b2a-7c5d9e0f1a2b"


def _pending(ext: str = ".jpg") -> str:
    return f"enquiries/pending/{uuid.uuid4()}{ext}"


# ─── Validating what the form sends ──────────────────────────────────────────


@pytest.mark.parametrize("ext", [".jpg", ".png", ".webp"])
def test_a_bare_pending_key_is_accepted(ext):
    key = _pending(ext)
    assert svc.pending_key_from_reference(key) == key


def test_the_signed_private_url_an_older_storefront_sends_is_accepted():
    key = _pending()
    url = (
        f"{GCS}/{PRIVATE}/{key}?X-Goog-Algorithm=GOOG4-RSA-SHA256"
        "&X-Goog-Credential=x&X-Goog-Signature=abc"
    )
    assert svc.pending_key_from_reference(url) == key
    assert svc.pending_key_from_reference(f"{GCS}/{PRIVATE}/{key}") == key


@pytest.mark.parametrize(
    "value",
    [
        # Foreign hosts and schemes.
        "https://evil.example.com/enquiries/pending/{u}.jpg",
        "https://storage.googleapis.com.evil.example/{priv}/enquiries/pending/{u}.jpg",
        "https://storage.googleapis.com@evil.example/{priv}/enquiries/pending/{u}.jpg",
        "http://storage.googleapis.com/{priv}/enquiries/pending/{u}.jpg",
        "javascript:alert(1)//enquiries/pending/{u}.jpg",
        "data:image/svg+xml,<svg onload=alert(1)>",
        # The wrong bucket: the public one, where the old uploads went.
        "https://storage.googleapis.com/{pub}/enquiries/pending/{u}.jpg",
        "https://storage.googleapis.com/{pub}/enquiries/{u}.jpg",
        # Path traversal, raw and encoded.
        "enquiries/pending/../../legal-entities/x/{u}.jpg",
        "enquiries/pending/{u}.jpg/../../../invoices/{u}.jpg",
        "https://storage.googleapis.com/{priv}/enquiries/pending/..%2F..%2Finvoices/{u}.jpg",
        "/enquiries/pending/{u}.jpg",
        # Not a name we mint.
        "enquiries/pending/cake.jpg",
        "enquiries/pending/{u}.gif",
        "enquiries/pending/{u}.heic",
        "enquiries/pending/{u}",
        "enquiries/pending/{U}.jpg",
        "enquiries/pending/00000000-0000-1000-8000-000000000000.jpg",  # uuid1
        # Our prefix, but not a pending upload: no claiming someone's photo.
        "enquiries/{u}/{u}.jpg",
        "enquiries/legacy/{u}.jpg",
        "enquiries/{u}.jpg",
        "products/{u}.jpg",
        "",
    ],
)
def test_anything_but_our_own_pending_upload_is_a_400(value):
    u = uuid.uuid4()
    value = value.format(u=u, U=str(u).upper(), priv=PRIVATE, pub=PUBLIC)
    with pytest.raises(BadRequestError):
        svc.pending_key_from_reference(value)


# ─── Reading stored values ───────────────────────────────────────────────────


def test_a_stored_key_is_read_as_is():
    key = f"enquiries/{uuid.uuid4()}/{uuid.uuid4()}.jpg"
    assert svc.stored_photo_key(key) == key


def test_a_pre_private_public_url_maps_to_the_legacy_key():
    name = f"{uuid.uuid4()}.jpg"
    assert (
        svc.stored_photo_key(f"{GCS}/{PUBLIC}/enquiries/{name}")
        == f"enquiries/legacy/{name}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/1.jpg",
        f"{GCS}/{PUBLIC}/cater/wedding-1.jpg",
        f"{GCS}/{PUBLIC}/enquiries/../products/{U}.jpg",
        f"{GCS}/{PUBLIC}/enquiries/{U}.jpg?x=1",
        f"{GCS}/some-other-bucket/enquiries/{U}.jpg",
        f"enquiries/pending/{U}.jpg",
        "javascript:alert(1)",
    ],
)
def test_a_stored_value_that_is_not_ours_is_never_handed_out(value):
    assert svc.stored_photo_key(value) is None


async def test_admin_links_are_signed_per_request_for_both_shapes(monkeypatch):
    new_key = f"enquiries/{uuid.uuid4()}/{uuid.uuid4()}.jpg"
    legacy_name = f"{uuid.uuid4()}.png"
    signed: list[str] = []

    async def fake_sign(key, *, expires_seconds):
        signed.append(key)
        return f"https://signed.example/{key}"

    monkeypatch.setattr(private_documents, "sign_or_none", fake_sign)
    enquiry = SimpleNamespace(
        reference_image_urls=[
            new_key,
            f"{GCS}/{PUBLIC}/enquiries/{legacy_name}",
            "https://example.com/phish.jpg",
        ]
    )

    urls = await svc.signed_photo_urls(enquiry)

    assert signed == [new_key, f"enquiries/legacy/{legacy_name}"]
    assert urls == [f"https://signed.example/{k}" for k in signed]


async def test_a_photo_that_cannot_be_signed_is_left_out(monkeypatch):
    async def fake_sign(key, *, expires_seconds):
        return None

    monkeypatch.setattr(private_documents, "sign_or_none", fake_sign)
    enquiry = SimpleNamespace(
        reference_image_urls=[f"enquiries/{uuid.uuid4()}/{uuid.uuid4()}.jpg"]
    )
    assert await svc.signed_photo_urls(enquiry) == []


# ─── The submit moves pending uploads under the enquiry ──────────────────────


class _Bucket:
    """A fake private bucket behind `object_storage`."""

    def __init__(self, keys=(), fail_copy_to: str | None = None):
        self.objects = set(keys)
        self.fail_copy_to = fail_copy_to
        self.copies: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    def exists(self, *, bucket, key):
        assert bucket == PRIVATE
        return key in self.objects

    def copy(self, *, bucket, src_key, dst_key):
        assert bucket == PRIVATE
        if src_key not in self.objects or dst_key == self.fail_copy_to:
            raise RuntimeError("copy failed")
        self.copies.append((src_key, dst_key))
        self.objects.add(dst_key)

    def delete(self, *, bucket, key):
        assert bucket == PRIVATE
        self.deletes.append(key)
        self.objects.discard(key)


@pytest.fixture
def bucket(monkeypatch):
    b = _Bucket()
    store = private_documents.object_storage
    monkeypatch.setattr(store, "object_exists", b.exists)
    monkeypatch.setattr(store, "copy_object", b.copy)
    monkeypatch.setattr(store, "delete_object", b.delete)
    return b


def _db(enquiry_id: uuid.UUID):
    db = MagicMock()
    added: list = []

    def add(obj):
        added.append(obj)

    async def flush():
        for obj in added:
            obj.id = enquiry_id

    db.add = add
    db.flush = AsyncMock(side_effect=flush)
    db.refresh = AsyncMock()
    db.added = added
    return db


def _body(refs: list[str]) -> CustomOrderEnquiryCreate:
    return CustomOrderEnquiryCreate(
        customer_name="Sara",
        customer_phone="0501234567",
        description="Pistachio, three tiers",
        reference_image_urls=refs,
    )


async def test_submit_moves_each_pending_upload_under_the_enquiry(bucket):
    a, b = _pending(), _pending(".png")
    bucket.objects |= {a, b}
    enquiry_id = uuid.uuid4()
    db = _db(enquiry_id)

    # One as a bare key, one as the signed URL an older storefront echoes back,
    # and a duplicate that must not be moved twice.
    enquiry = await svc.create(
        db, data=_body([a, f"{GCS}/{PRIVATE}/{b}?X-Goog-Signature=x", a])
    )

    want = [
        f"enquiries/{enquiry_id}/{a.rsplit('/', 1)[1]}",
        f"enquiries/{enquiry_id}/{b.rsplit('/', 1)[1]}",
    ]
    assert enquiry.reference_image_urls == want
    assert bucket.copies == [(a, want[0]), (b, want[1])]
    assert bucket.deletes == [a, b]
    assert bucket.objects == set(want)


async def test_submit_without_photos_touches_no_storage(bucket):
    db = _db(uuid.uuid4())
    enquiry = await svc.create(db, data=_body([]))
    assert enquiry.reference_image_urls == []
    assert bucket.copies == [] and bucket.deletes == []


async def test_a_key_with_no_object_behind_it_is_a_400_and_writes_nothing(bucket):
    real, missing = _pending(), _pending()
    bucket.objects.add(real)
    db = _db(uuid.uuid4())

    with pytest.raises(BadRequestError):
        await svc.create(db, data=_body([real, missing]))

    assert db.added == []
    assert bucket.copies == [] and bucket.deletes == []


async def test_a_foreign_url_is_a_400_before_any_storage_call(bucket):
    db = _db(uuid.uuid4())
    with pytest.raises(BadRequestError):
        await svc.create(db, data=_body(["https://evil.example/x.jpg"]))
    assert db.added == []


async def test_a_failed_copy_undoes_the_copies_and_keeps_the_pending_uploads(bucket):
    a, b = _pending(), _pending()
    bucket.objects |= {a, b}
    enquiry_id = uuid.uuid4()
    bucket.fail_copy_to = f"enquiries/{enquiry_id}/{b.rsplit('/', 1)[1]}"

    with pytest.raises(BadGatewayError):
        await svc.create(_db(enquiry_id), data=_body([a, b]))

    # The copy of `a` is gone again, and both uploads are still there for the
    # customer's retry.
    assert bucket.objects == {a, b}


# ─── The public upload endpoint ──────────────────────────────────────────────


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


class _Upload:
    def __init__(self, content_type: str, body: bytes):
        self.content_type = content_type
        self._body = body

    async def read(self) -> bytes:
        return self._body


@pytest.fixture
def uploads_recorded(monkeypatch):
    calls: list[dict] = []

    def fake_upload(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(private_documents.object_storage, "upload_object", fake_upload)
    monkeypatch.setattr(
        private_documents.object_storage,
        "signed_url",
        lambda *, bucket, key, expires_seconds: f"{GCS}/{bucket}/{key}?sig=1",
    )
    return calls


# The route is wrapped by the rate limiter; exercise the handler itself.
_upload_route = routes.upload_enquiry_image.__wrapped__


async def test_upload_goes_to_the_private_bucket_under_pending(uploads_recorded):
    res = await _upload_route(
        request=SimpleNamespace(), file=_Upload("image/jpeg", _jpeg_bytes())
    )

    assert len(uploads_recorded) == 1
    call = uploads_recorded[0]
    assert call["bucket"] == PRIVATE
    assert call["cache_control"] == "private, no-store"
    assert call["key"] == res.key
    assert svc.pending_key_from_reference(res.key) == res.key
    # The signed link it returns resolves back to the same pending key.
    assert svc.pending_key_from_reference(res.url) == res.key


async def test_upload_of_something_that_is_not_an_image_is_refused(
    uploads_recorded,
):
    with pytest.raises(BadRequestError):
        await _upload_route(
            request=SimpleNamespace(),
            file=_Upload("image/jpeg", b"<html><script>alert(1)</script></html>"),
        )
    assert uploads_recorded == []


# ─── The staff email no longer carries image links ───────────────────────────


def test_staff_email_counts_photos_and_links_to_the_admin(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_URL", "https://admin.example.com/")
    key = f"enquiries/{uuid.uuid4()}/{uuid.uuid4()}.jpg"
    html = email_service._render(
        "custom_order_enquiry.html",
        "shop@example.com",
        locale="en",
        customer_name="Sara",
        customer_phone="+971501234567",
        description="Pistachio",
        approx_kg=None,
        delivery_by=None,
        photo_count=2,
        enquiries_url=email_service._admin_enquiries_url(),
        submitted_at=datetime(2026, 11, 11, tzinfo=UTC).isoformat(),
    )
    assert "2 photos attached" in html
    assert 'href="https://admin.example.com/enquiries"' in html
    assert "storage.googleapis.com" not in html
    assert key not in html
