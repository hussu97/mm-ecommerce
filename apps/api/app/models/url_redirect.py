from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin

#: The only two statuses this table may serve.
#:
#: Both are permanent. 308 is the default because it keeps the method on the way
#: through and every search engine treats it as a 301; 301 is kept because some
#: very old clients and a few link checkers still only understand that one, and
#: an operator who knows they are dealing with one should be able to say so.
#: Temporary redirects are deliberately absent — a row in a table that outlives
#: deploys is not how you express "for the next hour", and a 302 that gets
#: forgotten is a URL that never consolidates.
ALLOWED_STATUS_CODES = (301, 308)

#: Where a row came from, which is the difference between "a human decided this"
#: and "a rename wrote it". Only the first is safe to edit freely; the second is
#: rewritten if the same category is renamed again.
ALLOWED_SOURCES = ("manual", "category_rename", "seed")


class UrlRedirect(Base, UUIDMixin, TimestampMixin):
    """
    One old URL and where it goes now.

    Paths are stored **without a locale prefix** — `/cat-brownies`, not
    `/en/cat-brownies` — and apply to both languages, because a slug rename
    breaks both at once and asking an operator to write the same rule twice is
    asking for the second one to be forgotten. A path that genuinely only exists
    in one language may still be written out in full (`/en/about-me`), and the
    resolver tries that exact form first, so specific beats general.

    The table is kept **flat**: `redirect_service` collapses A→B→C into A→C on
    write rather than letting the resolver walk a chain. A chain is a redirect
    that costs two round trips, loses a little ranking signal at each hop, and
    can be turned into a loop by one more innocent-looking rename.
    """

    __tablename__ = "url_redirects"

    #: Mirrors `119_url_redirects`. Spelled out rather than derived from an enum
    #: because these are not a lifecycle — see convention 6; the vocabulary lives
    #: in the migration and is repeated here so the ORM metadata matches.
    __table_args__ = (
        CheckConstraint(
            "status_code IN (301, 308)",
            name="ck_url_redirects_status_code_allowed",
        ),
        CheckConstraint(
            "source IN ('manual', 'category_rename', 'seed')",
            name="ck_url_redirects_source_allowed",
        ),
        # A row pointing at itself is an infinite loop the browser gives up on.
        # The service refuses it too; this is the backstop for anything that
        # reaches the table another way.
        CheckConstraint(
            "from_path <> to_path",
            name="ck_url_redirects_not_self",
        ),
        # Both ends are absolute site paths. Without this a half-typed "brownies"
        # in the console becomes a redirect to a relative URL, which resolves
        # against whatever page the visitor happened to be on.
        CheckConstraint(
            "from_path LIKE '/%' AND to_path LIKE '/%'",
            name="ck_url_redirects_absolute_paths",
        ),
    )

    from_path: Mapped[str] = mapped_column(
        String(500), unique=True, nullable=False, index=True
    )
    to_path: Mapped[str] = mapped_column(String(500), nullable=False)

    #: Whether everything *below* this path moves too.
    #:
    #: This is what makes a category rename safe. `/cat-mixboxes` → `/mix-boxes`
    #: as a prefix also carries `/cat-mixboxes/mix-cookies-box-of-9`, which is
    #: the URL Google actually has — there are 36 product URLs nested under the
    #: eight category slugs and none of them would be covered by an exact match.
    #: Off by default: a hand-written rule is usually about one page, and a
    #: prefix rule written by accident silently swallows a whole subtree.
    is_prefix: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    status_code: Mapped[int] = mapped_column(
        Integer, nullable=False, default=308, server_default="308"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default="manual"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: How many times this rule has fired, and when it last did.
    #:
    #: The only question anyone asks of a redirect table a year later is "can
    #: this row go?", and without these the answer is a guess. Written from
    #: `POST /redirects/hit` on its own session — see `redirect_service.record_hit`.
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_hit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<UrlRedirect {self.from_path} -> {self.to_path}>"
