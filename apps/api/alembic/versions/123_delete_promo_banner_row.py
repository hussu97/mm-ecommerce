"""Delete `promo_banner.text` again, now that nothing puts it back.

`122` already ran this exact `DELETE`, and it worked — for about four seconds.
The deploy order is migrate, restart, seed, and at that point
`scripts.seed_i18n` still listed the key, so the lifespan hook re-inserted it on
boot. The row that exists today was written by that seed, not by `007` and not
by anybody in the console.

The seed entry is gone as of the commit before this one, so nothing restores it
any more. But `122` is already stamped, alembic will not run it a second time,
and `seed()` only ever adds and updates — it has no opinion about a key it no
longer knows. So the row simply sits there, and needs deleting once more, after
the thing that was recreating it stopped.

Which is the whole lesson of this pair, worth stating plainly for the next key
somebody retires: **remove it from `ALL_TRANSLATIONS` first, then delete the
row.** Both in one commit is fine — they deploy together and the seed runs after
the migration. What does not work is deleting the row while the seed still
mentions it, because that is not a race, it is a guaranteed loss.

Revision ID: 123_delete_promo_banner_row
Revises: 122_retire_promo_banner
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "123_delete_promo_banner_row"
down_revision: Union[str, None] = "122_retire_promo_banner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FLAT_KEY = "promo_banner.text"

#: What the seed last wrote, so `downgrade` restores what was actually there.
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
