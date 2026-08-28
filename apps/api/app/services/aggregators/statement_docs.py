"""Archive statement invoice documents onto object storage.

Settlement invoices (PDF / CSV / XLSX / ZIP) are the documents finance uses to
claim VAT on marketplace fees. Providers already download these bytes to parse
lines; this module persists them under a private prefix so the shop keeps a
copy independent of the portal.

Storage: Cloudflare R2 (same credentials as catalogue images), but under a
**private** prefix — never the public CDN URL. Keys look like:

    aggregator-statements/{channel}/{statement_id}/{filename}

`aggregator_statement.invoice_object_key` holds that key; content type and
fetch time sit beside it. Multi-file channels (Talabat zip + PDF) can also
append to `invoice_attachments` JSONB.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Private prefix — must not be served via CLOUDFLARE_R2_PUBLIC_URL for VAT docs.
_PREFIX = "aggregator-statements"


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


def _r2_ready() -> bool:
    return bool(
        settings.CLOUDFLARE_R2_ENDPOINT
        and settings.CLOUDFLARE_R2_ACCESS_KEY
        and settings.CLOUDFLARE_R2_SECRET_KEY
        and settings.CLOUDFLARE_R2_BUCKET
    )


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT,
        aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY,
        aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_KEY,
        region_name="auto",
    )


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
    """Upload primary (and optional extra) statement invoice bytes to private R2.

    Returns None when R2 is not configured or the body is empty — callers treat
    that as "parse without archive", not a hard failure of the finance sweep.
    """
    if not body:
        return None
    if not _r2_ready():
        logger.warning(
            "statement invoice archive skipped — R2 credentials not configured"
        )
        return None

    key = object_key(channel, statement_id, filename)
    fetched_at = datetime.now(timezone.utc)
    client = _client()
    try:
        client.put_object(
            Bucket=settings.CLOUDFLARE_R2_BUCKET,
            Key=key,
            Body=body,
            ContentType=content_type,
            # Private VAT docs — no public cache headers.
            CacheControl="private, no-store",
            Metadata={
                "channel": channel,
                "statement_id": statement_id,
                "kind": "statement-invoice",
            },
        )
    except (BotoCoreError, ClientError):
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
            client.put_object(
                Bucket=settings.CLOUDFLARE_R2_BUCKET,
                Key=extra_key,
                Body=extra_body,
                ContentType=extra_type,
                CacheControl="private, no-store",
                Metadata={
                    "channel": channel,
                    "statement_id": statement_id,
                    "kind": "statement-invoice-attachment",
                },
            )
            attachments.append(
                {
                    "object_key": extra_key,
                    "content_type": extra_type,
                    "original_filename": extra_name,
                    "size_bytes": len(extra_body),
                }
            )
        except (BotoCoreError, ClientError):
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
    if not object_key or not _r2_ready():
        return None
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.CLOUDFLARE_R2_BUCKET,
                "Key": object_key,
            },
            ExpiresIn=expires_seconds,
        )
    except (BotoCoreError, ClientError):
        logger.exception("presign failed for %s", object_key)
        return None
