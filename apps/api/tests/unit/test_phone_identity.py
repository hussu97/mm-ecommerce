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
from app.core.phone import normalise_phone, phone_identities
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
