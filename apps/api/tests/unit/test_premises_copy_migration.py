"""
Migration 107, at the level of the rewrite it computes.

Content in a migration is the arrangement this codebase has always used for CMS
copy, and the risk it carries is that it runs unattended against a database
somebody else has been editing. So the properties worth pinning are not "does it
change the words" but the two that decide whether it is safe to let it run on
its own: it matches whole strings rather than positions, and it does nothing at
all to anything it does not recognise.

The fixture is the content as the live API actually returned it on 2026-08-18,
not as `054` seeded it — those had drifted, and a test written against the seed
would have passed while the migration matched nothing in production.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "107_premises_copy_correction.py"
)
_spec = importlib.util.spec_from_file_location("premises_copy_migration", _PATH)
migration = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(migration)

REPLACEMENTS = migration.REPLACEMENTS
rewrite = migration.rewrite

ALLERGY_EN = (
    "Everything is baked in a home kitchen that also handles nuts, dairy, eggs "
    "and gluten, so we can't promise anything is free of them. If you have an "
    "allergy, message us before ordering and we'll tell you honestly whether "
    "we can work around it."
)
BASED_EN = (
    "Sharjah. It's a home kitchen rather than a shop, so there's no counter to "
    "walk into — but pickup is free, and we send you the address once the "
    "order is placed."
)
BASED_AR = (
    "الشارقة. وهو مطبخ منزلي لا محل، فلا يوجد مكان تدخله وتشتري منه — لكن "
    "الاستلام مجاني، ونرسل لك العنوان بعد تقديم الطلب."
)
HOME_SEO_EN = (
    "Fudgy brownies, gooey cookies, cookie melts and cakes, baked to order in "
    "a Sharjah home kitchen and delivered to Dubai, Sharjah, Ajman and every "
    "emirate. Free delivery over AED 150 in selected areas."
)
ABOUT_SEO_EN = (
    "Melting Moments is a home bakery in Sharjah run by Fatema Abbasi. "
    "Brownies, cookies and desserts baked to order and delivered across Dubai, "
    "Sharjah and the rest of the UAE."
)


def faq_page() -> dict:
    """`cms_pages.content` for `faq`, in the `{locale: …}` shape the row holds."""
    return {
        "en": {
            "header": {"title": "Frequently Asked Questions"},
            "seo": {"description": "Delivery, timings, payment and allergies."},
            "items": [
                {
                    "question": "Can I pick up my order?",
                    "answer": (
                        "Yes, and it's free. Once you've ordered we'll confirm the "
                        "time and the Sharjah address on WhatsApp."
                    ),
                },
                {
                    "question": "What if I have an allergy or a dietary requirement?",
                    "answer": ALLERGY_EN,
                },
                {"question": "Where are you based?", "answer": BASED_EN},
            ],
            "cta": {"title": "Ask us anything"},
        },
        "ar": {"items": [{"question": "أين مقرّكم؟", "answer": BASED_AR}]},
    }


def test_it_rewrites_every_string_it_knows_and_counts_them():
    page = faq_page()

    out, changed = rewrite(page, REPLACEMENTS)

    assert changed == 3, "two English answers and one Arabic"
    assert out["en"]["items"][1]["answer"] == REPLACEMENTS[ALLERGY_EN]
    assert out["en"]["items"][2]["answer"] == REPLACEMENTS[BASED_EN]
    assert out["ar"]["items"][0]["answer"] == REPLACEMENTS[BASED_AR]


def test_the_three_reported_claims_are_gone():
    """Not a home kitchen, you can walk in, and there is no dine-in."""
    out, _ = rewrite(faq_page(), REPLACEMENTS)
    based = out["en"]["items"][2]["answer"]

    assert "home kitchen" not in out["en"]["items"][1]["answer"]
    assert "home kitchen" not in based
    assert "no counter to walk into" not in based
    assert "walk in and collect" in based
    assert "no dine-in" in based
    assert "مطبخ منزلي" not in out["ar"]["items"][0]["answer"]


def test_the_allergen_warning_survives_intact():
    """
    Only the premises was wrong. One kitchen handling all four is exactly why
    nothing can be promised free of them, and losing that while fixing the other
    half would be the expensive mistake here.
    """
    answer = REPLACEMENTS[ALLERGY_EN]

    for allergen in ("nuts", "dairy", "eggs", "gluten"):
        assert allergen in answer
    assert "can't promise anything is free of them" in answer
    assert "message us before ordering" in answer


def test_it_covers_the_meta_descriptions_search_engines_print():
    """
    These are the copies a customer reads without opening the site, which is why
    the fix could not stop at the FAQ.
    """
    for old in (HOME_SEO_EN, ABOUT_SEO_EN):
        assert old in REPLACEMENTS
        assert "home kitchen" not in REPLACEMENTS[old]
        assert "home bakery" not in REPLACEMENTS[old]


def test_it_leaves_everything_it_does_not_recognise_exactly_alone():
    before = faq_page()

    out, _ = rewrite(copy.deepcopy(before), REPLACEMENTS)

    assert out["en"]["items"][0] == before["en"]["items"][0]
    assert out["en"]["header"] == before["en"]["header"]
    assert out["en"]["seo"] == before["en"]["seo"]
    assert out["en"]["cta"] == before["en"]["cta"]
    # The questions are not in the mapping, so they must come through untouched.
    assert [i["question"] for i in out["en"]["items"]] == [
        i["question"] for i in before["en"]["items"]
    ]


def test_running_it_twice_is_a_no_op_the_second_time():
    """
    The property that makes it safe to leave in the chain. Alembic will not
    re-run it, but a restored dump replayed through `upgrade head` would.
    """
    once, first = rewrite(faq_page(), REPLACEMENTS)
    twice, second = rewrite(once, REPLACEMENTS)

    assert first == 3
    assert second == 0
    assert twice == once


def test_an_answer_reworded_in_the_admin_is_left_to_stand():
    """
    The failure that would matter, and the reason this matches whole strings
    rather than positions. Somebody edits the answer in Admin → Content; this
    must then recognise nothing and change nothing, rather than fall back to
    position and overwrite their wording.
    """
    page = faq_page()
    page["en"]["items"][2]["answer"] = "Sharjah — come and say hello, we're open 9–7."

    out, changed = rewrite(page, REPLACEMENTS)

    assert changed == 2, "the two it still recognises, not the reworded one"
    assert out["en"]["items"][2]["answer"] == page["en"]["items"][2]["answer"]


def test_it_does_not_mutate_the_content_it_was_given():
    before = faq_page()
    snapshot = copy.deepcopy(before)

    rewrite(before, REPLACEMENTS)

    assert before == snapshot


def test_the_downgrade_mapping_is_a_clean_inverse():
    """
    Every replacement is a whole-string swap, so reversing the dict is a real
    downgrade rather than an approximation — provided no two old strings map to
    the same new one, which this asserts.
    """
    assert len(set(REPLACEMENTS.values())) == len(REPLACEMENTS)

    forward, _ = rewrite(faq_page(), REPLACEMENTS)
    back, _ = rewrite(forward, {new: old for old, new in REPLACEMENTS.items()})

    assert back == faq_page()


@pytest.mark.parametrize("old,new", list(REPLACEMENTS.items()))
def test_no_replacement_still_contains_the_claim(old, new):
    for banned in ("home kitchen", "home bakery", "مطبخ منزلي", "مخبز منزلي"):
        assert banned not in new, f"{banned!r} survived in {new!r}"


# ── 109: the four the first sweep missed ─────────────────────────────────────
#
# `107` swept `cms_pages` and stopped there. The same claim was also in
# `ui_translations` and in a blog post, seeded by different migrations. These
# tests exist mostly so the omission is written down somewhere that runs.

_PATH_109 = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "109_premises_copy_remainder.py"
)
_spec_109 = importlib.util.spec_from_file_location("premises_remainder", _PATH_109)
remainder = importlib.util.module_from_spec(_spec_109)
assert _spec_109.loader is not None
_spec_109.loader.exec_module(remainder)


def test_the_blog_sentence_is_matched_inside_a_body():
    """
    A blog body is one long string with the claim buried in it, so `109` matches
    substrings where `107` matched whole values. The regression to guard is the
    opposite of `107`'s: too *little* matching, silently leaving the post alone.
    """
    body = (
        "Ask before ordering. Sometimes it's an honest no.\n\n"
        "One thing we can't change: it's a home kitchen that also handles eggs, "
        "so an eggless recipe isn't an allergen-free environment. If it's an "
        "allergy rather than a preference, tell us."
    )
    post = {"en": {"title": "Eggless baking", "body": body}}

    out, changed = remainder.rewrite(post, remainder.BLOG_REPLACEMENTS)

    assert changed == 1
    assert "home kitchen" not in out["en"]["body"]
    assert "one kitchen that also handles eggs" in out["en"]["body"]
    # The sentences either side must survive untouched.
    assert out["en"]["body"].startswith("Ask before ordering.")
    assert out["en"]["body"].endswith("tell us.")
    assert "allergen-free environment" in out["en"]["body"]


def test_the_blog_rewrite_is_idempotent():
    post = {"en": {"body": list(remainder.BLOG_REPLACEMENTS)[0]}}

    once, first = remainder.rewrite(post, remainder.BLOG_REPLACEMENTS)
    twice, second = remainder.rewrite(once, remainder.BLOG_REPLACEMENTS)

    assert first == 1 and second == 0
    assert twice == once


def test_no_109_replacement_still_carries_the_claim():
    for mapping in (remainder.UI_TRANSLATIONS, remainder.BLOG_REPLACEMENTS):
        for new in mapping.values():
            for banned in ("home kitchen", "home bakery", "مطبخ منزلي", "مخبز منزلي"):
                assert banned not in new, f"{banned!r} survived in {new!r}"


def test_109_mappings_are_cleanly_invertible():
    for mapping in (remainder.UI_TRANSLATIONS, remainder.BLOG_REPLACEMENTS):
        assert len(set(mapping.values())) == len(mapping)
        assert not set(mapping) & set(mapping.values()), (
            "an old string is also a new one"
        )


# ── 110: publishing the address ──────────────────────────────────────────────

_PATH_110 = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "110_publish_collection_address.py"
)
_spec_110 = importlib.util.spec_from_file_location("publish_address", _PATH_110)
publish = importlib.util.module_from_spec(_spec_110)
assert _spec_110.loader is not None
_spec_110.loader.exec_module(publish)


def test_both_pickup_answers_name_the_sharjah_counter():
    """
    The state 107 left behind was worse than either end of it: an invitation to
    walk in, followed by a refusal to say where.
    """
    for new in publish.FAQ_REPLACEMENTS.values():
        assert publish.SHARJAH in new or publish.SHARJAH_AR in new
        assert "send you the address once the order is placed" not in new
        assert "عنوان الاستلام في" not in new


def test_the_second_collection_point_is_mentioned_but_not_addressed():
    """
    Barsha Heights is a counter inside somebody else's café. Customers should
    know it exists; printing a partner's address in editorial copy is not a
    call a copy migration gets to make, and the checkout already lists it.
    """
    english = [v for v in publish.FAQ_REPLACEMENTS.values() if "Sharjah" in v]
    assert english, "expected the English answers"
    for new in english:
        assert "Barsha Heights" in new
        assert "Attibassi" not in new
        assert "Al Shafar" not in new


def test_the_about_block_carries_no_address():
    """
    The whole point of `find_us` being copy-only: the page fetches the branches
    and prints their addresses, so this block must never hold one itself.
    """
    for block in publish.ABOUT_FIND_US.values():
        for value in block.values():
            assert "Garden Tower" not in value
            assert "Al Majaz" not in value
            assert "Barsha" not in value


def test_110_rewrite_is_idempotent_and_leaves_strangers_alone():
    page = {
        "en": {
            "items": [
                {
                    "question": "Where are you based?",
                    "answer": list(publish.FAQ_REPLACEMENTS)[0],
                },
                {"question": "Something else", "answer": "Unrelated answer."},
            ]
        }
    }

    once, first = publish.rewrite(page, publish.FAQ_REPLACEMENTS)
    twice, second = publish.rewrite(once, publish.FAQ_REPLACEMENTS)

    assert first == 1 and second == 0
    assert twice == once
    assert once["en"]["items"][1]["answer"] == "Unrelated answer."


def test_110_mapping_is_cleanly_invertible():
    m = publish.FAQ_REPLACEMENTS
    assert len(set(m.values())) == len(m)
    assert not set(m) & set(m.values())
