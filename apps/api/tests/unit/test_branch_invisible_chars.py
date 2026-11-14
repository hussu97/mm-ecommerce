"""Branch text is saved without invisible format characters.

The Sharjah phone was pasted from iOS Contacts as U+202A 504865186 U+202C; the
till's receipt encoder aborted on those marks, so every reprint crashed
(2026-11-14). The till now survives them, and the API no longer stores them.
"""

from __future__ import annotations

import pytest

from app.schemas.pos.branches import BranchCreate, BranchUpdate, strip_invisible


def test_pasted_phone_loses_its_bidi_marks_on_update():
    assert BranchUpdate(phone="‪504865186‬").phone == "504865186"


def test_create_strips_every_printed_field():
    branch = BranchCreate(
        name="‏Sharjah Kitchen",
        reference="K001",
        phone="⁦+971 50 486 5186⁩",
        address="Al Nahda﻿",
        receipt_footer="Thank you​",
    )
    assert branch.name == "Sharjah Kitchen"
    assert branch.phone == "+971 50 486 5186"
    assert branch.address == "Al Nahda"
    assert branch.receipt_footer == "Thank you"


@pytest.mark.parametrize("value", ["504865186", "مطبخ الشارقة", None, ""])
def test_ordinary_text_is_untouched(value):
    assert strip_invisible(value) == value


def test_unset_fields_stay_unset_on_a_partial_update():
    assert BranchUpdate(phone="504865186").model_dump(exclude_unset=True) == {
        "phone": "504865186"
    }
