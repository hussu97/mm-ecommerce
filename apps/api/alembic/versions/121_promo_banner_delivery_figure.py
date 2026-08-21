"""The banner across the top of the site stops promising free delivery over 150.

`118` swept `cms_pages` and I called the job done. It was not — which is the
same sentence `109` opens with, about the same mistake, three months earlier.
`109` even names the table: `ui_translations` seeds from a different migration
and nothing in `cms_pages` points at it.

    ui_translations   promo_banner.text   en + ar   (seeded by `007`)

**Superseded by `122`, and wrong about why it mattered.** This file originally
said `promo_banner.text` was "the strip across the top of every page, the
most-read sentence the shop publishes". It is not. Nothing renders it — the top
strip is `promo.new_customer_title`, the home delivery line is
`usp.free_delivery_*`, and this key reaches the browser only inside the
serialized translations blob. I read the key's name instead of looking for its
consumer, which is the same shortcut that made `118` miss this table in the
first place.

It also did not work: this migration deployed green and updated no rows, for a
reason never established. `122` deletes the key instead, matching on the
flattened `namespace || '.' || key` — the only spelling of the row's name that
has been confirmed against production.

Left in the chain because it is already applied. The rest of this file is
accurate about `ui_translations` being a table `cms_pages` sweeps miss, which is
worth keeping.

**Why this one is hard-coded at all.** Every other delivery string in
`ui_translations` is a template — `checkout.delivery_free_note` is "Free delivery
on orders above {amount} AED", filled from the priced quote, so it followed the
zone thresholds on its own and needs nothing from this migration. The banner has
no basket and no pin behind it, so there was no `{amount}` to fill and somebody
typed the number instead. It is replaced with wording that does not need one,
matching what `118` put on the home page: Sharjah city is free at any size, and
75 is where it starts everywhere else that has a threshold at all.

Guarded on the exact string, per convention 7. If somebody edits this in the
console first, theirs wins.

Revision ID: 121_promo_banner_figure
Revises: 120_readable_category_slugs
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "121_promo_banner_figure"
down_revision: Union[str, None] = "120_readable_category_slugs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The row this migration is about.
#:
#: `namespace` and `key` are separate columns and the pair is what
#: `uq_ui_translation` is unique on. `promo_banner.text` — the name the site
#: uses, and the name in this file's title — is something `i18n_service` builds
#: on the way out (`f"{t.namespace}.{t.key}"`); it is not a value in the
#: database and matching on it finds nothing. Worth writing down, because the
#: first draft of this migration matched `key = 'promo_banner.text'` and would
#: have deployed green, changed nothing, and left the banner up.
NAMESPACE = "promo_banner"
KEY = "text"

#: (locale, old value, new value).
#:
#: `ui_translations.value` is a flat text column, so these are matched and
#: replaced whole rather than walked — same as `109`.
#:
#: The old values are what production serves today, which is **not** what `007`
#: seeded ("Free Shipping above 200 AED | Use code FREESHIP") — somebody
#: rewrote this in the console at some point. So the guard is doing real work
#: here rather than being a formality: this row has a history of being edited by
#: hand, and if it happens again before this deploys, theirs wins and this does
#: nothing.
#:
#: The wording mirrors the home page's `seo.description` from `118` rather than
#: inventing a second phrasing for the same fact. Word order follows the
#: banner's own house style ("75 AED", not "AED 75"), which is what the string
#: it replaces used.
TRANSLATIONS: list[tuple[str, str, str]] = [
    (
        "en",
        "Free delivery over 150 AED in selected areas",
        "Free delivery in Sharjah city, and from 75 AED in selected areas",
    ),
    (
        "ar",
        "توصيل مجاني للطلبات فوق 150 درهم في مناطق مختارة",
        "توصيل مجاني داخل مدينة الشارقة، ومن 75 درهماً في مناطق مختارة",
    ),
]


def _rewrite(rows: list[tuple[str, str, str]]) -> None:
    conn = op.get_bind()
    for locale, old, new in rows:
        conn.execute(
            sa.text(
                "UPDATE ui_translations SET value = :new "
                "WHERE locale = :locale AND namespace = :namespace "
                "AND key = :key AND value = :old"
            ),
            {
                "locale": locale,
                "namespace": NAMESPACE,
                "key": KEY,
                "old": old,
                "new": new,
            },
        )


def upgrade() -> None:
    _rewrite(TRANSLATIONS)


def downgrade() -> None:
    _rewrite([(loc, new, old) for loc, old, new in TRANSLATIONS])
