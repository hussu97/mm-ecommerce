"""One free-delivery rule: over AED 150, in the fixed-fee zones only.

There is exactly one threshold after this migration and it is 150. The old 200
does not survive in any form — not as a second tier, not as a wider-but-dearer
alternative, not anywhere. Every `200` appearing in this file is a string being
searched for and deleted; none of them is copy this migration writes.

**What changes.** Free delivery used to be "over AED 200, everywhere". That was
written when every zone had a fee we had chosen, and waiving a 15 or a 25 at
that basket size is an ordinary margin decision. Since 057 the fee outside the
three city zones is the courier's own quote for the customer's pin — 62 to Al
Dhaid, 103 to Khor Fakkan, 137 to Abu Dhabi. That bill does not shrink because
the order is large, and there is no fee of ours to waive against it: a free
delivery there is us paying 137 dirhams out of a 150 dirham order.

So the rule becomes: **over AED 150, in Sharjah City, Ajman City and Dubai City
— and nowhere else.** Everywhere else pays what the run costs, whatever the
basket is worth. The code half of that lives in `delivery_service.price`, which
only sets `free_available` for a zone priced `static`.

**Why the copy is rewritten here.** A shopper in Abu Dhabi who reads "free
delivery over 150", fills a basket to 150 and is then charged 137 has been
misled by us, however correct the arithmetic was. So every customer-facing
claim gains the qualifier. These strings live in `cms_pages` and nothing else
edits them, so a migration is the only place this can happen.

The replacements are exact-string, applied to the whole JSON document per page:
the phrases sit in nested content blocks (`seo.description`, FAQ answers) and
matching the text is far less brittle than walking a shape that gets redesigned.
Every searched-for string is verified present in production at the time of
writing; one that has since been hand-edited simply will not match, which is the
safe direction to fail in.

Revision ID: 058_free_delivery_scope
Revises: 057_dynamic_delivery_pricing
Create Date: 2026-08-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Kept under 32 characters: `alembic_version.version_num` is varchar(32), and a
# longer id fails the whole upgrade on the very last statement it runs.
revision: str = "058_free_delivery_scope"
down_revision: Union[str, None] = "057_dynamic_delivery_pricing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The one threshold. Nothing else in the system has an opinion about this
#: number, and nothing is free above any other figure.
THRESHOLD = "150.00"

#: What it was before this migration. Referenced only by `downgrade`.
SUPERSEDED_THRESHOLD = "200.00"

#: (slug, claim to delete, claim to write). Left column is old live copy; every
#: one of them is going away.
COPY: list[tuple[str, str, str]] = [
    (
        "home",
        "Free delivery over AED 200.",
        "Free delivery over AED 150 in selected areas.",
    ),
    (
        "home",
        "توصيل مجاني للطلبات فوق 200 درهم.",
        "توصيل مجاني للطلبات فوق 150 درهم في مناطق مختارة.",
    ),
    (
        "about",
        "Free over AED 200.",
        "Free over AED 150 in selected areas.",
    ),
    (
        "about",
        "مجاناً فوق 200 درهم.",
        "مجاناً فوق 150 درهم في مناطق مختارة.",
    ),
    # The FAQ names the areas in full rather than saying "selected": someone
    # reading it has come looking for exactly which ones.
    (
        "faq",
        "it's free on orders over AED 200",
        "it's free on orders over AED 150 in the Dubai, Sharjah and Ajman city areas",
    ),
    (
        "faq",
        "Orders over AED 200 deliver free anywhere we go",
        "Orders over AED 150 deliver free in the Dubai, Sharjah and Ajman city areas",
    ),
    (
        "faq",
        "وهي مجانية للطلبات فوق 200 درهم",
        "وهي مجانية للطلبات فوق 150 درهم في مناطق دبي والشارقة وعجمان",
    ),
    (
        "faq",
        "الطلبات فوق 200 درهم تُوصَّل مجاناً إلى أي مكان نصل إليه",
        "الطلبات فوق 150 درهم تُوصَّل مجاناً في مناطق دبي والشارقة وعجمان",
    ),
]


def _rewrite(pairs: list[tuple[str, str, str]]) -> None:
    conn = op.get_bind()
    for slug, old, new in pairs:
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = REPLACE(content::text, :old, :new)::jsonb "
                "WHERE slug = :slug AND content::text LIKE '%' || :old || '%'"
            ),
            {"slug": slug, "old": old, "new": new},
        )


def _set_threshold(value: str) -> None:
    op.execute(
        sa.text(f"UPDATE delivery_settings SET free_delivery_threshold = {value}")
    )
    op.alter_column(
        "delivery_settings", "free_delivery_threshold", server_default=value
    )


def upgrade() -> None:
    _set_threshold(THRESHOLD)
    _rewrite(COPY)


def downgrade() -> None:
    _set_threshold(SUPERSEDED_THRESHOLD)
    _rewrite([(slug, new, old) for slug, old, new in COPY])
