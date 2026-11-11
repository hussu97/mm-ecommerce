"""Async plumbing for a private document: a scan or PDF in the finance bucket.

Validate → upload → sign on read → delete quietly, each off the event loop
(GCS and the IAM signBlob call are blocking network I/O). Never public: the
bucket is uniform-access with public-access prevention, so the only way to a
document is a short-lived signed URL minted for an authorised request.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from app.core import file_sniff, object_storage
from app.core.config import settings
from app.core.exceptions import BadGatewayError

logger = logging.getLogger(__name__)

MAX_BYTES = file_sniff.MAX_BYTES
EXTENSIONS = file_sniff.DOCUMENT_TYPES
IMAGE_EXTENSIONS = file_sniff.IMAGE_TYPES


def validate(
    body: bytes,
    content_type: str,
    label: str,
    *,
    allowed: dict[str, str] = EXTENSIONS,
) -> tuple[str, str]:
    """The ``(content_type, extension)`` to store *body* under, or a 400 naming
    *label*. The type is the one the bytes really are (``file_sniff``), so a
    mislabelled genuine scan is stored truthfully and a disguised file refused."""
    return file_sniff.resolve(body, content_type, label, allowed=allowed)


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


@dataclass(frozen=True)
class SignedFile:
    """One private document, signed twice: `url` shows it in the page (an
    `<img>` or `<iframe>`), `download_url` saves it as `filename`."""

    url: str
    download_url: str
    filename: str
    content_type: str | None


def download_name(title: str, key: str, content_type: str | None) -> str:
    """`title` made safe for a file name, with the object's extension."""
    ext = EXTENSIONS.get(content_type or "") or (
        "." + key.rsplit(".", 1)[1] if "." in key.rsplit("/", 1)[-1] else ""
    )
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title)
    stem = re.sub(r"\s+", " ", stem).strip(" .") or "document"
    return f"{stem}{ext}"


async def sign_file(key: str, *, title: str, content_type: str | None) -> SignedFile:
    """Sign *key* for viewing and for download (two IAM signBlob calls, run
    together)."""
    filename = download_name(title, key, content_type)
    view, download = await asyncio.gather(
        asyncio.to_thread(
            object_storage.signed_url, bucket=settings.GCS_INVOICE_BUCKET, key=key
        ),
        asyncio.to_thread(
            object_storage.signed_url,
            bucket=settings.GCS_INVOICE_BUCKET,
            key=key,
            download_filename=filename,
        ),
    )
    if view is None or download is None:
        raise BadGatewayError("Could not sign a download link for the document")
    return SignedFile(view, download, filename, content_type)


async def delete_quietly(key: str) -> None:
    """Delete an object nothing points at any more. The row is already right; a
    failed delete only strands an object in a private bucket, so log it."""
    try:
        await asyncio.to_thread(
            object_storage.delete_object, bucket=settings.GCS_INVOICE_BUCKET, key=key
        )
    except Exception:
        logger.warning("could not delete private document %s", key, exc_info=True)
