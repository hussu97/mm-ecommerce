"""
PIN sign-in must not sign the wrong person in on a collision (F-POS-7).

Two halves: PINs are unique per branch at write time (`_assert_pin_unique`), so
the login scan can match at most one person; and when the terminal names who is
signing in, only that one hash is verified (`_match_pin`) — O(1) bcrypt, and a
leftover collision can never land a cashier on a manager's session.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.staff import _assert_pin_unique, _match_pin
from app.core.exceptions import ConflictError
from app.core.security import hash_password


def _staff(pin: str | None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        pin_hash=hash_password(pin) if pin else None,
    )


def _db_returning(users: list):
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = users
    return SimpleNamespace(execute=AsyncMock(return_value=result))


@pytest.mark.asyncio
class TestPinUniquenessAtWriteTime:
    async def test_a_duplicate_pin_at_a_shared_branch_is_refused(self):
        existing = _staff("1234")
        db = _db_returning([existing])
        with pytest.raises(ConflictError, match="already used"):
            await _assert_pin_unique(
                db, pin="1234", branch_ids=[uuid.uuid4()], exclude_id=uuid.uuid4()
            )

    async def test_a_unique_pin_is_accepted(self):
        existing = _staff("9999")
        db = _db_returning([existing])
        # Does not raise — a different PIN.
        await _assert_pin_unique(
            db, pin="1234", branch_ids=[uuid.uuid4()], exclude_id=uuid.uuid4()
        )

    async def test_no_branches_or_no_pin_is_a_noop(self):
        db = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("no query")))
        await _assert_pin_unique(db, pin=None, branch_ids=[uuid.uuid4()])
        await _assert_pin_unique(db, pin="1234", branch_ids=[])


class TestMatchPrefersTheNamedStaff:
    def test_a_named_staff_id_is_verified_alone_not_the_first_collision(self):
        # Two people share a PIN (a pre-fix collision). The terminal names the
        # second; the scan must not return the first.
        pin = "4321"
        first = _staff(pin)
        second = _staff(pin)
        matched = _match_pin([first, second], pin, user_id=second.id)
        assert matched is second

    def test_without_an_id_the_scan_is_the_fallback(self):
        pin = "4321"
        only = _staff(pin)
        assert _match_pin([only], pin, user_id=None) is only

    def test_a_wrong_pin_matches_nobody(self):
        assert _match_pin([_staff("1111")], "2222", user_id=None) is None

    def test_a_named_id_not_in_the_branch_matches_nobody(self):
        assert _match_pin([_staff("1111")], "1111", user_id=uuid.uuid4()) is None
