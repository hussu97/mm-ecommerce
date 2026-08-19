"""Publish the collection address instead of holding it back until checkout.

The FAQ told a customer they were welcome to walk in and then declined to say
where — "we send you the address once the order is placed", written when there
was nothing to walk into. `107` fixed the first half of that sentence and left
the second, which is a worse state than either: an invitation with no address.

Two answers change, in both locales:

    "Where are you based?"     now names the Sharjah counter outright
    "Can I pick up my order?"  no longer promises the address on WhatsApp as
                               though it were a secret

**Only the Sharjah address is written into the copy.** The other collection
point is a counter inside a partner café in Barsha Heights, and naming somebody
else's business in editorial copy is not a decision a copy migration should
make. It is already on the checkout's collection picker, and the About page
lists it from the branch record — see below.

**The About page gets a heading, not an address.** `find_us` is only a label, a
title and a sentence; the addresses there are fetched from `branches` by the
page itself. An address in CMS copy is a second answer to a question the branch
row already settles, and the day the shop moves one of the two is wrong with
nothing to say so. This repo has been bitten by a duplicated fact twice; the
FAQ line above is a deliberate, documented exception because a prose answer has
nowhere to interpolate from.

Revision ID: 110_publish_address
Revises: 109_premises_remainder
Create Date: 2026-08-18
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "110_publish_address"
down_revision: Union[str, None] = "109_premises_remainder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: As held on the `K001` branch row on the day this was written. If the shop
#: moves, this string and `branches.address` disagree and this one is the stale
#: copy — which is exactly why it appears here once and nowhere else.
SHARJAH = "Garden Tower 1, Shop 2, Al Majaz 3, Sharjah"
SHARJAH_AR = "جاردن تاور 1، محل رقم 2، المجاز 3، الشارقة"

FAQ_REPLACEMENTS: dict[str, str] = {
    # ── "Where are you based?" ─────────────────────────────────────────────
    (
        "Sharjah. You're welcome to walk in and collect — it's takeaway only, "
        "there's no dine-in. Pickup is free, and we send you the address once "
        "the order is placed."
    ): (
        f"{SHARJAH}. You're welcome to walk in and collect — it's takeaway "
        "only, there's no dine-in. Pickup is free, and there's a second "
        "collection point in Barsha Heights, Dubai, which you can choose at "
        "checkout."
    ),
    (
        "الشارقة. يمكنك الحضور لاستلام طلبك — تيك أواي فقط، فلا توجد صالة "
        "للجلوس. والاستلام مجاني، ونرسل لك العنوان بعد تقديم الطلب."
    ): (
        f"{SHARJAH_AR}. يمكنك الحضور لاستلام طلبك — تيك أواي فقط، فلا توجد "
        "صالة للجلوس. والاستلام مجاني، وهناك نقطة استلام ثانية في برشا هايتس "
        "بدبي يمكنك اختيارها عند إتمام الطلب."
    ),
    # ── "Can I pick up my order?" ──────────────────────────────────────────
    #
    # The old wording made the address sound like something released only to
    # customers who had already paid, which is now the opposite of the policy.
    # The confirmation of the *time* is real and stays.
    (
        "Yes, and it's free. Once you've ordered we'll confirm the time and the "
        "Sharjah address on WhatsApp. Orders placed before noon are usually "
        "ready the same day."
    ): (
        f"Yes, and it's free. Collect from {SHARJAH}, or from Barsha Heights in "
        "Dubai — pick either at checkout. We'll confirm the time on WhatsApp, "
        "and orders placed before noon are usually ready the same day."
    ),
    (
        "نعم، ومجاناً. بعد تقديم الطلب نؤكد لك الموعد وعنوان الاستلام في "
        "الشارقة عبر واتساب. الطلبات قبل الظهر تكون جاهزة في نفس اليوم عادةً."
    ): (
        f"نعم، ومجاناً. الاستلام من {SHARJAH_AR}، أو من برشا هايتس في دبي — "
        "اختر ما يناسبك عند إتمام الطلب. نؤكد لك الموعد عبر واتساب، والطلبات "
        "قبل الظهر تكون جاهزة في نفس اليوم عادةً."
    ),
}

#: The About page's collection block. Copy only — `app/[locale]/about/page.tsx`
#: fetches the branches and prints their addresses itself.
ABOUT_FIND_US = {
    "en": {
        "label": "Come and see us",
        "title": "Where to collect",
        "subtitle": (
            "Collection is free from either counter. Takeaway only — there is "
            "no seating at the kitchen."
        ),
    },
    "ar": {
        "label": "تفضّل بزيارتنا",
        "title": "أين تستلم طلبك",
        "subtitle": (
            "الاستلام مجاني من أي من النقطتين. تيك أواي فقط — لا توجد صالة "
            "للجلوس في المطبخ."
        ),
    },
}


def rewrite(node: Any, mapping: dict[str, str]) -> tuple[Any, int]:
    """Every string the mapping knows about, replaced whole. See `107`."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        count = 0
        for key, value in node.items():
            out[key], hits = rewrite(value, mapping)
            count += hits
        return out, count
    if isinstance(node, list):
        items = []
        count = 0
        for value in node:
            item, hits = rewrite(value, mapping)
            items.append(item)
            count += hits
        return items, count
    if isinstance(node, str) and node in mapping:
        return mapping[node], 1
    return node, 0


def _page(conn, slug: str) -> dict | None:
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = :s"), {"s": slug}
    ).fetchone()
    if not row or not row[0]:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def _save(conn, slug: str, content: dict) -> None:
    conn.execute(
        sa.text(
            "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() "
            "WHERE slug = :s"
        ),
        {"c": json.dumps(content, ensure_ascii=False), "s": slug},
    )


def upgrade() -> None:
    conn = op.get_bind()

    faq = _page(conn, "faq")
    if faq is not None:
        updated, changed = rewrite(faq, FAQ_REPLACEMENTS)
        if changed:
            _save(conn, "faq", updated)

    about = _page(conn, "about")
    if about is not None:
        for locale, block in ABOUT_FIND_US.items():
            section = about.get(locale)
            if isinstance(section, dict) and "find_us" not in section:
                about[locale] = {**section, "find_us": block}
        _save(conn, "about", about)


def downgrade() -> None:
    conn = op.get_bind()

    faq = _page(conn, "faq")
    if faq is not None:
        updated, changed = rewrite(
            faq, {new: old for old, new in FAQ_REPLACEMENTS.items()}
        )
        if changed:
            _save(conn, "faq", updated)

    about = _page(conn, "about")
    if about is not None:
        for locale, block in ABOUT_FIND_US.items():
            section = about.get(locale)
            # Removed only if it still reads exactly as this migration wrote it,
            # so a heading somebody has since edited survives the downgrade.
            if isinstance(section, dict) and section.get("find_us") == block:
                about[locale] = {k: v for k, v in section.items() if k != "find_us"}
        _save(conn, "about", about)
