"""Keep every branch's live menu PDF in the public bucket, re-published on change.

A printed QR on a shop window points at a fixed object —
``gs://<GCS_IMAGE_BUCKET>/menus/<branch>-menu-<lang>.pdf`` — so that link has to
keep answering with the menu the register is selling *now*, not the one somebody
remembered to upload. This loop is that guarantee.

It builds exactly the document the admin's ``GET /menu-groups/{id}/pdf`` builds —
``build_menu_document`` → ``prepare_menu_assets`` → ``render_menu_pdf``, nothing of
its own — for every active branch root, in English and Arabic, and uploads one
only when what would print has changed.

"Changed" is a fingerprint of the document *before* its assets are inlined:
every section and item, name, description, price, option, image URL, the brand
and the branch's WhatsApp number — so adding, removing or re-ordering a product
in the menu group, a price edit or a renamed item all count — plus the templates
it renders through, so a layout deploy re-publishes too. The fingerprint lives on
the object itself (custom metadata): no table to keep, and an object deleted by
hand is simply re-published on the next tick. An unchanged menu costs a few
queries and one metadata read per object; the WeasyPrint render runs only when
something moved.

Started only in production (``app_setup``): a maintainer's machine has ADC write
access to the same bucket, and a dev database must never overwrite the shop's
live menu.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat, object_storage
from app.core.config import settings
from app.core.database import SchedulerSessionFactory
from app.models.branch import Branch
from app.models.menu import MenuGroup

from .builder import build_menu_document, menu_pdf_filename
from .render import _TEMPLATES_DIR, prepare_menu_assets, render_menu_pdf
from .view_models import MenuDocument

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4818
#: How soon a menu edit reaches the printed QR.
_TICK_SECONDS = 300
#: A pass renders at most a few PDFs; this only stops a wedged image host or
#: bucket from pinning the leader connection forever.
_TICK_BUDGET_SECONDS = 600

LANGS = ("en", "ar")
_PREFIX = "menus/"
_FINGERPRINT_KEY = "menu-fingerprint"
#: Short, so a phone that opened yesterday's menu sees today's within minutes.
_CACHE_CONTROL = "public, max-age=300"


def object_key(branch_name: str, lang: str) -> str:
    """Where a branch's menu lives — the address a printed QR points at, so it
    must never change shape."""
    return f"{_PREFIX}{menu_pdf_filename(branch_name, lang)}"


@lru_cache(maxsize=1)
def _layout_digest() -> str:
    """The templates the PDF renders through, so a layout change re-publishes."""
    digest = hashlib.sha256()
    for path in sorted(_TEMPLATES_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(_TEMPLATES_DIR).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def fingerprint(document: MenuDocument) -> str:
    """What would print, as one hash. Call before `prepare_menu_assets`, which
    fills the inlined images and QR the comparison must not depend on."""
    payload = json.dumps(
        dataclasses.asdict(document), sort_keys=True, default=str, ensure_ascii=False
    )
    return hashlib.sha256((_layout_digest() + payload).encode()).hexdigest()


async def _branch_roots(db: AsyncSession) -> list[tuple[MenuGroup, Branch]]:
    """Every active branch's counter menu root, with its branch."""
    rows = await db.execute(
        select(MenuGroup, Branch)
        .join(Branch, Branch.id == MenuGroup.branch_id)
        .where(
            MenuGroup.parent_id.is_(None),
            MenuGroup.root_kind == "branch",
            MenuGroup.deleted_at.is_(None),
            MenuGroup.is_active.is_(True),
            Branch.is_active.is_(True),
            Branch.deleted_at.is_(None),
        )
        .order_by(Branch.display_order, Branch.name)
    )
    return [(root, branch) for root, branch in rows.tuples()]


async def publish_once() -> list[str]:
    """One pass: build every branch menu, upload those that changed.

    Returns the object keys written. A menu that fails to render or upload is
    logged and left as it was — the others still publish, and it is retried on
    the next tick because its stored fingerprint never moved.
    """
    async with SchedulerSessionFactory() as db:
        documents = [
            (
                object_key(branch.name, lang),
                await build_menu_document(db, root, lang=lang),
            )
            for root, branch in await _branch_roots(db)
            for lang in LANGS
        ]
    # The session is closed before anything leaves the building: the image
    # fetches and the bucket are third parties (test_scheduler_session_guard).

    bucket = settings.GCS_IMAGE_BUCKET
    written: list[str] = []
    for key, document in documents:
        try:
            digest = fingerprint(document)
            current = await asyncio.to_thread(
                object_storage.object_metadata, bucket=bucket, key=key
            )
            if current is not None and current.get(_FINGERPRINT_KEY) == digest:
                continue
            await prepare_menu_assets(document)
            pdf = await asyncio.to_thread(render_menu_pdf, document)
            await asyncio.to_thread(
                object_storage.upload_object,
                bucket=bucket,
                key=key,
                body=pdf,
                content_type="application/pdf",
                cache_control=_CACHE_CONTROL,
                metadata={_FINGERPRINT_KEY: digest},
            )
            written.append(key)
        except Exception:  # noqa: BLE001 — one bad menu must not hold up the rest
            logger.exception("Menu PDF publish failed for %s", key)
    return written


async def run_forever() -> None:
    """Re-publish changed branch menus, on a leader-elected loop.

    Same shape as the other lifespan loops — no cron in this stack, an advisory
    lock so a second copy across blue/green does nothing. `held`, not
    `held_session`: the pass fetches images and writes to the bucket, and must
    not keep a session open across either.
    """
    logger.info("Menu PDF publisher started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            # Sleeps first: boot is busy and a menu is not urgent.
            await asyncio.sleep(_TICK_SECONDS)
            await heartbeat.beat("menu_pdf_publish")
            async with advisory_lock.held(
                _ADVISORY_LOCK_KEY, name="menu pdf publisher"
            ) as got:
                if not got:
                    continue
                async with asyncio.timeout(_TICK_BUDGET_SECONDS):
                    written = await publish_once()
            if written:
                logger.info("Menu PDF publisher re-published %s", written)
        except asyncio.CancelledError:
            logger.info("Menu PDF publisher stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Menu PDF publisher tick failed")
