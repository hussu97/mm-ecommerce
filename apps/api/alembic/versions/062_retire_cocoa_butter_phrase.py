"""Say what compound chocolate does without naming the fat it leaves out.

061 replaced "Chocolate with cocoa butter in it" on the About page with
"Couverture chocolate" — the name of the thing rather than a description of it.
One sentence was left behind, in the blog post *Butter, vanilla, chocolate:
where the money goes*:

    Compound chocolate replaces cocoa butter with vegetable fat.

That sentence is teaching the distinction rather than naming an ingredient, so
it was defensible where it stood. It goes now anyway, because the phrase should
not appear anywhere a customer reads. The point survives intact — arguably
better, since "the fat that makes real chocolate melt" explains to somebody who
has never heard of cocoa butter exactly what compound chocolate costs them,
which the technical name never did.

Everything else stays. The post already contrasts couverture against compound
in its opening line and its meta description, so nothing here needs teaching a
new word.

Written as an exact-string swap rather than a rewrite of the post: if the copy
has been edited in the admin since, the old sentence will not match and this
does nothing, which is the right outcome for a migration whose whole job is to
remove one sentence somebody may already have removed.

Revision ID: 062_retire_cocoa_butter_phrase
Revises: 061_about_page_copy
Create Date: 2026-08-04 00:00:00.000000

"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "062_retire_cocoa_butter_phrase"
down_revision: Union[str, None] = "061_about_page_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SLUG = "why-we-use-premium-ingredients"

#: (locale, before, after). Reversible by construction — the downgrade runs the
#: same swaps the other way round, so no backup table is needed for one
#: sentence.
SWAPS: list[tuple[str, str, str]] = [
    (
        "en",
        "Compound chocolate replaces cocoa butter with vegetable fat.",
        "Compound chocolate swaps the fat that makes real chocolate melt for a "
        "vegetable one.",
    ),
    (
        "ar",
        "الشوكولاتة المركّبة تستبدل زبدة الكاكاو بدهون نباتية.",
        "الشوكولاتة المركّبة تستبدل الدهن الذي يجعل الشوكولاتة الحقيقية تذوب "
        "بدهن نباتي.",
    ),
]


def _swap(reverse: bool) -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT content FROM blog_posts WHERE slug = :s"), {"s": SLUG}
    ).fetchone()
    if not row:
        return

    content: dict[str, Any] = row[0] or {}
    changed = False
    for locale, before, after in SWAPS:
        if reverse:
            before, after = after, before
        post = content.get(locale)
        if not isinstance(post, dict):
            continue
        body = post.get("body")
        if not isinstance(body, str) or before not in body:
            continue
        content[locale] = {**post, "body": body.replace(before, after)}
        changed = True

    if not changed:
        return

    conn.execute(
        sa.text(
            "UPDATE blog_posts SET content = CAST(:c AS jsonb), updated_at = now() "
            "WHERE slug = :s"
        ),
        {"c": json.dumps(content, ensure_ascii=False), "s": SLUG},
    )


def upgrade() -> None:
    _swap(reverse=False)


def downgrade() -> None:
    _swap(reverse=True)
