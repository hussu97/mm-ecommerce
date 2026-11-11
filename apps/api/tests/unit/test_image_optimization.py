"""
Uploads used to land in the bucket exactly as the admin's camera produced them.

The catalogue came in from Foodics as 2048px JPEGs averaging 350KB, rendered
into product cards no wider than ~480 CSS px. Every one of those was a full
original that the image optimiser had to pull and decode before it could serve
the first visitor a thumbnail — a cold transform measured 3.1s to first byte.

The catalogue was re-encoded in place. This covers the other half: that the
next upload cannot put a 4000px original back, and that squeezing it does not
quietly corrupt anything on the way — a rotated phone photo must not land
sideways, a logo with transparency must not come out with a black background.

And it fails closed: what is stored is always the re-encode — never the upload's
own bytes, which may not be an image at all, or may carry a phone's EXIF block
with the GPS fix of where the photo was taken.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.core.exceptions import BadRequestError
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


def _heic(size: tuple[int, int], colour=(180, 40, 60), quality: int = 60) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "HEIF", quality=quality)
    return buf.getvalue()


def test_iphone_heic_is_decoded_and_stored_as_jpeg():
    """An iPhone's native photo format must not land in the bucket as .heic —
    browsers cannot render it. pillow-heif decodes it and it comes out JPEG."""
    raw = _heic((3000, 2000))
    out, content_type = optimize_image(raw, "image/heic")

    assert content_type == "image/jpeg"
    assert extension_for(content_type) == ".jpg"
    assert Image.open(io.BytesIO(out)).format == "JPEG"
    assert max(Image.open(io.BytesIO(out)).size) == MAX_DIMENSION


def test_heic_is_never_kept_even_when_its_jpeg_reencode_is_larger():
    """A small flat HEIC can compress tighter than the JPEG we make from it. The
    'keep whichever is smaller' shortcut must not apply to a non-web type, or we
    would store a .heic no browser can show."""
    raw = _heic((64, 64))
    out, content_type = optimize_image(raw, "image/heic")

    assert content_type == "image/jpeg"
    assert Image.open(io.BytesIO(out)).format == "JPEG"


def test_a_tiny_already_tuned_jpeg_is_still_re_encoded():
    """Keeping the original when the re-encode is not smaller would keep its
    metadata too, so the re-encode is stored even when it is larger."""
    raw = _jpeg((120, 120), quality=20)
    out, content_type = optimize_image(raw, "image/jpeg")

    assert out is not raw
    assert content_type == "image/jpeg"
    assert Image.open(io.BytesIO(out)).format == "JPEG"


@pytest.mark.parametrize(
    "raw",
    [
        b"this is not an image",
        b"%PDF-1.7\n1 0 obj <<>> endobj",
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"",
    ],
)
def test_bytes_pillow_cannot_decode_are_refused_not_stored(raw):
    with pytest.raises(BadRequestError):
        optimize_image(raw, "image/jpeg")


def test_a_truncated_jpeg_is_refused():
    raw = _jpeg((800, 600))[:200]
    with pytest.raises(BadRequestError):
        optimize_image(raw, "image/jpeg")


def _gps_jpeg() -> bytes:
    """A phone-style photo: EXIF with a GPS fix, a camera model and XMP."""
    im = Image.new("RGB", (900, 600), (90, 120, 150))
    exif = im.getexif()
    exif[0x0110] = "iPhone 15 Pro"  # Model
    gps = exif.get_ifd(0x8825)  # GPSInfo
    gps[1] = "N"
    gps[2] = (25.0, 12.0, 30.0)
    gps[3] = "E"
    gps[4] = (55.0, 16.0, 45.0)
    buf = io.BytesIO()
    im.save(
        buf,
        "JPEG",
        exif=exif,
        xmp=b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>home</x:xmpmeta>",
    )
    return buf.getvalue()


def test_exif_gps_is_stripped_from_a_phone_photo():
    raw = _gps_jpeg()
    # The fixture really carries what we are about to strip.
    with Image.open(io.BytesIO(raw)) as src:
        assert src.getexif().get_ifd(0x8825)
    assert b"xmpmeta" in raw

    out, content_type = optimize_image(raw, "image/jpeg")

    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(out)) as im:
        assert not im.getexif().get_ifd(0x8825)
        assert 0x0110 not in im.getexif()
        assert "exif" not in im.info
        assert "xmp" not in im.info
    assert b"iPhone" not in out
    assert b"xmpmeta" not in out


def test_metadata_is_stripped_from_a_transparent_png_too():
    im = Image.new("RGBA", (300, 300), (200, 150, 120, 0))
    exif = im.getexif()
    exif[0x0110] = "iPhone 15 Pro"
    from PIL.PngImagePlugin import PngInfo

    text = PngInfo()
    text.add_text("Comment", "123 Secret Street")
    buf = io.BytesIO()
    im.save(buf, "PNG", exif=exif, pnginfo=text)
    raw = buf.getvalue()
    assert b"iPhone" in raw and b"Secret Street" in raw

    out, content_type = optimize_image(raw, "image/png")

    assert content_type == "image/png"
    assert b"iPhone" not in out
    assert b"Secret Street" not in out


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
