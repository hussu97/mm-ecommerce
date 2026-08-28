"""Archive statement invoice documents onto object storage.

Settlement invoices (PDF / CSV / XLSX / ZIP) are the documents finance uses to
claim VAT on marketplace fees. Providers already download these bytes to parse
lines; this module persists them under a private prefix so the shop keeps a
copy independent of the portal.

Storage: the private Google Cloud Storage bucket `GCS_INVOICE_BUCKET`
(uniform access, public-access-prevention) — never a public URL. Keys look
like:

    invoices/{channel}/{statement_id}/{filename}

`aggregator_statement.invoice_object_key` holds that key; content type and
fetch time sit beside it. Multi-file channels (Talabat zip + PDF) can also
append to `invoice_attachments` JSONB. Downloads use short-lived V4 signed
URLs (see `presigned_get_url`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core import object_storage
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Private prefix — these are VAT docs and are never served publicly.
_PREFIX = "invoices"


@dataclass(frozen=True)
class StoredStatementInvoice:
    """What ingest writes onto `aggregator_statement` after an archive."""

    object_key: str
    content_type: str
    original_filename: str
    fetched_at: datetime
    size_bytes: int
    #: Extra files when the primary is a zip or the portal yields multiple docs.
    attachments: list[dict[str, Any]] | None = None


def _gcs_ready() -> bool:
    return bool(settings.GCS_INVOICE_BUCKET)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    return cleaned.strip("-")[:120] or "unknown"


def object_key(channel: str, statement_id: str, filename: str) -> str:
    return (
        f"{_PREFIX}/{_safe_segment(channel)}/"
        f"{_safe_segment(statement_id)}/{_safe_segment(filename)}"
    )


def store_statement_invoice(
    *,
    channel: str,
    statement_id: str,
    filename: str,
    body: bytes,
    content_type: str,
    extra_files: list[tuple[str, bytes, str]] | None = None,
) -> StoredStatementInvoice | None:
    """Upload primary (and optional extra) statement invoice bytes to private GCS.

    Returns None when GCS is not configured or the body is empty — callers treat
    that as "parse without archive", not a hard failure of the finance sweep.
    """
    if not body:
        return None
    if not _gcs_ready():
        logger.warning("statement invoice archive skipped — GCS bucket not configured")
        return None

    key = object_key(channel, statement_id, filename)
    fetched_at = datetime.now(timezone.utc)
    try:
        object_storage.upload_object(
            bucket=settings.GCS_INVOICE_BUCKET,
            key=key,
            body=body,
            content_type=content_type,
            # Private VAT docs — no public cache headers.
            cache_control="private, no-store",
        )
    except Exception:
        logger.exception(
            "failed to archive statement invoice %s/%s", channel, statement_id
        )
        return None

    attachments: list[dict[str, Any]] = []
    for extra_name, extra_body, extra_type in extra_files or []:
        if not extra_body:
            continue
        extra_key = object_key(channel, statement_id, extra_name)
        try:
            object_storage.upload_object(
                bucket=settings.GCS_INVOICE_BUCKET,
                key=extra_key,
                body=extra_body,
                content_type=extra_type,
                cache_control="private, no-store",
            )
            attachments.append(
                {
                    "object_key": extra_key,
                    "content_type": extra_type,
                    "original_filename": extra_name,
                    "size_bytes": len(extra_body),
                }
            )
        except Exception:
            logger.exception(
                "failed to archive statement attachment %s/%s/%s",
                channel,
                statement_id,
                extra_name,
            )

    return StoredStatementInvoice(
        object_key=key,
        content_type=content_type,
        original_filename=filename,
        fetched_at=fetched_at,
        size_bytes=len(body),
        attachments=attachments or None,
    )


def presigned_get_url(object_key: str, *, expires_seconds: int = 3600) -> str | None:
    """Short-lived download URL for an archived statement invoice (admin use)."""
    if not object_key or not _gcs_ready():
        return None
    return object_storage.signed_url(
        bucket=settings.GCS_INVOICE_BUCKET,
        key=object_key,
        expires_seconds=expires_seconds,
    )
