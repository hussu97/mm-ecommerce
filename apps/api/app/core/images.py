"""Re-encoding for images on their way into object storage.

The product catalogue was originally imported straight from the Foodics feed,
which meant 2048px JPEGs averaging 350KB sitting behind every product card. The
storefront renders those into slots no wider than ~480 CSS px, and the Next.js
optimiser has to fetch and decode the whole original on every cold transform —
measured at 3.1s to first byte. The catalogue has since been re-encoded in
place, so this exists to stop the next admin upload from putting it back.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

# Wide enough for a full-bleed banner on a 2x desktop, and comfortably above any
# slot the storefront actually renders a product into.
MAX_DIMENSION = 1600
JPEG_QUALITY = 82

# Pillow will happily spend all available memory opening a hostile file. The
# route caps the upload at 5MB, but pixel count is the dimension that matters:
# a small file can decompress into an enormous bitmap.
Image.MAX_IMAGE_PIXELS = 50_000_000


def _has_real_transparency(im: Image.Image) -> bool:
    """Whether any pixel is actually not fully opaque.

    Carrying an alpha channel is not the same as using one. Both `person_shot`
    images on the about page were RGBA PNGs at ~350KB with every pixel opaque —
    a photograph paying for a channel it never touches. Going by mode alone
    keeps those as PNG and gives up almost all of the saving.
    """
    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")

    if im.mode not in ("RGBA", "LA", "PA"):
        return False

    alpha = im.getchannel("A")
    minimum, _ = alpha.getextrema()
    return minimum < 255


def optimize_image(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Downscale and re-encode an upload. Returns ``(bytes, content_type)``.

    Falls back to the original bytes whenever re-encoding would not help or the
    file cannot be read as an image — the caller has already validated the
    declared content type, and a failure here should not cost the admin an
    upload.
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            # A phone photo carries its rotation in EXIF. Apply it before the tag
            # is dropped, or the image lands on the site sideways.
            im = ImageOps.exif_transpose(im)

            im.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
            has_alpha = _has_real_transparency(im)

            buf = io.BytesIO()
            if has_alpha:
                # Transparency has to survive; PNG is the only one of the three
                # accepted types that keeps it without a format change.
                im.convert("RGBA").save(buf, "PNG", optimize=True)
                out_type = "image/png"
            else:
                im.convert("RGB").save(
                    buf,
                    "JPEG",
                    quality=JPEG_QUALITY,
                    optimize=True,
                    progressive=True,
                )
                out_type = "image/jpeg"
    except Exception:
        return data, content_type

    optimized = buf.getvalue()

    # An already-tuned asset can come out larger than it went in. Keep whichever
    # is smaller, and keep the original's type when we keep the original.
    if len(optimized) >= len(data):
        return data, content_type

    return optimized, out_type


def extension_for(content_type: str) -> str:
    """The file extension to store an object under for a given content type."""
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".jpg")
