"""Drop the two UI strings that only existed to build a link to a missing page.

The signup page read `By signing up you agree to our [Terms] and [Privacy
Policy]`, and `/terms` was never a route. The proxy answered it with a 307 to
`/en/terms` and Next answered that with a 404, which is what Bing's site scan
reported as a broken redirect on both URLs.

There is no terms page and none is wanted, so the link is gone and the sentence
now names the privacy policy alone. `auth.tos_terms` and `auth.tos_and` have no
remaining reader.

Deleting them takes two edits, not one. `scripts/seed_i18n.py` is the source of
truth and runs on every boot, but it only inserts and updates — a key removed
from it is simply never touched again, so the row it already wrote keeps being
served to anything that reads the bundle. Removing the line stops it coming
back; this migration removes what is there.

Guarded on the exact retired values, so a row a human has since edited in the
console is left alone rather than deleted out from under them.

Revision ID: 129_retire_terms_strings
Revises: 128_slider_zones
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "129_retire_terms_strings"
down_revision: Union[str, None] = "128_slider_zones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RETIRED = [
    ("en", "tos_terms", "Terms"),
    ("en", "tos_and", "and"),
    ("ar", "tos_terms", "الشروط"),
    ("ar", "tos_and", "و"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for locale, key, value in RETIRED:
        conn.execute(
            sa.text(
                "DELETE FROM ui_translations "
                "WHERE locale = :locale AND namespace = 'auth' "
                "AND key = :key AND value = :value"
            ),
            {"locale": locale, "key": key, "value": value},
        )


def downgrade() -> None:
    # Restoring these would put back two strings nothing renders. The signup
    # copy is the thing that changed; reverting that is a code revert.
    pass
