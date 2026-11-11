"""P2-12: a private upload's type comes from its bytes, not the client's label.

`file_sniff.resolve` is the one check every private-bucket upload path runs
(employee photo + documents, supplier and legal-entity paperwork, purchase-order
invoices from the admin and the till). A body that is none of the field's
allowed types is refused; a genuine document the client mislabelled is stored
under the type it really is.
"""

from __future__ import annotations

import pytest

from app.core import file_sniff
from app.core.exceptions import BadRequestError

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 32


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (WEBP, "image/webp"),
        (PDF, "application/pdf"),
    ],
)
def test_sniff_recognises_each_allowed_type(body, expected):
    assert file_sniff.sniff(body) == expected


def test_pdf_header_may_follow_leading_junk_within_1024_bytes():
    assert file_sniff.sniff(b"\x00" * 1000 + b"%PDF-1.4\n") == "application/pdf"
    # …but not past the window the PDF spec gives readers.
    assert file_sniff.sniff(b"\x00" * 1024 + b"%PDF-1.4\n") is None


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<!DOCTYPE html><html><script>alert(1)</script></html>",
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        b"PK\x03\x04\x14\x00\x08\x00",  # zip / docx / xlsx
        b"GIF89a\x01\x00\x01\x00",
        b"RIFF\x24\x00\x00\x00WAVEfmt ",  # RIFF, but not WebP
        b"RIFF",  # truncated
        b"\xff\xd8",  # truncated JPEG SOI
        b"%PDF",  # no version dash
        b"\x89PNG\r\n",  # truncated PNG signature
    ],
)
def test_sniff_rejects_everything_else(body):
    assert file_sniff.sniff(body) is None


@pytest.mark.parametrize(
    ("body", "declared", "expected"),
    [
        (JPEG, "image/jpeg", ("image/jpeg", ".jpg")),
        (PNG, "image/png", ("image/png", ".png")),
        (WEBP, "image/webp", ("image/webp", ".webp")),
        (PDF, "application/pdf", ("application/pdf", ".pdf")),
    ],
)
def test_resolve_accepts_a_correctly_labelled_document(body, declared, expected):
    assert file_sniff.resolve(body, declared, "Invoice") == expected


@pytest.mark.parametrize(
    ("body", "declared", "expected"),
    [
        (PNG, "image/jpeg", ("image/png", ".png")),
        (JPEG, "image/png", ("image/jpeg", ".jpg")),
        (PDF, "image/jpeg", ("application/pdf", ".pdf")),
        (JPEG, "application/pdf", ("image/jpeg", ".jpg")),
    ],
)
def test_resolve_retypes_a_mislabelled_genuine_document(body, declared, expected):
    """A real scan the iPad labelled wrongly is stored as what it is, not refused."""
    assert file_sniff.resolve(body, declared, "Invoice") == expected


@pytest.mark.parametrize(
    ("body", "declared"),
    [
        (b"<html><script>alert(1)</script></html>", "image/png"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/jpeg"),
        (b"PK\x03\x04\x14\x00\x08\x00", "application/pdf"),
        (b"just some text", "application/pdf"),
    ],
)
def test_resolve_refuses_a_disguised_file_naming_the_field(body, declared):
    with pytest.raises(BadRequestError) as exc:
        file_sniff.resolve(body, declared, "VAT certificate")
    assert "VAT certificate" in str(exc.value)
    assert "contents" in str(exc.value)


def test_resolve_refuses_a_pdf_where_only_images_are_allowed():
    with pytest.raises(BadRequestError) as exc:
        file_sniff.resolve(PDF, "image/jpeg", "Photo", allowed=file_sniff.IMAGE_TYPES)
    assert "Photo" in str(exc.value)
    assert "PDF" not in str(exc.value)


def test_resolve_keeps_the_declared_type_allowlist():
    with pytest.raises(BadRequestError, match="Invoice must be a JPEG"):
        file_sniff.resolve(PDF, "application/octet-stream", "Invoice")


def test_resolve_refuses_empty_and_oversized_bodies():
    with pytest.raises(BadRequestError, match="No invoice file"):
        file_sniff.resolve(b"", "application/pdf", "Invoice")
    with pytest.raises(BadRequestError, match="too large"):
        file_sniff.resolve(
            PDF + b"\x00" * file_sniff.MAX_BYTES, "application/pdf", "Invoice"
        )
