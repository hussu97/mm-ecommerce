"""Retire `promo_banner.text`, which nothing has rendered for a long time.

`121` rewrote this string's delivery figure and I described it in that commit as
"the strip across the top of every page, the most-read sentence the shop
publishes". That was wrong, and it was wrong because I read the key's name
instead of looking for what renders it. Nothing does.

Checked against production rather than the repo: six page types, zero
occurrences in visible markup. The string appears only inside the serialized
translations blob in a `<script>` tag — shipped to every page, rendered on none.
The two banners it sounds like it belongs to are driven by other keys entirely:

    top strip     layout/PromoBanner        promo.new_customer_title, promo.use_code
                                            (filled from the live promo row)
    home strip    home/DeliveryPromiseBanner usp.free_delivery_here / _over / delivering_to
                                            ({area}, {amount} from GET /delivery/area)

So the fix is not a better sentence, it is the absence of one.

**Why there is no value guard**, unlike `118` and `121`. The usual guard exists
so a migration cannot overwrite words a human chose. Here the key is dead by
code inspection, not by what it happens to say: whatever anybody types into it
renders nowhere, so keeping a hand-edited version would preserve the trap rather
than the person's intent. And somebody has already fallen into it — production's
value is not what `007` seeded, so it was edited in the console at some point by
someone who expected it to appear.

`downgrade` puts back exactly what production held, so nothing is lost that
cannot be restored.

**Why `121` appeared to do nothing, and why this migration is only half the
fix.** `app_setup` runs `scripts.seed_i18n` in the API's lifespan hook, so it
executes on every boot and overwrites any row whose value differs from its
constant. The deploy order is migrate, restart, seed — so `121` updated the row
and the seed restored it seconds later, then invalidated the Redis cache so the
restored text was serving immediately. Nothing about that is visible in the
migration's own output, which is why it looked like a `WHERE` clause that
matched nothing.

This migration therefore ships alongside the removal of `promo_banner.text` from
`ALL_TRANSLATIONS`. The seed only ever adds and updates, never deletes, so
retiring a key genuinely needs both halves: the line gone from the seed so it
stops being restored, and this `DELETE` for the row already in the table.

Matched on `namespace || '.' || key` regardless — that is the form the API
returns and the console displays, so it is the spelling verified against
production.

Revision ID: 122_retire_promo_banner
Revises: 121_promo_banner_figure
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "122_retire_promo_banner"
down_revision: Union[str, None] = "121_promo_banner_figure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The key as the site names it. Split across `namespace` and `key` in the
#: table; joined back together here because that is the spelling that has been
#: confirmed to exist.
FLAT_KEY = "promo_banner.text"

#: What production held when this was written, so `downgrade` restores the row
#: as it actually was rather than as `007` first seeded it.
RESTORE: list[tuple[str, str]] = [
    ("en", "Free delivery over 150 AED in selected areas"),
    ("ar", "توصيل مجاني للطلبات فوق 150 درهم في مناطق مختارة"),
]


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM ui_translations WHERE namespace || '.' || key = :flat_key"
        ),
        {"flat_key": FLAT_KEY},
    )


def downgrade() -> None:
    namespace, _, key = FLAT_KEY.partition(".")
    conn = op.get_bind()
    for locale, value in RESTORE:
        conn.execute(
            sa.text(
                "INSERT INTO ui_translations (id, locale, namespace, key, value) "
                "VALUES (gen_random_uuid(), :locale, :namespace, :key, :value) "
                "ON CONFLICT (locale, namespace, key) DO NOTHING"
            ),
            {"locale": locale, "namespace": namespace, "key": key, "value": value},
        )
