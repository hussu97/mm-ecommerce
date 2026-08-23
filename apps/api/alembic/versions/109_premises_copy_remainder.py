"""The last four places the home-kitchen claim was still being made.

`107` swept `cms_pages` and I called the job done. It was not: the same claim
also sits in two tables that seed content from entirely different migrations,
and I had not looked at either.

    ui_translations   about.story_p1        en + ar   (seeded by `007`/`009`)
    blog_posts        eggless-baking-…      en + ar   (seeded by `054`)

The blog one is the worst of the set. It reads "One thing we can't change: it's
a home kitchen that also handles eggs" — a sentence written specifically to be
believed, in a post about allergies, next to a real allergen warning. The
warning stays exactly as it was; only the premises changes.

Found by walking every public endpoint the site actually reads — `/cms/public`,
`/i18n/translations`, `/blog/public` — rather than by grepping the repo. The
seed migrations are history and several of them have been superseded, so what
they contain says nothing about what is live.

Same guard as `107`: whole-string matching, so once anybody rewords any of these
in the admin this migration recognises nothing and does nothing.

Revision ID: 109_premises_remainder
Revises: 108_noon_send_90m
Create Date: 2026-08-18
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "109_premises_remainder"
down_revision: Union[str, None] = "108_noon_send_90m"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: `ui_translations.value` is a flat text column, so these are matched and
#: replaced whole rather than walked.
UI_TRANSLATIONS: dict[str, str] = {
    (
        "What started as a passion project in a home kitchen has grown into "
        "Melting Moments Cakes — a brand built on the belief that every dessert "
        "should be a moment of joy."
    ): (
        "What started as a passion project has grown into Melting Moments Cakes "
        "— a brand built on the belief that every dessert should be a moment of "
        "joy."
    ),
    (
        "ما بدأ كمشروع شغف في مطبخ منزلي أصبح Melting Moments Cakes — علامة "
        "تجارية مبنية على أن كل حلوى يجب أن تكون لحظة فرح."
    ): (
        "ما بدأ كمشروع شغف أصبح Melting Moments Cakes — علامة تجارية مبنية على "
        "أن كل حلوى يجب أن تكون لحظة فرح."
    ),
}

#: `blog_posts.content` is JSONB keyed by locale, same as `cms_pages`, so these
#: are found by walking the document.
BLOG_SLUG = "eggless-baking-what-changes"
BLOG_REPLACEMENTS: dict[str, str] = {
    (
        "One thing we can't change: it's a home kitchen that also handles eggs, "
        "so an eggless recipe isn't an allergen-free environment."
    ): (
        "One thing we can't change: it's one kitchen that also handles eggs, so "
        "an eggless recipe isn't an allergen-free environment."
    ),
    (
        "وأمر واحد لا نستطيع تغييره: هذا مطبخ منزلي يتعامل أيضاً مع البيض، "
        "فالوصفة الخالية منه ليست بيئة خالية من مسبّبات الحساسية."
    ): (
        "وأمر واحد لا نستطيع تغييره: هذا مطبخ واحد يتعامل أيضاً مع البيض، "
        "فالوصفة الخالية منه ليست بيئة خالية من مسبّبات الحساسية."
    ),
}


def rewrite(node: Any, mapping: dict[str, str]) -> tuple[Any, int]:
    """
    Every string in `node` the mapping knows about, replaced. See `107`.

    Substring rather than whole-value here, unlike `107`: a blog body is one
    long string with the sentence buried in the middle of it, so an exact match
    on the whole field would never fire. The keys are full sentences, which is
    specific enough that a partial match cannot land anywhere unintended.
    """
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
    if isinstance(node, str):
        text = node
        count = 0
        for old, new in mapping.items():
            if old in text:
                text = text.replace(old, new)
                count += 1
        return text, count
    return node, 0


def _apply(ui: dict[str, str], blog: dict[str, str]) -> None:
    conn = op.get_bind()

    for old, new in ui.items():
        conn.execute(
            sa.text("UPDATE ui_translations SET value = :new WHERE value = :old"),
            {"new": new, "old": old},
        )

    row = conn.execute(
        sa.text("SELECT content FROM blog_posts WHERE slug = :s"), {"s": BLOG_SLUG}
    ).fetchone()
    if not row or not row[0]:
        return
    content = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    updated, changed = rewrite(content, blog)
    if not changed:
        return
    conn.execute(
        sa.text(
            "UPDATE blog_posts SET content = CAST(:c AS jsonb), updated_at = now() "
            "WHERE slug = :s"
        ),
        {"c": json.dumps(updated, ensure_ascii=False), "s": BLOG_SLUG},
    )


def upgrade() -> None:
    _apply(UI_TRANSLATIONS, BLOG_REPLACEMENTS)


def downgrade() -> None:
    _apply(
        {new: old for old, new in UI_TRANSLATIONS.items()},
        {new: old for old, new in BLOG_REPLACEMENTS.items()},
    )
