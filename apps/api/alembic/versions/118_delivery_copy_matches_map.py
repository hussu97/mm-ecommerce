"""The published delivery copy says what the checkout charges.

`058_free_delivery_scope` wrote "free over AED 150 in the Dubai, Sharjah and
Ajman city areas" into `cms_pages`, and it was true the day it landed. Then
`085_cost_banded_map` replaced the one national threshold with a per-zone one —
75 across the city zones, 100 in Ras al-Khaimah city, 200 in the outer bands,
and nothing at all in Sharjah Central, which is free at any basket — and did not
touch a single line of copy. Every customer-facing surface has been quoting a
number that no zone has used since.

The direction of the error is the bad one. A shopper in Dubai reads "free over
150", stops filling the basket at 150 because that is what we told them the
number was, and was already past the real threshold at 75 — we asked them for
twice the basket the offer needs. In Sharjah city they were told to reach 150
for something they get at any size.

**What the copy now says**, taken from `085`'s zone table and from
`delivery_service.price`, which qualifies on `subtotal >= threshold`:

    Sharjah Central                       free, any basket
    Sharjah Outer                         20, free from 75
    Ajman City                            10, free from 75
    Dubai Near / Mid / Far                20, free from 75
    Umm al-Quwain City                    30, free from 75
    Ras al-Khaimah City                   50, free from 100
    everywhere else                       80, free from 200

"From", not "over", for the same reason: 75 exactly is free, and "over 75" says
it is not.

The short surfaces — the home strip, the about line — cannot carry seven bands,
so they name the two facts a shopper acts on (Sharjah is free, elsewhere starts
at 75) and leave the rest to the checkout, which quotes the real figure from the
pin. The FAQ names them in full, because someone reading the FAQ has come
looking for exactly that.

**Guarded, per convention 7.** Each replacement matches the whole phrase `058`
wrote. Every one was verified verbatim on production while writing this, so
nobody has hand-edited them; if somebody does before this deploys, the `LIKE`
matches nothing and the migration leaves their words alone. Same on a database
restored from an older dump, and on a second run of this migration.

Revision ID: 118_delivery_copy_matches_map
Revises: 117_cart_must_name_its_shopper
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# Under 32 characters — `alembic_version.version_num` is varchar(32), and a
# longer id runs every statement and then fails on the very last one.
revision: str = "118_delivery_copy_matches_map"
down_revision: Union[str, None] = "117_cart_must_name_its_shopper"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (slug, phrase to replace, phrase to write). Left column is what `058` wrote
#: and what production still serves.
COPY: list[tuple[str, str, str]] = [
    (
        "home",
        "Free delivery over AED 150 in selected areas.",
        "Free delivery in Sharjah city, and from AED 75 in selected areas.",
    ),
    (
        "home",
        "توصيل مجاني للطلبات فوق 150 درهم في مناطق مختارة.",
        "توصيل مجاني داخل مدينة الشارقة، ومن 75 درهماً في مناطق مختارة.",
    ),
    (
        "about",
        "Free over AED 150 in selected areas.",
        "Free in Sharjah city, and from AED 75 in selected areas.",
    ),
    (
        "about",
        "مجاناً فوق 150 درهم في مناطق مختارة.",
        "مجاناً داخل مدينة الشارقة، ومن 75 درهماً في مناطق مختارة.",
    ),
    # "Where do you deliver?" — the fee is one clause of a longer answer, so this
    # replacement has to read on into "Pickup from Sharjah is free."
    (
        "faq",
        "it's free on orders over AED 150 in the Dubai, Sharjah and Ajman city areas",
        "it's free in Sharjah city at any basket size, and from AED 75 in the "
        "Dubai, Ajman and Umm Al Quwain city areas",
    ),
    (
        "faq",
        "وهي مجانية للطلبات فوق 150 درهم في مناطق دبي والشارقة وعجمان",
        "وهي مجانية داخل مدينة الشارقة مهما كان حجم الطلب، ومن 75 درهماً في "
        "مناطق دبي وعجمان وأم القيوين",
    ),
    # "How much is delivery?" — the one answer that names every band, and the
    # one a shopper is sent to when they ask what their own address costs. It
    # runs on into ", and collecting from Sharjah costs nothing", so it ends on
    # a clause and not a full stop.
    (
        "faq",
        "Orders over AED 150 deliver free in the Dubai, Sharjah and Ajman city areas",
        "Sharjah city is free at any basket size; the Dubai, Ajman and Umm Al "
        "Quwain city areas are free from AED 75; Ras Al Khaimah city from AED "
        "100; everywhere else from AED 200",
    ),
    (
        "faq",
        "الطلبات فوق 150 درهم تُوصَّل مجاناً في مناطق دبي والشارقة وعجمان",
        "التوصيل مجاني داخل مدينة الشارقة مهما كان حجم الطلب؛ ومن 75 درهماً في "
        "مناطق دبي وعجمان وأم القيوين؛ ومن 100 درهم في مدينة رأس الخيمة؛ ومن "
        "200 درهم في بقية الإمارات",
    ),
]


def _rewrite(pairs: list[tuple[str, str, str]]) -> None:
    """Whole-phrase swap on the page's JSON document.

    The phrases sit in nested content blocks (`seo.description`, FAQ answers),
    and matching the text is far less brittle than walking a shape that gets
    redesigned. The `LIKE` is the guard: no match, no write.
    """
    conn = op.get_bind()
    for slug, old, new in pairs:
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = REPLACE(content::text, :old, :new)::jsonb "
                "WHERE slug = :slug AND content::text LIKE '%' || :old || '%'"
            ),
            {"slug": slug, "old": old, "new": new},
        )


def upgrade() -> None:
    _rewrite(COPY)


def downgrade() -> None:
    _rewrite([(slug, new, old) for slug, old, new in COPY])
