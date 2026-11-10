"""Async plumbing for a private document: a scan or PDF in the finance bucket.

Validate → upload → sign on read → delete quietly, each off the event loop
(GCS and the IAM signBlob call are blocking network I/O). Never public: the
bucket is uniform-access with public-access prevention, so the only way to a
document is a short-lived signed URL minted for an authorised request.
"""

from __future__ import annotations

import asyncio
import logging

from app.core import object_storage
from app.core.config import settings
from app.core.exceptions import BadGatewayError, BadRequestError

logger = logging.getLogger(__name__)

MAX_BYTES = object_storage.PRIVATE_DOCUMENT_MAX_BYTES
EXTENSIONS = object_storage.PRIVATE_DOCUMENT_EXT
IMAGE_EXTENSIONS = {k: v for k, v in EXTENSIONS.items() if k.startswith("image/")}


def validate(
    body: bytes,
    content_type: str,
    label: str,
    *,
    allowed: dict[str, str] = EXTENSIONS,
) -> str:
    """The object-key extension for *content_type*, or a 400 naming *label*."""
    ext = allowed.get(content_type)
    if ext is None:
        kinds = (
            "JPEG, PNG or WebP"
            if allowed is IMAGE_EXTENSIONS
            else "JPEG, PNG, WebP or PDF"
        )
        raise BadRequestError(f"{label} must be a {kinds}")
    if not body:
        raise BadRequestError(f"No {label.lower()} file was uploaded")
    if len(body) > MAX_BYTES:
        raise BadRequestError(f"{label} file is too large (max 10 MB)")
    return ext


async def upload(key: str, body: bytes, content_type: str, label: str) -> None:
    try:
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


async def sign(key: str) -> str:
    url = await asyncio.to_thread(
        object_storage.signed_url, bucket=settings.GCS_INVOICE_BUCKET, key=key
    )
    if url is None:
        raise BadGatewayError("Could not sign a download link for the document")
    return url


async def delete_quietly(key: str) -> None:
    """Delete an object nothing points at any more. The row is already right; a
    failed delete only strands an object in a private bucket, so log it."""
    try:
        await asyncio.to_thread(
            object_storage.delete_object, bucket=settings.GCS_INVOICE_BUCKET, key=key
        )
    except Exception:
        logger.warning("could not delete private document %s", key, exc_info=True)
