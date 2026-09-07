"""
The payment gateway's return URL carries a signed token, never the email
(F-WEB-2/F-ORD-20).

The `success_url` is handed to Stripe and Ziina, kept in the browser's history,
and read by the analytics beacon. It used to carry `&email=<address>`, so the
customer's email — which is also the ownership credential — leaked into all
three. It now carries `&token=<receipt_token>`, which proves ownership of this
one order, cannot be read back into an email, and expires.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from app.core import receipt_token
from app.services.providers import checkout_urls


def _order():
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260907-0007",
        email="jane@example.com",
    )


def test_success_url_does_not_carry_the_email():
    order = _order()
    url = checkout_urls.success_url(order)
    assert "email=" not in url
    assert "jane@example.com" not in url
    assert order.email not in url


def test_success_url_carries_a_token_that_verifies_to_the_order():
    order = _order()
    url = checkout_urls.success_url(order)
    query = parse_qs(urlparse(url).query)
    assert query["order_number"] == [order.order_number]
    token = query["token"][0]
    assert receipt_token.order_id_from(token) == order.id


def test_the_gateway_placeholder_still_rides_as_session_id():
    order = _order()
    url = checkout_urls.success_url(order, reference_token="{CHECKOUT_SESSION_ID}")
    query = parse_qs(urlparse(url).query)
    assert query["session_id"] == ["{CHECKOUT_SESSION_ID}"]
    # …and the token is still there beside it, still no email.
    assert receipt_token.order_id_from(query["token"][0]) == order.id
    assert "email=" not in url
