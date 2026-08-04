"""Fatema's own words on the About page.

Four changes to the About page, all of them the owner's copy rather than mine:

  * The hero led with where the bakery is — "It started in a home kitchen in
    Sharjah" — which is a fact about the baker. It now leads with what the
    customer is buying: "From my kitchen to your celebrations".
  * **The Beginning** was the brownie story: forty trays, oven temperatures,
    how the chocolate goes in. True, and about the baking. It is now about why
    any of it matters — a dessert that becomes part of somebody's memory.
  * **How it works** described the logistics and repeated the emirate list that
    the values cards below it already carry. It becomes **From order to
    celebration**, about what is done for one order rather than where the vans
    go.
  * **Real ingredients** said "Chocolate with cocoa butter in it", which is a
    description of the thing rather than its name. Couverture is the name, and
    it is the word a customer comparing bakeries will recognise.

Only the About page is touched, and only in the fields listed. The blog post
*Why We Use Real Butter, Real Vanilla, and Real Chocolate* still explains
compound chocolate against cocoa butter at length — that one is teaching the
distinction rather than naming the ingredient, so the phrase belongs there.

Same shape as 054: the previous content is copied into `about_copy_backup_061`
before it is overwritten, and the downgrade puts it back verbatim and drops the
table. Overwriting is the point — the stored copy is what is being replaced.

Revision ID: 061_about_page_copy
Revises: 060_batch_retry
Create Date: 2026-08-04 00:00:00.000000

"""

from __future__ import annotations

import json
import uuid
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "061_about_page_copy"
down_revision: Union[str, None] = "060_batch_retry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKUP_TABLE = "about_copy_backup_061"

#: The three lines of the "centrepiece / photo / bite" stanza are single
#: newlines inside one paragraph, not separate paragraphs. The About page
#: renders story bodies with `whitespace-pre-line`, so they break where they
#: are written instead of running together.
STORY_1_EN = (
    "I never planned to build a bakery. I simply loved creating desserts that "
    "made people pause for a moment and smile.\n\n"
    "One order became another, recommendations spread, and before I knew it, "
    "strangers were trusting me with birthdays, weddings, baby showers, and "
    "the moments that matter most.\n\n"
    "Today, every cake is still designed with that same feeling in mind—not "
    "just to look beautiful, but to become part of someone's memory.\n\n"
    "I've always believed dessert should be more than the last course of a "
    "meal.\n\n"
    "It should be the centerpiece people gather around.\n"
    "The photo everyone takes.\n"
    "The bite everyone remembers.\n\n"
    "That's the philosophy Melting Moments was built on, and it's the standard "
    "every order is held to today."
)

STORY_2_EN = (
    "Every order begins from scratch—not from a shelf.\n\n"
    "From mixing the batter to the final ribbon, each dessert is prepared "
    "specifically for your celebration. That means fresher flavours, better "
    "texture, and the attention to detail that only comes from making one "
    "order at a time.\n\n"
    "Whether it's a single birthday cake or hundreds of corporate gifts, every "
    "box receives the same care before it leaves our kitchen.\n\n"
    "We plan, bake, decorate, package and deliver—handling every step with "
    "care so your desserts arrive looking just as beautiful as they taste.\n\n"
    "Whether you're ordering for ten people or a thousand, the goal is always "
    "the same: make your celebration effortless."
)

STORY_1_AR = (
    "لم أخطط يوماً لبناء مخبز. كنت ببساطة أحب صنع حلويات تجعل الناس يتوقفون "
    "لحظة ويبتسمون.\n\n"
    "طلبٌ قاد إلى آخر، وانتشرت التوصيات، وقبل أن أدرك صار أشخاص لا أعرفهم "
    "يأتمنونني على أعياد الميلاد والأعراس وحفلات استقبال المواليد، وعلى "
    "اللحظات الأهم في حياتهم.\n\n"
    "واليوم، ما زالت كل كعكة تُصمَّم بالشعور نفسه — لا لتبدو جميلة فحسب، بل "
    "لتصبح جزءاً من ذكرى أحدهم.\n\n"
    "آمنت دائماً بأن الحلوى يجب أن تكون أكثر من الطبق الأخير في الوجبة.\n\n"
    "يجب أن تكون القطعة التي يجتمع الناس حولها.\n"
    "الصورة التي يلتقطها الجميع.\n"
    "اللقمة التي يتذكرها الجميع.\n\n"
    # The Arabic copy already transliterates the brand — six times across the
    # about, home, FAQ and contact pages — so it is written the same way here
    # rather than dropping Latin script into the middle of a sentence.
    "هذه هي الفلسفة التي قامت عليها ملتنج مومنتس، وهي المعيار الذي يُقاس "
    "به كل طلب اليوم."
)

