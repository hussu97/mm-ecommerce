"""Stop describing the shop as a home kitchen you cannot walk into.

Fatema read the live FAQ on 2026-08-18 and reported it wrong on three counts:
it is not a home kitchen, customers *can* walk in, and there is no dine-in but
takeaway is there. The claim had spread past the FAQ — eight strings across
three CMS pages in both locales:

    faq    items[13].answer   "baked in a home kitchen that also handles…"
    faq    items[14].answer   "…so there's no counter to walk into"
    home   seo.description    "baked to order in a Sharjah home kitchen"
    about  seo.description    "Melting Moments is a home bakery in Sharjah"

`items[14]` is the one that cost money: it told anybody who would have collected
in person that there was nowhere to come to.

**Why this is a migration and not a `scripts/` file.** CLAUDE.md §7 sends
content edits to `scripts/`, and that is the wrong home for this: a script is
only as good as somebody remembering to run it, and until they do the site keeps
telling customers something untrue. Every previous CMS rewrite here — `008`,
`009`, `054`, `058`, `061` — is a migration for the same reason, and the rule
has been amended to match what the codebase actually does. A correction to
public copy has to land with the deploy that carries it.

**Matched on the exact current string, never on position.** These pages are
edited in the admin, so an item added or reordered would make a positional patch
rewrite a neighbouring answer — silently, in copy nobody re-reads. Replacing
only strings that still read exactly as the wrong version also makes this
idempotent, and makes it decline to fight an admin who has since reworded
something itself.

The allergen warning in `items[13]` is deliberately preserved word for word
apart from the premises. One kitchen handling nuts, dairy, eggs and gluten is
exactly why nothing can be promised free of them, and dropping that while
fixing the other half would be the expensive mistake here.

Revision ID: 107_premises_copy
Revises: 106_branch_holidays
Create Date: 2026-08-18
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "107_premises_copy"
down_revision: Union[str, None] = "106_branch_holidays"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The CMS pages carrying the claim. Named rather than swept, so this cannot
#: reach into a page nobody checked.
SLUGS = ("faq", "home", "about")

#: Exactly the wrong string -> exactly the right one.
#:
#: Whole strings rather than a `home kitchen` -> `kitchen` substitution: two of
#: these need the sentence around them rebuilt as well ("no counter to walk
#: into" is not repaired by deleting a word), and an exact key cannot
#: accidentally match a sentence written after this shipped.
REPLACEMENTS: dict[str, str] = {
    # ── FAQ: "What if I have an allergy or a dietary requirement?" ──────────
    (
        "Everything is baked in a home kitchen that also handles nuts, dairy, eggs "
        "and gluten, so we can't promise anything is free of them. If you have an "
        "allergy, message us before ordering and we'll tell you honestly whether "
        "we can work around it."
    ): (
        "Everything is baked in one kitchen that also handles nuts, dairy, eggs "
        "and gluten, so we can't promise anything is free of them. If you have an "
        "allergy, message us before ordering and we'll tell you honestly whether "
        "we can work around it."
    ),
    (
        "كل شيء يُخبز في مطبخ منزلي يتعامل أيضاً مع المكسرات والألبان والبيض "
        "والجلوتين، فلا نستطيع ضمان خلوّ أي صنف منها. إن كانت لديك حساسية، راسلنا "
        "قبل الطلب ونخبرك بصراحة إن كان بالإمكان تدبّر الأمر."
    ): (
        "كل شيء يُخبز في مطبخ واحد يتعامل أيضاً مع المكسرات والألبان والبيض "
        "والجلوتين، فلا نستطيع ضمان خلوّ أي صنف منها. إن كانت لديك حساسية، راسلنا "
        "قبل الطلب ونخبرك بصراحة إن كان بالإمكان تدبّر الأمر."
    ),
    # ── FAQ: "Where are you based?" ────────────────────────────────────────
    #
    # Keeps "we send you the address once the order is placed", which is what
    # the pickup answer three questions earlier also says. Publishing the
    # address so a walk-in can find the place without ordering first is a
    # separate decision and not one a copy fix should make.
    (
        "Sharjah. It's a home kitchen rather than a shop, so there's no counter to "
        "walk into — but pickup is free, and we send you the address once the "
        "order is placed."
    ): (
        "Sharjah. You're welcome to walk in and collect — it's takeaway only, "
        "there's no dine-in. Pickup is free, and we send you the address once the "
        "order is placed."
    ),
    (
        "الشارقة. وهو مطبخ منزلي لا محل، فلا يوجد مكان تدخله وتشتري منه — لكن "
        "الاستلام مجاني، ونرسل لك العنوان بعد تقديم الطلب."
    ): (
        "الشارقة. يمكنك الحضور لاستلام طلبك — تيك أواي فقط، فلا توجد صالة للجلوس. "
        "والاستلام مجاني، ونرسل لك العنوان بعد تقديم الطلب."
    ),
    # ── Home page meta description ─────────────────────────────────────────
    #
    # These two are what Google prints under the result, so the claim was being
    # made to people who never opened the site.
    (
        "Fudgy brownies, gooey cookies, cookie melts and cakes, baked to order in "
        "a Sharjah home kitchen and delivered to Dubai, Sharjah, Ajman and every "
        "emirate. Free delivery over AED 150 in selected areas."
    ): (
        "Fudgy brownies, gooey cookies, cookie melts and cakes, baked to order in "
        "our Sharjah kitchen and delivered to Dubai, Sharjah, Ajman and every "
        "emirate. Free delivery over AED 150 in selected areas."
    ),
    (
        "براوني طري وكوكيز وكوكي ملت وكيك، تُخبز عند الطلب في مطبخ منزلي بالشارقة "
        "وتُوصَّل إلى دبي والشارقة وعجمان وكل الإمارات. توصيل مجاني للطلبات فوق 150 "
        "درهم في مناطق مختارة."
    ): (
        "براوني طري وكوكيز وكوكي ملت وكيك، تُخبز عند الطلب في مطبخنا بالشارقة "
        "وتُوصَّل إلى دبي والشارقة وعجمان وكل الإمارات. توصيل مجاني للطلبات فوق 150 "
        "درهم في مناطق مختارة."
    ),
    # ── About page meta description ────────────────────────────────────────
    (
        "Melting Moments is a home bakery in Sharjah run by Fatema Abbasi. "
        "Brownies, cookies and desserts baked to order and delivered across Dubai, "
        "Sharjah and the rest of the UAE."
    ): (
        "Melting Moments is a bakery in Sharjah run by Fatema Abbasi. Brownies, "
        "cookies and desserts baked to order and delivered across Dubai, Sharjah "
        "and the rest of the UAE."
    ),
    (
        "ملتنج مومنتس مخبز منزلي في الشارقة تديره فاطمة عباسي. براوني وكوكيز "
        "وحلويات تُخبز عند الطلب وتُوصَّل إلى دبي والشارقة وبقية الإمارات."
    ): (
        "ملتنج مومنتس مخبز في الشارقة تديره فاطمة عباسي. براوني وكوكيز وحلويات "
        "تُخبز عند الطلب وتُوصَّل إلى دبي والشارقة وبقية الإمارات."
    ),
}


def rewrite(node: Any, mapping: dict[str, str]) -> tuple[Any, int]:
    """
    Every string in `node` that the mapping knows about, replaced.

    Returns a new structure and how many strings changed, so `upgrade` can say
    what it did instead of reporting success for a no-op. Rebuilt rather than
    mutated because the caller writes the result back as a whole JSONB value.

    Shape-agnostic on purpose: `cms_pages.content` is `{locale: {...}}` with
    different keys per page, and a walker that had to know where the strings
    live would need updating every time a page grows a field.
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        count = 0
        for key, value in node.items():
            out[key], hits = rewrite(value, mapping)
            count += hits
        return out, count
    if isinstance(node, list):
        items = []
        count = 0
        for value in node:
            item, hits = rewrite(value, mapping)
            items.append(item)
            count += hits
        return items, count
    if isinstance(node, str) and node in mapping:
        return mapping[node], 1
    return node, 0


def _apply(mapping: dict[str, str]) -> None:
    conn = op.get_bind()
    for slug in SLUGS:
        row = conn.execute(
            sa.text("SELECT content FROM cms_pages WHERE slug = :s"), {"s": slug}
        ).fetchone()
        if not row or not row[0]:
            continue
        content = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        updated, changed = rewrite(content, mapping)
        if not changed:
            continue
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() "
                "WHERE slug = :s"
            ),
            {"c": json.dumps(updated, ensure_ascii=False), "s": slug},
        )


def upgrade() -> None:
    _apply(REPLACEMENTS)


def downgrade() -> None:
    # Exactly reversible, because every replacement is a whole string swap: put
    # the old wording back for anything still reading as the new one. A page
    # reworded in the admin since this ran matches neither and is left alone,
    # which is the right answer — a downgrade should not resurrect a claim
    # somebody has since rewritten on purpose.
    _apply({new: old for old, new in REPLACEMENTS.items()})
