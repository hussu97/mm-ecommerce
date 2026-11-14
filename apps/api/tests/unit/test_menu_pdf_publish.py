"""The branch menu publisher: what counts as a change, where it writes, and that
an unchanged menu is never re-rendered.

No DB and no bucket: `build_menu_document`, the storage helpers and the render are
replaced with fakes, so this pins the publisher's own decisions — the builder and
renderer it reuses are covered by `test_menu_pdf.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.services.catalog.menu_pdf import builder as B
from app.services.catalog.menu_pdf import publish as P
from app.services.catalog.menu_pdf.theme import DEFAULT_THEME
from app.services.catalog.menu_pdf.view_models import (
    MenuColumnBlock,
    MenuDocument,
    MenuItem,
    MenuSection,
)


def _doc(*, price="15", name="Latte", lang="en") -> MenuDocument:
    return MenuDocument(
        lang=lang,
        direction="ltr",
        theme=DEFAULT_THEME,
        brand_name="Melting Moments Cafe",
        logo_url=None,
        logo_data_uri=None,
        title="Menu",
        branch_name="Barsha Heights",
        whatsapp_number="+971 50 846 3045",
        whatsapp_url="https://wa.me/971508463045",
        qr_svg=None,
        sections=[
            MenuSection(
                title="Coffee",
                icon_key="coffee",
                blocks=[
                    MenuColumnBlock(
                        columns=None,
                        items=[
                            MenuItem(
                                name=name,
                                description=None,
                                image_url="https://example.test/latte.jpg",
                                image_data_uri=None,
                                single_price=price,
                                price_prefix=None,
                                variants=[],
                                column_prices=None,
                                calories=None,
                            )
                        ],
                    )
                ],
            )
        ],
    )


# ── keys ───────────────────────────────────────────────────────────────────────


def test_object_key_is_the_branch_slug_per_language():
    assert P.object_key("Barsha Heights", "en") == "menus/barsha-heights-menu-en.pdf"
    assert P.object_key("Sharjah", "ar") == "menus/sharjah-menu-ar.pdf"


def test_admin_download_name_is_unchanged_by_the_shared_helper():
    # Lifted out of the router so the publisher names objects the same way.
    assert B.menu_pdf_filename("Melting Moments Cafe", "en") == (
        "melting-moments-cafe-menu-en.pdf"
    )
    assert B.menu_pdf_filename(None, "ar") == "menu-menu-ar.pdf"


# ── fingerprint ────────────────────────────────────────────────────────────────


def test_fingerprint_is_stable_for_the_same_menu():
    assert P.fingerprint(_doc()) == P.fingerprint(_doc())


@pytest.mark.parametrize(
    "changed", [_doc(price="16"), _doc(name="Spanish Latte"), _doc(lang="ar")]
)
def test_fingerprint_moves_when_what_prints_moves(changed):
    assert P.fingerprint(changed) != P.fingerprint(_doc())


# ── publish_once ───────────────────────────────────────────────────────────────


class _Bucket:
    def __init__(self, stored: dict[str, dict[str, str]] | None = None):
        self.stored = stored or {}
        self.uploads: list[dict] = []

    def object_metadata(self, *, bucket, key):
        return self.stored.get(key)

    def upload_object(self, **kw):
        self.uploads.append(kw)
        self.stored[kw["key"]] = kw["metadata"]


@pytest.fixture
def wired(monkeypatch):
    """Two branches, a fake bucket, and a render that just counts."""
    branches = [
        (SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(name="Barsha Heights")),
        (SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(name="Sharjah")),
    ]
    menus = {"price": "15"}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def roots(db):
        return branches

    async def build(db, root, *, lang):
        return _doc(price=menus["price"], lang=lang)

    async def prepare(document):
        document.qr_svg = "<svg/>"

    renders: list[str] = []

    def render(document):
        renders.append(document.lang)
        return b"%PDF-fake"

    bucket = _Bucket()
    monkeypatch.setattr(P, "SchedulerSessionFactory", _Session)
    monkeypatch.setattr(P, "_branch_roots", roots)
    monkeypatch.setattr(P, "build_menu_document", build)
    monkeypatch.setattr(P, "prepare_menu_assets", prepare)
    monkeypatch.setattr(P, "render_menu_pdf", render)
    monkeypatch.setattr(P.object_storage, "object_metadata", bucket.object_metadata)
    monkeypatch.setattr(P.object_storage, "upload_object", bucket.upload_object)
    return SimpleNamespace(bucket=bucket, renders=renders, menus=menus)


def test_first_pass_publishes_every_branch_in_both_languages(wired):
    written = asyncio.run(P.publish_once())
    assert written == [
        "menus/barsha-heights-menu-en.pdf",
        "menus/barsha-heights-menu-ar.pdf",
        "menus/sharjah-menu-en.pdf",
        "menus/sharjah-menu-ar.pdf",
    ]
    upload = wired.bucket.uploads[0]
    assert upload["content_type"] == "application/pdf"
    assert upload["cache_control"] == "public, max-age=300"
    # Taken before prepare_menu_assets inlines the QR and images, so a
    # re-fetched image can never read as a menu change.
    assert upload["metadata"]["menu-fingerprint"] == P.fingerprint(_doc(lang="en"))


def test_an_unchanged_menu_is_not_rendered_again(wired):
    asyncio.run(P.publish_once())
    wired.renders.clear()
    assert asyncio.run(P.publish_once()) == []
    assert wired.renders == []


def test_a_menu_change_republishes(wired):
    asyncio.run(P.publish_once())
    wired.menus["price"] = "17"
    assert len(asyncio.run(P.publish_once())) == 4


def test_one_failed_upload_does_not_stop_the_rest(wired, monkeypatch):
    real = wired.bucket.upload_object

    def flaky(**kw):
        if kw["key"].startswith("menus/barsha"):
            raise RuntimeError("bucket hiccup")
        real(**kw)

    monkeypatch.setattr(P.object_storage, "upload_object", flaky)
    written = asyncio.run(P.publish_once())
    assert written == ["menus/sharjah-menu-en.pdf", "menus/sharjah-menu-ar.pdf"]


# ── WhatsApp number ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stored", ["508463045", "0508463045", "+971 50 846 3045", "00971508463045"]
)
def test_whatsapp_link_carries_the_country_code_however_the_phone_was_typed(stored):
    number, url = B._whatsapp(SimpleNamespace(phone=stored))
    assert url == "https://wa.me/971508463045"
    assert number == "+971 50 846 3045"


def test_an_unreadable_phone_prints_as_typed_with_no_qr():
    assert B._whatsapp(SimpleNamespace(phone="ask at counter")) == (
        "ask at counter",
        None,
    )
    assert B._whatsapp(SimpleNamespace(phone=None)) == (None, None)
