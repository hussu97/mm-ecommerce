"""Shorten the about page's SEO title so the rendered <title> fits in 70 chars.

`app/[locale]/layout.tsx` sets `template: "%s | Melting Moments Cakes"`, which
adds 24 characters to every page title on the site. The about page's stored
title is 56, so what actually reached Bing was 80 —

    About Fatema — the baker behind Melting Moments, Sharjah | Melting Moments Cakes

— and it was reported as too long. It is also saying "Melting Moments" twice,
which is the same 24 characters being paid for a second time. Dropping the
duplicate is most of the fix on its own: the new title is 35, rendering at 59,
and still carries the two things anyone searches for, the baker's name and the
emirate.

Arabic is 45, rendering at 69, and is left alone.

Guarded on the exact string 054 wrote, so this matches nothing — and changes
nothing — once a human has edited the title in the console, including on a
database restored from a dump taken before that edit.

Revision ID: 130_about_title_70
Revises: 129_retire_terms_strings
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "130_about_title_70"
down_revision: Union[str, None] = "129_retire_terms_strings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_TITLE = "About Fatema — the baker behind Melting Moments, Sharjah"
NEW_TITLE = "About Fatema — the baker in Sharjah"


def _swap(old: str, new: str) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE cms_pages "
            "SET content = jsonb_set(content, '{en,seo,title}', to_jsonb(CAST(:new AS text))), "
            "    updated_at = now() "
            "WHERE slug = 'about' "
            "AND content #>> '{en,seo,title}' = :old"
        ),
        {"old": old, "new": new},
    )


def upgrade() -> None:
    _swap(OLD_TITLE, NEW_TITLE)


def downgrade() -> None:
    _swap(NEW_TITLE, OLD_TITLE)
