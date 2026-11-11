"""
Storing a custom-order enquiry, and the private photos it carries.

An enquiry is a message, not an order; the shop reads it and decides, and if it
goes ahead converts it into a custom order (`orders.custom_order_service`). So
this normalises the phone number, writes the row, and files the inspiration
photos. It `flush()`es and lets the request dependency commit, per the
transaction convention.

**Photos are private.** They are customers' own pictures — sometimes of
themselves, their children, their home — uploaded by an unauthenticated form,
so they live in the private finance bucket (`settings.GCS_INVOICE_BUCKET`:
uniform access, public-access prevention) and are only ever read through a
short-lived signed URL minted for an authorised admin request. Object layout:

* ``enquiries/pending/<uuid4>.<jpg|png|webp>`` — written by the public upload
  endpoint before the form is submitted. Unreferenced until a submit claims it;
  a GCS lifecycle rule deletes anything under this prefix older than 2 days, so
  an upload whose form is never sent cleans itself up.
* ``enquiries/<enquiry_id>/<uuid4>.<ext>`` — where a submitted enquiry's photos
  are moved (copy + delete). The row stores these **keys**, never URLs.
* ``enquiries/legacy/<uuid4>.<ext>`` — rows written before photos went private
  hold full public URLs ``https://storage.googleapis.com/<GCS_IMAGE_BUCKET>/
  enquiries/<name>``; those objects were copied here, and the reader maps the
  old URL onto this key.

`reference_image_urls` keeps its name although it now holds keys: the
storefront (Vercel) and the API (the VM) deploy at different times, so the
submit still accepts the signed URL an older storefront echoes back.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import private_documents
from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.core.phone import normalise_phone
from app.models.custom_order_enquiry import CustomOrderEnquiry
from app.schemas.custom_order_enquiry import CustomOrderEnquiryCreate

logger = logging.getLogger(__name__)

#: Every enquiry photo lives under this prefix of the private bucket.
PHOTO_PREFIX = "enquiries/"
#: Uploads not yet claimed by a submitted enquiry. A GCS lifecycle rule on the
#: private bucket deletes objects under this prefix older than 2 days.
PENDING_PREFIX = "enquiries/pending/"
#: Where the photos of rows written before this change were copied to.
LEGACY_PREFIX = "enquiries/legacy/"

#: How long an admin's link to a photo works. Long enough to read an enquiry
#: and convert it; never persisted.
SIGNED_URL_SECONDS = 3600

#: `<uuid4>.<ext>` — the only object name this API ever mints for a photo.
#: The extensions are the ones `extension_for` can return.
_NAME = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r"\.(?:jpg|png|webp)"
)
_PENDING_KEY_RE = re.compile(rf"enquiries/pending/{_NAME}")
_STORED_KEY_RE = re.compile(
    rf"enquiries/(?:legacy|[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}"
    rf"-[0-9a-f]{{12}})/{_NAME}"
)
_NAME_RE = re.compile(_NAME)

_GCS_HOST = "storage.googleapis.com"

_BAD_PHOTO = (
    "One of the photos could not be attached. Please remove it, add it again "
    "and resend your request."
)


def new_pending_key(ext: str) -> str:
    """Name a fresh upload. Nothing references it until a submit claims it —
    the bucket's lifecycle rule on `enquiries/pending/` (delete after 2 days)
    is what removes one whose form is never sent."""
    return f"{PENDING_PREFIX}{uuid.uuid4()}{ext}"


def pending_key_from_reference(value: str) -> str:
    """The pending object a submitted `reference_image_urls` entry names, or 400.

    Accepted: the bare key ``enquiries/pending/<uuid4>.<jpg|png|webp>``, or that
    key as a URL in the private bucket — ``https://storage.googleapis.com/
    <GCS_INVOICE_BUCKET>/<key>`` with any query string (the signature an older
    storefront echoes back) ignored. Nothing else: no other host, bucket or
    prefix, no traversal, no name we did not mint. These strings end up in an
    admin's page, so anything we did not create is refused, not stored.
    """
    candidate = value
    if "://" in value:
        try:
            parts = urlsplit(value)
        except ValueError:
            raise BadRequestError(_BAD_PHOTO) from None
        prefix = f"/{settings.GCS_INVOICE_BUCKET}/"
        if (
            parts.scheme != "https"
            or parts.netloc != _GCS_HOST
            or not parts.path.startswith(prefix)
        ):
            raise BadRequestError(_BAD_PHOTO)
        candidate = parts.path[len(prefix) :]
    if not _PENDING_KEY_RE.fullmatch(candidate):
        raise BadRequestError(_BAD_PHOTO)
    return candidate


def stored_photo_key(value: str) -> str | None:
    """The private-bucket key a stored `reference_image_urls` entry refers to.

    Two shapes are on disk: a key this module wrote
    (``enquiries/<enquiry_id>/<name>``), and a pre-private public URL
    (``https://storage.googleapis.com/<GCS_IMAGE_BUCKET>/enquiries/<name>``),
    whose object now lives at ``enquiries/legacy/<name>``. Anything else is not
    ours and maps to None — it is never handed to a browser.
    """
    if _STORED_KEY_RE.fullmatch(value):
        return value
    legacy = f"https://{_GCS_HOST}/{settings.GCS_IMAGE_BUCKET}/{PHOTO_PREFIX}"
    if value.startswith(legacy):
        name = value[len(legacy) :]
        if _NAME_RE.fullmatch(name):
            return f"{LEGACY_PREFIX}{name}"
    return None


async def signed_photo_urls(enquiry: CustomOrderEnquiry) -> list[str]:
    """Short-lived links to an enquiry's photos, minted per request.

    Never persisted. A photo whose stored value is not ours, or whose link
    could not be signed, is left out rather than failing the whole read.
    """
    keys = [
        key
        for key in (stored_photo_key(v) for v in enquiry.reference_image_urls or [])
        if key is not None
    ]
    urls = await asyncio.gather(
        *(
            private_documents.sign_or_none(k, expires_seconds=SIGNED_URL_SECONDS)
            for k in keys
        )
    )
    return [u for u in urls if u]


async def _claim_photos(enquiry_id: uuid.UUID, pending: list[str]) -> list[str]:
    """Move each pending upload under the enquiry, returning the new keys.

    Copy all, then delete the sources: if a copy fails, the copies already made
    are removed and the pending uploads are untouched, so the customer's retry
    of the same submit still works. A source that fails to delete only lingers
    until the pending lifecycle rule takes it.
    """
    claimed: list[str] = []
    try:
        for src in pending:
            dst = f"{PHOTO_PREFIX}{enquiry_id}/{src[len(PENDING_PREFIX) :]}"
            await private_documents.copy(src, dst, "photo")
            claimed.append(dst)
    except Exception:
        for dst in claimed:
            await private_documents.delete_quietly(dst)
        raise
    for src in pending:
        await private_documents.delete_quietly(src)
    return claimed


async def create(
    db: AsyncSession, *, data: CustomOrderEnquiryCreate
) -> CustomOrderEnquiry:
    """Persist one enquiry and file its photos. Creates no order, holds no slot."""
    # Validate every reference before writing anything: a 400 must leave no row
    # and move no object.
    pending = list(
        dict.fromkeys(pending_key_from_reference(v) for v in data.reference_image_urls)
    )
    for key in pending:
        if not await private_documents.exists(key):
            raise BadRequestError(_BAD_PHOTO)

    enquiry = CustomOrderEnquiry(
        customer_name=data.customer_name,
        # Store a normalised number when we can recognise one, but never lose the
        # customer's own digits if we can't — a lead we can't call back is worse
        # than an oddly formatted one.
        customer_phone=normalise_phone(data.customer_phone) or data.customer_phone,
        description=data.description,
        approx_kg=data.approx_kg,
        reference_image_urls=[],
        delivery_by=data.delivery_by,
    )
    db.add(enquiry)
    # The id names the photos' folder, so the row has to exist first.
    await db.flush()

    if pending:
        enquiry.reference_image_urls = await _claim_photos(enquiry.id, pending)
        await db.flush()
    await db.refresh(enquiry)
    return enquiry