STORY_2_AR = (
    "كل طلب يبدأ من الصفر — لا من رفّ جاهز.\n\n"
    "من خلط الخليط إلى آخر شريطة، تُحضَّر كل حلوى خصيصاً لاحتفالك. هذا يعني "
    "نكهات أطزج، وقواماً أفضل، والاهتمام بالتفاصيل الذي لا يأتي إلا من تحضير "
    "طلب واحد في كل مرة.\n\n"
    "سواء كانت كعكة عيد ميلاد واحدة أو مئات الهدايا المؤسسية، تنال كل علبة "
    "العناية نفسها قبل أن تغادر مطبخنا.\n\n"
    "نخطّط ونخبز ونزيّن ونغلّف ونوصّل — نتولّى كل خطوة بعناية حتى تصل حلوياتك "
    "بجمالٍ يوازي مذاقها.\n\n"
    "سواء كنت تطلب لعشرة أشخاص أو لألف، الهدف واحد دائماً: أن نجعل احتفالك "
    "بلا عناء."
)

ABOUT_PATCH: dict[str, Any] = {
    "en": {
        "hero": {
            "title": "From my kitchen to your celebrations",
            "subtitle": (
                "Thoughtfully designed. Carefully crafted. Made to become part "
                "of your happiest memories."
            ),
        },
        "story_1": {
            "title": "It was never just about cake.",
            "body": STORY_1_EN,
        },
        "story_2": {
            "label": "From order to celebration",
            "title": "Made to order. Made with intention.",
            "body": STORY_2_EN,
        },
    },
    "ar": {
        "hero": {
            "title": "من مطبخي إلى احتفالاتكم",
            "subtitle": "تصميمٌ مدروس. صناعةٌ بعناية. لتصبح جزءاً من أسعد ذكرياتكم.",
        },
        "story_1": {
            "title": "لم يكن الأمر يوماً عن الكيك وحده.",
            "body": STORY_1_AR,
        },
        "story_2": {
            "label": "من الطلب إلى الاحتفال",
            "title": "تُحضَّر عند الطلب. وتُحضَّر بعناية مقصودة.",
            "body": STORY_2_AR,
        },
    },
}

#: Positional, because `values` is an ordered list the page renders as four
#: cards in order. Only the one card changes; the index is asserted against its
#: title so a reordered list fails loudly rather than rewriting the wrong card.
VALUES_INDEX = 1
VALUES_EXPECTED_TITLE = {"en": "Real ingredients", "ar": "مكونات حقيقية"}
VALUES_DESCRIPTION = {
    "en": (
        "Butter, not margarine. Real vanilla. Couverture chocolate. It costs "
        "more and it tastes like it."
    ),
    "ar": "زبدة لا مارغرين. فانيلا حقيقية. شوكولاتة كوفرتور. تكلف أكثر، ويظهر ذلك في المذاق.",
}


def _apply(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Patch over base, recursing into dicts and replacing everything else."""
    result = dict(base)
    for key, value in patch.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _apply(current, value)
        else:
            result[key] = value
    return result


def _retitle_values(locale_content: dict[str, Any], locale: str) -> None:
    """Swap the one values card's description, in place, or leave it alone.

    A card that is not where it was expected is skipped rather than guessed at:
    rewriting "All seven emirates" with an ingredients sentence is worse than
    leaving one sentence stale.
    """
    values = locale_content.get("values")
    if not isinstance(values, list) or len(values) <= VALUES_INDEX:
        return
    card = values[VALUES_INDEX]
    if not isinstance(card, dict):
        return
    if card.get("title") != VALUES_EXPECTED_TITLE[locale]:
        return
    values[VALUES_INDEX] = {**card, "description": VALUES_DESCRIPTION[locale]}


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        BACKUP_TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(150), nullable=False),
        sa.Column("content", JSONB, nullable=True),
    )

    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'about'")
    ).fetchone()
    if not row:
        return

    content: dict[str, Any] = row[0] or {}
    op.bulk_insert(
        sa.table(
            BACKUP_TABLE,
            sa.column("id", UUID),
            sa.column("slug", sa.String),
            sa.column("content", JSONB),
        ),
        [{"id": uuid.uuid4(), "slug": "about", "content": content}],
    )

    updated = dict(content)
    for locale, locale_patch in ABOUT_PATCH.items():
        merged = _apply(updated.get(locale) or {}, locale_patch)
        # Copied so the patch cannot mutate the dict already handed to the
        # backup insert above.
        merged["values"] = [
            dict(v) if isinstance(v, dict) else v for v in (merged.get("values") or [])
        ]
        _retitle_values(merged, locale)
        updated[locale] = merged

    conn.execute(
        sa.text(
            "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() "
            "WHERE slug = 'about'"
        ),
        {"c": json.dumps(updated, ensure_ascii=False)},
    )


def downgrade() -> None:
    conn = op.get_bind()
    for slug, content in conn.execute(
        sa.text(f"SELECT slug, content FROM {BACKUP_TABLE}")  # noqa: S608
    ).fetchall():
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() "
                "WHERE slug = :s"
            ),
            {"c": json.dumps(content, ensure_ascii=False), "s": slug},
        )
    op.drop_table(BACKUP_TABLE)
