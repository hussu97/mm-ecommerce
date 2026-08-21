"""Category URLs stop saying `cat-`.

`/en/cat-brownies` is what the Foodics importer left behind: production derives
slugs from the POS `reference`, so `cat_brownies` became `cat-brownies` and the
storefront has been serving it ever since. Nobody searches for `cat-brownies`,
no answer engine repeats it, and `/en/brownies` — which is what both would
expect — is not a page on this site. It is the largest on-page reason nothing
ranks on an unbranded query.

`reference` is **not** touched. That is the Foodics identity and the key the
importer upserts on, and `import_catalogue.py` sets `slug` `create_only`, so a
re-import will not undo this.

**Guarded twice over.** Each rename matches the exact old slug, so a category
somebody has already renamed in the console is left alone — and so is a database
restored from a dump where that rename had happened. The CMS rewrite matches
whole href strings for the same reason. Convention 7: after a human has edited
it, this migration matches nothing and does nothing.

**The redirect rows are the point.** Eight category URLs and the 36 product URLs
nested under them are indexed, and several rank on brand queries today. Each row
is a *prefix* rule, so `/cat-mixboxes/mix-cookies-box-of-9` moves with
`/cat-mixboxes` rather than needing a row of its own.

`/about-me` comes along too. It was two hand-written entries in `next.config.ts`;
this is where it belongs now, where it can be edited without a deploy.

Revision ID: 120_readable_category_slugs
Revises: 119_url_redirects
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "120_readable_category_slugs"
down_revision: Union[str, None] = "119_url_redirects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: old slug -> new slug.
#:
#: Plain plural nouns. Keyword-stuffed slugs (`brownies-dubai`) have not been
#: worth anything to Google for years, make every URL longer, and read badly
#: when an answer engine cites one. The geography lives in the title, the H1 and
#: the copy, where it is a sentence rather than a URL segment.
#:
#: `cookiemelt` becomes `cookie-melts` and `mixboxes` becomes `mix-boxes`: the
#: unspaced forms are how a POS reference is written, not how a person types.
RENAMES: list[tuple[str, str]] = [
    ("cat-brownies", "brownies"),
    ("cat-cookies", "cookies"),
    ("cat-cookiemelt", "cookie-melts"),
    ("cat-mixboxes", "mix-boxes"),
    ("cat-cakes", "cakes"),
    ("cat-desserts", "desserts"),
    ("cat-eggless", "eggless"),
    ("cat-extras", "extras"),
]

#: Legacy URLs with no category behind them. `/about-me` is the Wix site's about
#: page; Google still holds it, and it was answering 200 with the 404 body, which
#: is why it stayed indexed as a second, contentless "about".
LEGACY: list[tuple[str, str, bool, str]] = [
    ("/about-me", "/about", False, "Wix-era URL for the about page."),
]


def _rename_categories(pairs: list[tuple[str, str]]) -> None:
    conn = op.get_bind()
    for old, new in pairs:
        conn.execute(
            sa.text("UPDATE categories SET slug = :new WHERE slug = :old"),
            {"old": old, "new": new},
        )


def _rewrite_hrefs(pairs: list[tuple[str, str]]) -> None:
    """
    Follow the rename into any CMS copy that hard-codes the URL.

    Most category links on the site are rendered from the API and move by
    themselves. What does not is a hero slide's `cta_href` or a promo band's,
    which are free text typed into the console — `HomeEditor` even offers
    `cat-brownies` as the placeholder. Migration `016_fix_cat_brownies_slug` did
    exactly this rewrite for exactly this reason.

    Both the locale-prefixed and bare forms, and both the category href itself and
    anything beneath it, since a hand-typed link to a single product is the other
    thing these fields hold.

    Every pattern keeps its opening quote, which is what stops a match landing in
    the middle of a longer path. The closing quote on the exact form is what
    keeps `"/cat-cookies"` out of `"/cat-cookiemelt"`; the trailing slash on the
    sub-path form does the same job.
    """
    conn = op.get_bind()
    for old, new in pairs:
        for prefix in ("", "/en", "/ar"):
            for suffix in ('"', "/"):
                old_href = f'"{prefix}/{old}{suffix}'
                new_href = f'"{prefix}/{new}{suffix}'
                conn.execute(
                    sa.text(
                        "UPDATE cms_pages SET content = "
                        "REPLACE(content::text, :old, :new)::jsonb "
                        "WHERE content::text LIKE '%' || :old || '%'"
                    ),
                    {"old": old_href, "new": new_href},
                )


def _seed_redirects(rows: list[tuple[str, str, bool, str, str]]) -> None:
    """
    Insert a rule, unless the console already has an opinion about that path.

    `ON CONFLICT DO NOTHING` rather than an upsert: if a row for this `from_path`
    already exists, somebody put it there deliberately and this migration is not
    better informed than they were.
    """
    conn = op.get_bind()
    for from_path, to_path, is_prefix, source, note in rows:
        conn.execute(
            sa.text(
                "INSERT INTO url_redirects "
                "(id, from_path, to_path, is_prefix, status_code, is_active, source, note) "
                "VALUES (gen_random_uuid(), :from_path, :to_path, :is_prefix, 308, true, :source, :note) "
                "ON CONFLICT (from_path) DO NOTHING"
            ),
            {
                "from_path": from_path,
                "to_path": to_path,
                "is_prefix": is_prefix,
                "source": source,
                "note": note,
            },
        )


def upgrade() -> None:
    _rename_categories(RENAMES)
    _rewrite_hrefs(RENAMES)
    _seed_redirects(
        [
            (
                f"/{old}",
                f"/{new}",
                True,
                "seed",
                f"Category slug renamed from '{old}' to '{new}' in migration 120.",
            )
            for old, new in RENAMES
        ]
        + [
            (from_path, to_path, is_prefix, "seed", note)
            for from_path, to_path, is_prefix, note in LEGACY
        ]
    )


def downgrade() -> None:
    reversed_pairs = [(new, old) for old, new in RENAMES]
    _rename_categories(reversed_pairs)
    _rewrite_hrefs(reversed_pairs)
    conn = op.get_bind()
    # Only the rows this migration seeded. A rule an operator added since, or one
    # written by a rename in the console, is theirs and survives the downgrade.
    for from_path in [f"/{old}" for old, _ in RENAMES] + [f for f, _, _, _ in LEGACY]:
        conn.execute(
            sa.text(
                "DELETE FROM url_redirects "
                "WHERE from_path = :from_path AND source = 'seed'"
            ),
            {"from_path": from_path},
        )
