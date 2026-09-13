"""Realtime printable-menu PDFs for a menu-group root.

A menu group root (a branch's counter tree, or the integrator/Grubtech tree) is
turned into a print-ready A4 PDF, in English or Arabic, from live catalogue data
— the same names, descriptions, images, options and prices the register and the
marketplaces are serving right now.

Branding follows the money, not a hard-coded logo: a branch (counter) menu is
titled by the legal entity that branch's *counter* channel trades under
(``tax_identity_service`` — Barsha's counter is Najm AlShamal / "Attibassi
Coffee", every other branch is Fatema / "Melting Moments"), so the same builder
prints an Attibassi coffee menu and a Melting Moments dessert menu without
knowing either name. The integrator tree has no branch, so it falls to the
registered default entity.

Layout is Jinja2 → WeasyPrint. Product images and the entity logo are fetched
once, downscaled and inlined as data URIs so the render never reaches the network
mid-layout; the WhatsApp QR is an inline SVG from ``segno``. Fonts are bundled
(``fonts/``) so a slim container renders identically to a maintainer's machine.

Public surface:
    build_menu_document(db, root, *, lang)  -> MenuDocument   (builder.py)
    render_menu_pdf(document)               -> bytes          (render.py)
"""

from __future__ import annotations

from .builder import build_menu_document
from .render import prepare_menu_assets, render_menu_pdf
from .view_models import MenuDocument

__all__ = [
    "MenuDocument",
    "build_menu_document",
    "prepare_menu_assets",
    "render_menu_pdf",
]
