"""
The signed receipt token that keeps a customer's email out of the payment
gateway's return URL (F-WEB-2/F-ORD-20).

It has to do exactly two things and nothing more: hand back the order id it was
minted for, and hand back nothing for anything that does not verify — a bad
signature, an expired token, one of the wrong type, or junk — so the ownership
check falls back to the email path rather than raising at a customer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.core import receipt_token
from app.core.security import ALGORITHM


def test_a_token_round_trips_to_its_order_id():
    order_id = uuid.uuid4()
    token = receipt_token.mint(order_id)
    assert receipt_token.order_id_from(token) == order_id


def test_each_order_gets_a_token_only_its_own_id_verifies():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert receipt_token.order_id_from(receipt_token.mint(a)) == a
    assert receipt_token.order_id_from(receipt_token.mint(b)) == b
    assert receipt_token.order_id_from(receipt_token.mint(a)) != b


def test_no_token_is_no_proof_not_an_error():
    assert receipt_token.order_id_from(None) is None
    assert receipt_token.order_id_from("") is None


def test_a_tampered_token_verifies_to_nothing():
    token = receipt_token.mint(uuid.uuid4())
    assert receipt_token.order_id_from(token + "x") is None


def test_an_expired_token_verifies_to_nothing():
    order_id = receipt_token.mint(uuid.uuid4())  # noqa: F841 - shape reference only
    assert (
        receipt_token.order_id_from(
            receipt_token.mint(uuid.uuid4(), ttl=timedelta(seconds=-1))
        )
        is None
    )


def test_a_token_of_the_wrong_type_is_refused():
    """A login/reset JWT is signed with the same key; the `type` claim is what
    stops one being replayed as an order receipt."""
    from app.core.config import settings

    now = datetime.now(timezone.utc)
    not_a_receipt = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert receipt_token.order_id_from(not_a_receipt) is None


def test_a_receipt_with_a_non_uuid_subject_is_refused():
    from app.core.config import settings

    now = datetime.now(timezone.utc)
    bad_sub = jwt.encode(
        {
            "sub": "not-a-uuid",
            "type": "order_receipt",
            "iat": now,
            "exp": now + timedelta(days=1),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert receipt_token.order_id_from(bad_sub) is None
