"""
Custom-cake enquiries: the storefront's "We cater to" form and the admin list.

An enquiry is a lead, never an order. The shop reads it and, if it goes ahead,
creates a custom order from it (the admin console's "Convert" action), which is
the only link between the two.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import object_storage
from app.core.config import settings
from app.core.deps import get_db
from app.core.exceptions import BadGatewayError, BadRequestError
from app.core.images import extension_for, optimize_image
from app.core.limiter import limiter
from app.core.permissions import require
from app.models.custom_order_enquiry import CustomOrderEnquiry
from app.models.user import User
from app.schemas.custom_order_enquiry import (
    MAX_REFERENCE_IMAGES,
    CustomOrderEnquiryCreate,
    CustomOrderEnquiryResponse,
    PaginatedCustomOrderEnquiries,
)
from app.services import (
    custom_order_enquiry_service,
    email_service,
    image_warm_service,
    turnstile_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter()

#: The reference photos an enquiry may carry. Image types the storefront can
#: produce from a camera or a photo library, capped at the same 5 MB the admin
#: uploader uses. HEIC/HEIF are here because that is an iPhone's native photo
#: format: Safari transcodes it to JPEG for a Photos-library pick, but a raw
#: .heic from the Files app / iCloud Drive arrives as-is. `optimize_image`
#: re-encodes both to JPEG (pillow-heif), so nothing browser-unrenderable is
#: ever stored.
_ENQUIRY_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
_ENQUIRY_IMAGE_MAX_BYTES = 5 * 1024 * 1024


async def _require_human(request: Request, token: str | None) -> None:
    """Refuse an enquiry form nobody filled in.

    The enquiry endpoints make us write a row and send mail to a number the
    caller chose, which is exactly what makes an unauthenticated form worth
    abusing. Off wherever no Turnstile secret is set (dev, tests); enforced in
    production. The refusal names no cause — a bot learns from a specific error.
    """
    ok, reason = await turnstile_service.verify(
        token, remote_ip=get_remote_address(request)
    )
    if ok:
        return
    logger.warning(
        "Turnstile refused a custom-order enquiry from %s: %s",
        get_remote_address(request),
        reason,
    )
    raise BadRequestError(
        "We couldn't verify that you're human. Please refresh the page and try again."
    )


# ─── Public: sending an enquiry (a lead, never a booking) ──────────────────────


@router.post(
    "/enquiry",
    response_model=CustomOrderEnquiryResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
async def submit_enquiry(
    request: Request,
    body: CustomOrderEnquiryCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Take a custom-order request from the "We cater to" section of the home page.

    This stores a lead and emails the shop — it does **not** create an order. A
    human reads it and decides whether it becomes a custom order, which is why
    the form tells the customer their delivery date is confirmed only after
    review.
    """
    await _require_human(request, body.turnstile_token)

    # Belt and braces: the schema caps this, but the boundary enforces it too.
    if len(body.reference_image_urls) > MAX_REFERENCE_IMAGES:
        raise BadRequestError(
            f"An enquiry can include at most {MAX_REFERENCE_IMAGES} photos."
        )

    enquiry = await custom_order_enquiry_service.create(db, data=body)
    # Flush the row so the email carries a real created_at (the "when it was
    # sent"); the request dependency still owns the commit.
    await db.flush()

    # Inline-await through the never-raise funnel (convention 5): a failed send is
    # journalled, never an exception that loses the lead we just stored.
    await email_service.send_custom_order_enquiry(enquiry=enquiry)

    return enquiry


@router.post(
    "/enquiry/image",
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def upload_enquiry_image(
    request: Request,
    file: UploadFile = File(...),
):
    """
    Upload one inspiration photo for a custom-order enquiry, returning its URL.

    Public because the person filling in the enquiry form has no account — the
    admin uploader at `/uploads/image` requires `catalogue.manage`, so it cannot
    be reused. Not Turnstile-guarded, and deliberately: a Turnstile solution is
    single-use, and one enquiry uploads up to four photos before it submits, so a
    token spent on the first upload would fail the rest. The human check lives on
    `/enquiry` — the request that actually stores a lead and sends mail. This
    endpoint only puts a re-encoded, downscaled, size- and type-capped image in
    the bucket, and a rate limit bounds the rest.
    """
    content_type = file.content_type or ""
    if content_type not in _ENQUIRY_IMAGE_CONTENT_TYPES:
        raise BadRequestError(
            f"Invalid file type '{content_type}'. Allowed: jpeg, png, webp"
        )

    contents = await file.read()
    if len(contents) > _ENQUIRY_IMAGE_MAX_BYTES:
        raise BadRequestError(
            f"File too large. Maximum size is {_ENQUIRY_IMAGE_MAX_BYTES // (1024 * 1024)} MB"
        )

    # Pillow decode/resize/encode is CPU work — keep it off the event loop so one
    # upload does not stall the worker for everyone else.
    contents, content_type = await asyncio.to_thread(
        optimize_image, contents, content_type
    )

    ext = extension_for(content_type)
    key = f"enquiries/{uuid.uuid4()}{ext}"
    try:
        await asyncio.to_thread(
            object_storage.upload_object,
            bucket=settings.GCS_IMAGE_BUCKET,
            key=key,
            body=contents,
            content_type=content_type,
            cache_control="public, max-age=31536000",
        )
    except Exception as e:
        raise BadGatewayError(f"Failed to upload image: {str(e)}")

    public_url = object_storage.public_url(settings.GCS_IMAGE_BUCKET, key)
    image_warm_service.warm_in_background([public_url])
    return {"url": public_url, "key": key}


# ─── Admin: enquiries (the storefront leads) ───────────────────────────────────


@admin_router.get("/enquiries", response_model=PaginatedCustomOrderEnquiries)
async def list_enquiries(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.custom.manage")),
):
    """The custom-order enquiries sent from the storefront, newest first.

    Read-only: these are leads someone answers by phone or email. Same
    permission as custom orders, since the same people handle both and an
    enquiry is converted into one.
    """
    total = int(
        (
            await db.execute(select(func.count()).select_from(CustomOrderEnquiry))
        ).scalar()
        or 0
    )
    rows = (
        (
            await db.execute(
                select(CustomOrderEnquiry)
                .order_by(CustomOrderEnquiry.created_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )
        .scalars()
        .all()
    )
    pages = (total + per_page - 1) // per_page if total else 0
    return PaginatedCustomOrderEnquiries(
        items=rows, total=total, page=page, per_page=per_page, pages=pages
    )
