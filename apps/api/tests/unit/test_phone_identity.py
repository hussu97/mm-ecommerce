"""
One handset is one customer, however the number was typed.

The new-customer coupon counts a customer's orders across account, email and
phone, and the phone is the leg that is supposed to be expensive to fake. It
only is while two spellings of one number compare equal. `0501234567` and
`+971501234567` reaching the identity check as two different strings would make
the whole rule bypassable by retyping your own number — no new SIM, no new
email, just a different format in the same box.

The storefront's `PhoneInput` emits E.164 and would have hidden this. The API is
a public entry point and does not get to assume its own client is the only
caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.phone import describe_phone, normalise_phone, phone_identities
from app.services import firebase_auth_service, promo_code_service


@pytest.mark.parametrize(
    "raw",
    [
        "+971501234567",
        "00971501234567",
        "971501234567",
        "0501234567",
        "050 123 4567",
        "050-123-4567",
        " +971 50 123 4567 ",
    ],
)
def test_every_spelling_of_one_number_reaches_the_same_identity(raw):
    assert normalise_phone(raw) == "+971501234567"


@pytest.mark.parametrize("raw", ["12345", "", None, "not a phone"])
def test_nonsense_is_dropped_rather_than_guessed_at(raw):
    """A guessed identity is worse than none: it merges two customers."""
    assert normalise_phone(raw) == ""


# ── the switch to `phonenumbers` ──────────────────────────────────────────────
#
# `normalise_phone` used to be length arithmetic: `+` and eight characters was
# a phone number, eight-to-ten bare digits was a UAE one. The storefront was
# running libphonenumber-js against the same box, which put the stricter check
# on the side of the wire we do not control. These two blocks are the contract
# of the replacement: everything the shop actually receives still normalises,
# and the things that only ever passed because the old rule was arithmetic no
# longer do.


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Every mobile prefix the UAE assigns, in the shape a form receives it.
        ("0501234567", "+971501234567"),
        ("0521234567", "+971521234567"),
        ("0541234567", "+971541234567"),
        ("0551234567", "+971551234567"),
        ("0561234567", "+971561234567"),
        ("0581234567", "+971581234567"),
        # Written internationally, in every punctuation people use.
        ("+971 50 123 4567", "+971501234567"),
        ("+971 (50) 123-4567", "+971501234567"),
        ("971-50-123-4567", "+971501234567"),
        ("00971 50 123 4567", "+971501234567"),
        # The `(0)` that people carry over from a national spelling.
        ("+971(0)501234567", "+971501234567"),
        # Bare national, no leading zero — what a customer types when the form
        # already shows a +971 prefix.
        ("501234567", "+971501234567"),
        # Landlines. `seed_pos_demo` gives a branch `04 445 1555`, and
        # `lalamove_service` hands a branch's phone straight to the courier.
        ("04 445 1555", "+97144451555"),
        ("+97144451555", "+97144451555"),
        ("026123456", "+97126123456"),
        # A 600 service number, as `test_lalamove_reassignment` uses.
        ("+971600500500", "+971600500500"),
        # Not everyone ordering a cake in Dubai holds a UAE number.
        ("+919876543210", "+919876543210"),
        ("+966501234567", "+966501234567"),
        ("+447911123456", "+447911123456"),
        ("+14155552671", "+14155552671"),
        # Arabic-Indic digits, which an Arabic keyboard produces and the old
        # `isdigit()` cleaning happened to survive. It still does.
        ("٠٥٠١٢٣٤٥٦٧", "+971501234567"),
        # Junk around a real number. The old rule threw away everything that was
        # not a digit, so a stored `0501234567 (Ali)` still reached a driver;
        # the second pass keeps that true.
        ("0501234567 (Ali)", "+971501234567"),
        ("abc0501234567xyz", "+971501234567"),
    ],
)
def test_every_format_the_shop_receives_still_normalises(raw, expected):
    assert normalise_phone(raw) == expected


@pytest.mark.parametrize(
    "raw,was",
    [
        # The headline case. Eight characters after a `+` was the whole test,
        # so this impossible number was an identity — and identities are what
        # the new-customer coupon is counted against.
        ("+12345678", "+12345678"),
        # A UAE mobile one digit short, and one digit long. Both are the shape
        # of a typo, and the old rule passed the first and invented a country
        # code for the second.
        ("+97150123456", "+97150123456"),
        ("05012345678", "+9715012345678"),
        # Two numbers in one box — a note, not a number. The old rule
        # concatenated them into a single 20-digit "identity".
        ("050 123 4567 / 055 765 4321", "+5012345670557654321"),
        # `00` is an international prefix, not a spare pair of digits: this is
        # a landline with the prefix and no country code, which is not a number
        # anybody can dial.
        ("0044451555", "+97144451555"),
    ],
)
def test_what_only_ever_passed_because_the_old_rule_was_arithmetic_is_refused(raw, was):
    """
    Deliberate rejections, each one previously an accepted "identity".

    `was` is what the old function returned, recorded here so the change is
    legible in the test rather than only in a commit message.
    """
    assert was, "the old function accepted this — that is the point of the case"
    assert normalise_phone(raw) == ""


def test_an_extension_is_dropped_rather_than_appended_to_the_number():
    """
    Not a rejection but a correction. `+971-50-123-4567 ext 9` used to come out
    as `+9715012345679`, which is not a handset — the extension was glued onto
    the subscriber number and the result was an identity belonging to nobody.
    """
    assert normalise_phone("+971-50-123-4567 ext 9") == "+971501234567"


def test_identities_include_the_string_as_given():
    """
    Rows written at the counter before this normalisation existed hold whatever
    was typed. A lookup asking only for the canonical form would stop seeing
    them, and a customer's order history would silently reset — handing them the
    new-customer coupon all over again.
    """
    assert phone_identities("050 123 4567") == ["+971501234567", "050 123 4567"]
    # No duplicate clause when the two are already the same.
    assert phone_identities("+971501234567") == ["+971501234567"]
    # Nothing usable means no phone clause at all, not a clause matching nothing.
    assert phone_identities("") == []
    assert phone_identities(None) == []


async def test_orders_are_counted_under_every_spelling_of_the_phone():
    """
    The count is what enforces `first_orders_limit`. It has to reach an order
    stored as `+971501234567` when the checkout hands it `0501234567`, or the
    fourth order gets the new-customer discount.
    """
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar=lambda: 3)

    placed = await promo_code_service.orders_placed_by(
        db, user_id=None, email=None, phone="0501234567"
    )
    assert placed == 3

    compiled = str(
        db.execute.call_args.args[0].compile(compile_kwargs={"literal_binds": True})
    )
    assert "+971501234567" in compiled
    assert "0501234567" in compiled


async def test_a_verified_number_is_found_however_the_checkout_spells_it():
    """
    Firebase writes the ledger row in E.164. The number reaching this check comes
    out of a form. A customer who has just completed an OTP being told to verify
    their phone is the worst possible version of this bug — the button they were
    told to press is the one they already pressed.
    """
    captured = {}

    class _Result:
        @staticmethod
        def scalar():
            return "a-row-id"

    async def _execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return _Result()

    db = SimpleNamespace(execute=_execute)
    assert await firebase_auth_service.is_phone_verified(db, "050 123 4567") is True
    assert "+971501234567" in captured["sql"]


async def test_an_unusable_number_is_never_treated_as_verified():
    db = AsyncMock()
    assert await firebase_auth_service.is_phone_verified(db, None) is False
    assert await firebase_auth_service.is_phone_verified(db, "   ") is False
    db.execute.assert_not_called()


async def test_a_proof_older_than_the_window_does_not_count():
    """
    `PHONE_VERIFICATION_TTL_SECONDS` is the point of the timestamp. A row from
    last year is evidence that somebody once held the number, not that whoever
    is checking out now does.
    """
    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return SimpleNamespace(scalar=lambda: None)

    db = SimpleNamespace(execute=_execute)
    assert await firebase_auth_service.is_phone_verified(db, "+971501234567") is False

    # The cutoff is in the query rather than applied to the result, so the index
    # does the work instead of every row this number ever produced. And it is
    # the configured window, not an arbitrary one.
    expected = datetime.now(timezone.utc) - timedelta(
        seconds=settings.PHONE_VERIFICATION_TTL_SECONDS
    )
    assert "verified_at" in captured["sql"]
    assert expected.strftime("%Y-%m-%d") in captured["sql"]


async def test_the_certificate_fetch_is_not_one_per_unknown_key():
    """
    `kid` comes off an unauthenticated, attacker-supplied token, and an unknown
    one triggers a refetch because that is what a key rotation looks like from
    here. Without a floor between fetches, a stream of random `kid`s makes this
    endpoint an amplifier pointed at Google — one outbound request per inbound.
    """
    firebase_auth_service.reset_keys()
    fetches = 0

    async def _fetch():
        nonlocal fetches
        fetches += 1
        return {"real-kid": {"kid": "real-kid"}}

    with patch.object(firebase_auth_service, "_fetch_keys", new=_fetch):
        # Cold start: the first unknown key is worth a fetch, because holding no
        # keys means every verification is failing anyway.
        with pytest.raises(firebase_auth_service.VerificationError):
            await firebase_auth_service._signing_key("bogus-1")
        assert fetches == 1

        # Now that we hold a usable set, the next hundred forgeries buy nothing.
        for i in range(100):
            with pytest.raises(firebase_auth_service.VerificationError):
                await firebase_auth_service._signing_key(f"bogus-{i}")
        assert fetches == 1

        # And a real key still resolves out of the cache.
        assert await firebase_auth_service._signing_key("real-kid") == {
            "kid": "real-kid"
        }

    firebase_auth_service.reset_keys()


# ── describe_phone: E.164 + ISO country + line type, one shape for storage ──


def test_describe_phone_reads_a_mobile():
    parts = describe_phone("0501234567")
    assert (parts.e164, parts.country, parts.type) == ("+971501234567", "AE", "mobile")


def test_describe_phone_reads_a_uae_landline():
    # A Dubai home line ("04…") must classify as a landline, not be rejected.
    parts = describe_phone("04-4451555")
    assert (parts.e164, parts.country, parts.type) == ("+97144451555", "AE", "landline")


def test_describe_phone_reads_a_toll_free_deliveroo_line():
    parts = describe_phone("+9718000320499")
    assert (parts.e164, parts.country, parts.type) == (
        "+9718000320499",
        "AE",
        "toll_free",
    )


def test_describe_phone_empty_on_junk():
    for raw in ("", None, "0", "UNKNOWN"):
        parts = describe_phone(raw)
        assert (parts.e164, parts.country, parts.type) == ("", None, None)
