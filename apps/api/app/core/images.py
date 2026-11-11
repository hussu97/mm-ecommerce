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

from app.core.exceptions import BadRequestError

# Register HEIC/HEIF decoding into Pillow. An iPhone's default photo format is
# HEIC; Safari transcodes it to JPEG when picked from the Photos library, but a
# raw .heic uploaded from the Files app / iCloud Drive arrives undecodable to
# stock Pillow. With the opener registered, `Image.open` reads it and the normal
# re-encode below turns it into a JPEG, so callers never store a .heic that a
# browser cannot render.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - absence just means no HEIC decode
    pass

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

    Fails closed. Everything this feeds lands in a bucket somebody can read —
    the public image bucket, or a customer's enquiry photo a member of staff
    opens — so the stored bytes are *always* Pillow's re-encode, never the
    upload itself:

    * a file Pillow cannot decode is refused (400) rather than stored raw. The
      declared content type is only the browser's say-so; this used to fall
      back to the original bytes, which let anything at all be published
      under an image extension;
    * the re-encode is kept even when it comes out larger than the original.
      It used to keep whichever was smaller, and the original of a phone photo
      still carries its EXIF block — GPS coordinates of somebody's home
      included. Pillow writes no EXIF/XMP/text chunks unless asked, so the
      re-encode is the metadata strip.

    The ``content_type`` argument is unused beyond the signature callers
    already pass; the output type follows the pixels (PNG only where real
    transparency needs keeping, JPEG otherwise).
    """
    del content_type  # the output type is decided by the decoded image
    try:
        with Image.open(io.BytesIO(data)) as im:
            # A phone photo carries its rotation in EXIF. Apply it before the tag
            # is dropped, or the image lands on the site sideways.
            im = ImageOps.exif_transpose(im)

            im.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
            has_alpha = _has_real_transparency(im)

            buf = io.BytesIO()
            if has_alpha:
                # Transparency has to survive; PNG is the only one of the
                # stored types that keeps it without a format change.
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
    except Exception as exc:
        raise BadRequestError(
            "That file could not be read as an image. Please upload a JPG, "
            "PNG, WebP or HEIC photo."
        ) from exc

    return buf.getvalue(), out_type


def extension_for(content_type: str) -> str:
    """The file extension to store an object under for a given content type."""
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".jpg")
