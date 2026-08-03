"""
Uploads used to land in the bucket exactly as the admin's camera produced them.

The catalogue came in from Foodics as 2048px JPEGs averaging 350KB, rendered
into product cards no wider than ~480 CSS px. Every one of those was a full
original that the image optimiser had to pull and decode before it could serve
the first visitor a thumbnail — a cold transform measured 3.1s to first byte.

The catalogue was re-encoded in place. This covers the other half: that the
next upload cannot put a 4000px original back, and that squeezing it does not
quietly corrupt anything on the way — a rotated phone photo must not land
sideways, a logo with transparency must not come out with a black background,
and an image the encoder cannot improve must be stored exactly as received.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.core.images import MAX_DIMENSION, extension_for, optimize_image


def _jpeg(size: tuple[int, int], colour=(200, 150, 120), quality: int = 95) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _png(size: tuple[int, int], alpha: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (200, 150, 120, alpha)).save(buf, "PNG")
    return buf.getvalue()


def _photographic_png(size: tuple[int, int], alpha: int) -> bytes:
    """An RGBA PNG with photographic detail in it.

    A flat colour is the one thing PNG genuinely beats JPEG on, so a flat test
    image exercises the "keep whichever is smaller" guard rather than the
    format choice. Real photographs — which is what the two `person_shot` PNGs
    were — carry noise, so build noise.
    """
    import random

    rng = random.Random(20260803)
    im = Image.new("RGBA", size)
    im.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256), alpha)
            for _ in range(size[0] * size[1])
        ]
    )
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_oversized_photo_is_capped_and_shrunk():
    raw = _jpeg((4000, 3000))
    out, content_type = optimize_image(raw, "image/jpeg")

    assert content_type == "image/jpeg"
    assert len(out) < len(raw)
    assert max(Image.open(io.BytesIO(out)).size) == MAX_DIMENSION


def test_image_within_the_cap_is_not_enlarged():
    raw = _jpeg((800, 600))
    out, _ = optimize_image(raw, "image/jpeg")

    assert Image.open(io.BytesIO(out)).size == (800, 600)


def test_transparency_survives_and_stays_png():
    out, content_type = optimize_image(_png((2400, 2400), alpha=0), "image/png")

    assert content_type == "image/png"
    assert extension_for(content_type) == ".png"
    assert Image.open(io.BytesIO(out)).mode == "RGBA"


def test_opaque_png_becomes_a_jpeg_and_is_named_like_one():
    # A photograph stored as PNG is just an expensive JPEG. Both `person_shot`
    # files in the storefront were exactly this: ~350KB for a 514px image.
    out, content_type = optimize_image(
        _photographic_png((600, 600), alpha=255), "image/png"
    )

    assert content_type == "image/jpeg"
    assert extension_for(content_type) == ".jpg"
    assert Image.open(io.BytesIO(out)).format == "JPEG"


def test_exif_rotation_is_applied_before_the_tag_is_dropped():
    # Orientation 6 means "rotate 90° clockwise on display". Strip the tag
    # without acting on it and a portrait phone photo lands on its side.
    im = Image.new("RGB", (1200, 600), (10, 20, 30))
    exif = im.getexif()
    exif[274] = 6
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)

    out, _ = optimize_image(buf.getvalue(), "image/jpeg")

    assert Image.open(io.BytesIO(out)).size == (600, 1200)


def test_a_file_the_encoder_cannot_improve_is_returned_untouched():
    raw = _jpeg((120, 120), quality=20)
    out, content_type = optimize_image(raw, "image/jpeg")

    assert out is raw or len(out) < len(raw)
    if out is raw:
        assert content_type == "image/jpeg"


def test_unreadable_bytes_do_not_cost_the_admin_the_upload():
    raw = b"this is not an image"
    out, content_type = optimize_image(raw, "image/jpeg")

    assert out == raw
    assert content_type == "image/jpeg"


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("image/jpeg", ".jpg"),
        ("image/png", ".png"),
        ("image/webp", ".webp"),
        ("application/octet-stream", ".jpg"),
    ],
)
def test_extension_follows_the_stored_content_type(content_type, expected):
    assert extension_for(content_type) == expected
