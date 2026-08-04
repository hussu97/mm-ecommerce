"""
Whether the confirmation page asks someone to sign up or to sign in.

Getting this backwards is a dead end, not a cosmetic slip: a returning customer
shown "create an account" fills in a password, submits, and is told the email is
taken — an error for having done nothing wrong, at the one moment they were most
willing to sign up.

The answer rides on the order rather than living behind an "is this email
registered?" endpoint. That endpoint would answer the question for any address
anyone cared to type; this one only answers it for a caller who already holds
the order number and the email that placed it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services import order_service


def _db(found: bool) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = ("some-id",) if found else None
    db.execute = AsyncMock(return_value=result)
    return db


async def test_a_registered_email_is_recognised():
    assert await order_service._email_has_account(_db(True), "hi@example.com") is True


async def test_an_unknown_email_is_not():
    assert await order_service._email_has_account(_db(False), "hi@example.com") is False


async def test_an_order_with_no_email_asks_nothing():
    db = _db(True)
    assert await order_service._email_has_account(db, None) is False
    assert await order_service._email_has_account(db, "") is False
    db.execute.assert_not_awaited()


async def test_the_lookup_ignores_case_and_stray_whitespace():
    """
    The address is whatever was typed into the checkout. `Hi@Example.com ` and
    `hi@example.com` are one account, and treating them as two sends a
    returning customer down the sign-up path.
    """
    db = _db(True)
    await order_service._email_has_account(db, "  Hi@Example.COM ")

    compiled = db.execute.await_args[0][0].compile()
    assert "hi@example.com" in str(compiled.params.values()).lower()


async def test_guests_are_not_accounts():
    """
    Checkout mints a `…@guest.local` user for every anonymous order, so "a user
    row exists" is true for practically every order ever placed. Counting those
    would tell almost everyone to sign in to an account that has no password.
    """
    db = _db(True)
    await order_service._email_has_account(db, "someone@example.com")

    where = str(db.execute.await_args[0][0])
    assert "is_guest" in where, "the guest rows are no longer excluded"
