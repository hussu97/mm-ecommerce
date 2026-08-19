"""
The FAQ premises correction, at the level of the patch it computes.

The script writes customer-facing copy into a JSONB column that the admin also
edits, so the two things worth pinning are that it changes exactly the two
answers it means to, and that it cannot be made to rewrite the wrong one. It is
keyed by question rather than by index precisely because an item added or
reordered in the admin would otherwise shift a positional patch onto a
neighbour — silently, in copy nobody re-reads.
"""

from __future__ import annotations

import copy

from scripts.fix_faq_premises import CORRECTIONS, _patch

ALLERGY_EN = "What if I have an allergy or a dietary requirement?"
BASED_EN = "Where are you based?"
ALLERGY_AR = "ماذا لو كان لديّ حساسية أو متطلب غذائي؟"
BASED_AR = "أين مقرّكم؟"


def live() -> dict:
    """The FAQ as migration 054 seeded it, trimmed to what this touches."""
    return {
        "en": {
            "header": {"title": "Frequently Asked Questions"},
            "items": [
                {
                    "question": "Can I pick up my order?",
                    "answer": "Yes, and it's free. Once you've ordered we'll confirm…",
                },
                {
                    "question": ALLERGY_EN,
                    "answer": (
                        "Everything is baked in a home kitchen that also handles nuts, "
                        "dairy, eggs and gluten, so we can't promise anything is free "
                        "of them. If you have an allergy, message us before ordering "
                        "and we'll tell you honestly whether we can work around it."
                    ),
                },
                {
                    "question": BASED_EN,
                    "answer": (
                        "Sharjah. It's a home kitchen rather than a shop, so there's "
                        "no counter to walk into — but pickup is free, and we send you "
                        "the address once the order is placed."
                    ),
                },
            ],
            "cta": {"title": "Ask us anything"},
        },
        "ar": {
            "items": [
                {"question": ALLERGY_AR, "answer": "كل شيء يُخبز في مطبخ منزلي…"},
                {"question": BASED_AR, "answer": "الشارقة. وهو مطبخ منزلي لا محل…"},
            ],
        },
    }


def answers(content: dict, locale: str) -> dict[str, str]:
    return {i["question"]: i["answer"] for i in content[locale]["items"]}


def test_it_corrects_both_answers_in_both_locales():
    out, changed, missing = _patch(live())

    assert missing == []
    assert len(changed) == 4, changed
    for locale, wanted in CORRECTIONS.items():
        for question, answer in wanted.items():
            assert answers(out, locale)[question] == answer


def test_the_premises_claims_are_gone():
    """
    The three statements this exists for: not a home kitchen, you can walk in,
    and there is no dine-in.
    """
    out, _, _ = _patch(live())
    en = answers(out, "en")

    assert "home kitchen" not in en[ALLERGY_EN]
    assert "home kitchen" not in en[BASED_EN]
    assert "no counter to walk into" not in en[BASED_EN]
    assert "walk in" in en[BASED_EN]
    assert "no dine-in" in en[BASED_EN]
    assert "مطبخ منزلي" not in answers(out, "ar")[BASED_AR]


def test_the_allergen_warning_survives():
    """
    Only the word for the premises was wrong. One kitchen handling all four is
    exactly why nothing can be promised free of them, and dropping that while
    fixing the other half would be the expensive mistake here.
    """
    out, _, _ = _patch(live())
    answer = answers(out, "en")[ALLERGY_EN]

    for allergen in ("nuts", "dairy", "eggs", "gluten"):
        assert allergen in answer
    assert "can't promise anything is free of them" in answer


def test_it_leaves_every_other_answer_alone():
    before = live()
    out, _, _ = _patch(copy.deepcopy(before))

    assert (
        answers(out, "en")["Can I pick up my order?"]
        == (answers(before, "en")["Can I pick up my order?"])
    )
    assert out["en"]["header"] == before["en"]["header"]
    assert out["en"]["cta"] == before["en"]["cta"]


def test_running_it_twice_changes_nothing_the_second_time():
    once, _, _ = _patch(live())
    twice, changed, missing = _patch(once)

    assert changed == []
    assert missing == []
    assert twice == once


def test_a_renamed_question_is_reported_rather_than_guessed_at():
    """
    The failure that would matter. If somebody renames the question in the
    admin, this must say so — not fall back to position and rewrite whichever
    answer happens to sit there.
    """
    content = live()
    content["en"]["items"][2]["question"] = "Where can I find you?"

    out, changed, missing = _patch(content)

    assert f"en: {BASED_EN}" in missing
    assert out["en"]["items"][2]["answer"] == content["en"]["items"][2]["answer"]
    assert all(BASED_EN not in c for c in changed)


def test_it_does_not_mutate_what_it_was_given():
    """
    The content comes off a JSONB column; mutating it in place would not
    reliably mark the attribute dirty, so the write would be a silent no-op.
    """
    before = live()
    snapshot = copy.deepcopy(before)
    _patch(before)

    assert before == snapshot
