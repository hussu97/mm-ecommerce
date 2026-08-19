"""
Correct two FAQ answers that describe the premises wrongly.

Reported by Fatema on 2026-08-18, reading the live page: it is not a home
kitchen, customers *can* walk in, there is no dine-in, and takeaway is there.
Two of the sixteen answers said otherwise, in both locales:

* **"What if I have an allergy or a dietary requirement?"** opened with
  "Everything is baked in a home kitchen…". The allergen warning itself is
  unchanged and still true — one kitchen handling nuts, dairy, eggs and gluten
  is exactly why nothing can be promised free of them. Only the word describing
  the premises was wrong.

* **"Where are you based?"** said "there's no counter to walk into", which is
  the opposite of the truth and the reason this was worth fixing quickly: it
  turned away anybody who would have collected in person.

The replacement keeps "we send you the address once the order is placed",
because that is what the pickup answer three questions earlier also says and
nothing in the report contradicts it. If the address should now be published so
somebody can walk in without ordering first, that is a second change and this
script is not it.

**A script and not a migration**, per CLAUDE.md §7: this is CMS copy. It is also
not a thing the deploy applies — run it against the environment's own
`DATABASE_URL`, or make the same two edits in Admin → Content → FAQ, which
writes the same rows.

    python -m scripts.fix_faq_premises            # report what would change
    python -m scripts.fix_faq_premises --apply

Idempotent: it matches on the question, replaces the answer, and reports an
answer that is already correct as unchanged rather than writing it again.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import select

from app.core.database import AsyncSessionFactory
from app.models.cms import CmsPage

#: locale -> question -> the answer it should have.
#:
#: Keyed by the question rather than by index: the FAQ is edited in the admin,
#: and an item added or reordered there would make a positional patch rewrite
#: the wrong answer — silently, in the customer-facing copy.
CORRECTIONS: dict[str, dict[str, str]] = {
    "en": {
        "What if I have an allergy or a dietary requirement?": (
            "Everything is baked in one kitchen that also handles nuts, dairy, eggs "
            "and gluten, so we can't promise anything is free of them. If you have "
            "an allergy, message us before ordering and we'll tell you honestly "
            "whether we can work around it."
        ),
        "Where are you based?": (
            "Sharjah. You're welcome to walk in and collect — it's takeaway only, "
            "there's no dine-in. Pickup is free, and we send you the address once "
            "the order is placed."
        ),
    },
    "ar": {
        "ماذا لو كان لديّ حساسية أو متطلب غذائي؟": (
            "كل شيء يُخبز في مطبخ واحد يتعامل أيضاً مع المكسرات والألبان والبيض "
            "والجلوتين، فلا نستطيع ضمان خلوّ أي صنف منها. إن كانت لديك حساسية، "
            "راسلنا قبل الطلب ونخبرك بصراحة إن كان بالإمكان تدبّر الأمر."
        ),
        "أين مقرّكم؟": (
            "الشارقة. يمكنك الحضور لاستلام طلبك — تيك أواي فقط، فلا توجد صالة "
            "للجلوس. والاستلام مجاني، ونرسل لك العنوان بعد تقديم الطلب."
        ),
    },
}


def _patch(content: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Return the corrected content, what changed, and what could not be found."""
    changed: list[str] = []
    missing: list[str] = []
    # Rebuilt rather than mutated in place: `content` is a JSONB value loaded by
    # SQLAlchemy, and mutating it does not reliably mark the attribute dirty.
    out = {**content}

    for locale, answers in CORRECTIONS.items():
        section = out.get(locale)
        if not isinstance(section, dict):
            missing.append(f"{locale}: locale missing")
            continue
        items = section.get("items")
        if not isinstance(items, list):
            missing.append(f"{locale}: no items")
            continue

        new_items = []
        seen: set[str] = set()
        for item in items:
            question = (item or {}).get("question")
            wanted = answers.get(question)
            if wanted is None:
                new_items.append(item)
                continue
            seen.add(question)
            if item.get("answer") == wanted:
                new_items.append(item)
                continue
            new_items.append({**item, "answer": wanted})
            changed.append(f"{locale}: {question}")
        missing.extend(f"{locale}: {q}" for q in answers if q not in seen)
        out[locale] = {**section, "items": new_items}

    return out, changed, missing


async def _run(apply: bool) -> int:
    async with AsyncSessionFactory() as db:
        page = (
            await db.execute(select(CmsPage).where(CmsPage.slug == "faq"))
        ).scalar_one_or_none()
        if page is None:
            print("No cms_pages row with slug 'faq'.", file=sys.stderr)
            return 1

        updated, changed, missing = _patch(page.content or {})

        for entry in missing:
            print(f"  ! not found — {entry}", file=sys.stderr)
        if not changed:
            print("Nothing to change; the FAQ already reads correctly.")
            return 1 if missing else 0

        for entry in changed:
            print(f"  · {entry}")
        if not apply:
            print(f"\n{len(changed)} answer(s) would change. Re-run with --apply.")
            return 0

        page.content = updated
        await db.commit()
        print(f"\n{len(changed)} answer(s) updated.")
        # A question that vanished is worth a non-zero exit even on a successful
        # run: it means the admin has since renamed it and this script now has a
        # stale key that will quietly do nothing next time.
        return 1 if missing else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Reports only without it.",
    )
    raise SystemExit(asyncio.run(_run(parser.parse_args().apply)))


if __name__ == "__main__":
    main()
