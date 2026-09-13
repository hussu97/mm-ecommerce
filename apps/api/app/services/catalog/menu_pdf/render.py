"""Turn a MenuDocument into PDF bytes: fetch assets, then Jinja2 → WeasyPrint.

Two phases on purpose. `prepare_menu_assets` is async and does all the I/O —
every product image and the entity logo are fetched concurrently, downscaled and
inlined as data URIs, and the WhatsApp QR is generated — so that the layout pass
never touches the network. `render_menu_pdf` is then pure CPU (Jinja + WeasyPrint)
and is meant to run in a worker thread via `asyncio.to_thread`, off the event
loop, the way the image-upload route runs Pillow.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from functools import lru_cache
from pathlib import Path

import httpx

from .view_models import MenuDocument

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_FONTS_DIR = _HERE / "fonts"
_TEMPLATES_DIR = _HERE / "templates"

# Thumbnails print at ~2cm; a 260px source is crisp at 300dpi and keeps the PDF
# small even for a 60-item menu.
_THUMB_MAX = 260
_LOGO_MAX = 360
_FETCH_TIMEOUT = 8.0
_FETCH_CONCURRENCY = 8


# ── Asset preparation (async I/O) ──────────────────────────────────────────────


def _to_thumb_data_uri(raw: bytes, *, max_dim: int) -> str | None:
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)
            has_alpha = im.mode in ("RGBA", "LA", "P")
            im.thumbnail((max_dim, max_dim))
            out = io.BytesIO()
            if has_alpha:
                im = im.convert("RGBA")
                im.save(out, format="PNG", optimize=True)
                mime = "image/png"
            else:
                im = im.convert("RGB")
                im.save(out, format="JPEG", quality=82, optimize=True)
                mime = "image/jpeg"
    except Exception:
        logger.exception("menu pdf: could not decode an image")
        return None
    encoded = base64.b64encode(out.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def _fetch(
    client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore, *, max_dim: int
) -> tuple[str, str | None]:
    async with sem:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data_uri = await asyncio.to_thread(
                _to_thumb_data_uri, resp.content, max_dim=max_dim
            )
            return url, data_uri
        except Exception:
            logger.warning("menu pdf: image fetch failed for %s", url)
            return url, None


async def prepare_menu_assets(document: MenuDocument) -> None:
    """Fetch + inline every image and build the QR, mutating `document` in place.

    A failed image just leaves that item without a thumbnail (a clean text row),
    never failing the whole menu — the same graceful degradation the design wants
    for items that have no photo at all.
    """
    document.qr_svg = _qr_svg(document.whatsapp_url, dark=document.theme.ink)

    wants: dict[str, int] = {}
    for section in document.sections:
        for item in section.items:
            if item.image_url:
                wants[item.image_url] = _THUMB_MAX
    if document.logo_url:
        wants[document.logo_url] = _LOGO_MAX

    if not wants:
        return

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT, follow_redirects=True
    ) as client:
        results = await asyncio.gather(
            *(_fetch(client, url, sem, max_dim=mx) for url, mx in wants.items())
        )
    by_url = dict(results)

    for section in document.sections:
        for item in section.items:
            if item.image_url:
                item.image_data_uri = by_url.get(item.image_url)
    if document.logo_url:
        document.logo_data_uri = by_url.get(document.logo_url)


def _qr_svg(url: str | None, *, dark: str) -> str | None:
    if not url:
        return None
    try:
        import segno

        qr = segno.make(url, error="m")
        buff = io.BytesIO()
        qr.save(
            buff,
            kind="svg",
            scale=1,
            border=0,
            dark=dark,
            xmldecl=False,
            svgns=True,
            omitsize=True,
        )
        return buff.getvalue().decode("utf-8")
    except Exception:
        logger.exception("menu pdf: QR generation failed")
        return None


# ── Rendering (sync CPU; run under asyncio.to_thread) ──────────────────────────


@lru_cache(maxsize=1)
def _jinja_env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    from .icons import icon_svg

    env.globals["font_uri"] = lambda name: (_FONTS_DIR / name).as_uri()
    env.globals["icon"] = lambda key: icon_svg(key)
    return env


def render_menu_pdf(document: MenuDocument) -> bytes:
    """Jinja2 → HTML → WeasyPrint → PDF bytes. Pure CPU; call in a thread."""
    from weasyprint import HTML

    html = _jinja_env().get_template("menu.html.j2").render(doc=document)
    return HTML(string=html, base_url=str(_HERE)).write_pdf()
