"""What a private upload really is, read from its bytes rather than its label.

Every private document — an employee's photo or ID, a supplier's licence, a
legal entity's VAT certificate, a purchase-order invoice from the admin or the
till — arrives with a ``Content-Type`` the client chose. Checking that label
against an allowlist says nothing about the bytes: an HTML page or an SVG with
script in it, labelled ``image/png``, sailed through, landed in the finance
bucket under that type, and was served back to staff through a signed URL.

So the bytes decide. Each allowed type has a magic-number signature:

* JPEG — ``FF D8 FF``
* PNG  — ``89 50 4E 47 0D 0A 1A 0A``
* WebP — ``RIFF`` + 4-byte size + ``WEBP``
* PDF  — ``%PDF-`` anywhere in the first 1024 bytes (the spec tolerates
  leading junk before the header, and real scanners write some)

A body that matches none of the field's allowed types is rejected. A body that
is a *different* allowed type than declared — a PNG the iPad labelled
``image/jpeg`` — is not: it is stored under the type it really is, with the
matching extension, because refusing a genuine document over a wrong label
only makes a cashier retake a photo of an invoice for nothing.
"""

from __future__ import annotations

from app.core.exceptions import BadRequestError

JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
PDF = "application/pdf"

#: What a private document may be, and the object-key extension each takes.
DOCUMENT_TYPES: dict[str, str] = {
    JPEG: ".jpg",
    PNG: ".png",
    WEBP: ".webp",
    PDF: ".pdf",
}

#: A photo of a person: images only.
IMAGE_TYPES: dict[str, str] = {k: v for k, v in DOCUMENT_TYPES.items() if k != PDF}

#: Same ceiling as a purchase-order invoice: a scan or PDF, not an archive.
MAX_BYTES = 10 * 1024 * 1024

#: How far into the body a PDF header may sit (PDF 1.7, §7.5.2 implementation
#: note: readers accept the header within the first 1024 bytes).
_PDF_HEADER_WINDOW = 1024


def sniff(body: bytes) -> str | None:
    """The content type *body*'s magic bytes say it is, or None if none match."""
    if body[:3] == b"\xff\xd8\xff":
        return JPEG
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return WEBP
    if b"%PDF-" in body[:_PDF_HEADER_WINDOW]:
        return PDF
    return None


def _kinds(allowed: dict[str, str]) -> str:
    return "JPEG, PNG or WebP" if PDF not in allowed else "JPEG, PNG, WebP or PDF"


def check_declared(
    content_type: str, label: str, *, allowed: dict[str, str] = DOCUMENT_TYPES
) -> None:
    """Reject a declared type outside *allowed* before the body is even read."""
    if content_type not in allowed:
        raise BadRequestError(f"{label} must be a {_kinds(allowed)}")


def resolve(
    body: bytes,
    content_type: str,
    label: str,
    *,
    allowed: dict[str, str] = DOCUMENT_TYPES,
) -> tuple[str, str]:
    """Validate an upload and return the ``(content_type, extension)`` to store.

    The declared type must be one of *allowed* (as before), the body must be
    non-empty and within ``MAX_BYTES``, and its bytes must really be one of
    *allowed*. The returned type is the detected one, which differs from the
    declared one only when the client mislabelled a genuine document.
    Every refusal is a ``BadRequestError`` naming *label*.
    """
    check_declared(content_type, label, allowed=allowed)
    if not body:
        raise BadRequestError(f"No {label.lower()} file was uploaded")
    if len(body) > MAX_BYTES:
        raise BadRequestError(f"{label} file is too large (max 10 MB)")
    detected = sniff(body)
    if detected is None or detected not in allowed:
        raise BadRequestError(
            f"{label} file is not a real {_kinds(allowed)} — its contents do not "
            "match any of those formats"
        )
    return detected, allowed[detected]
