"""
The order polls' ETag token, without a database: how a client's
`If-None-Match` is read, and when a token stops being honoured.

The DB-backed behaviour (304s, bumps, scoping) is in
`tests/integration/test_pos_orders_etag.py`, which pr-check cannot run.
"""

from __future__ import annotations

from app.services.pos import pos_orders_etag as etag

NOW = 1_800_000_000.0


def test_a_live_token_for_the_same_key_matches():
    token = etag.make_etag("abc", int(NOW) + 60)
    assert etag.matching_etag(token, "abc", now=NOW) == token


def test_the_weak_prefix_and_quotes_are_optional():
    assert etag.matching_etag(f'"abc.{int(NOW) + 60}"', "abc", now=NOW)
    assert etag.matching_etag(f"abc.{int(NOW) + 60}", "abc", now=NOW)


def test_any_of_several_tokens_may_match():
    held = f'W/"old.{int(NOW) + 60}", W/"abc.{int(NOW) + 60}"'
    assert etag.matching_etag(held, "abc", now=NOW) == f'W/"abc.{int(NOW) + 60}"'


def test_another_key_or_an_expired_token_does_not():
    assert (
        etag.matching_etag(etag.make_etag("abd", int(NOW) + 60), "abc", now=NOW) is None
    )
    assert etag.matching_etag(etag.make_etag("abc", int(NOW)), "abc", now=NOW) is None


def test_nothing_or_nonsense_does_not():
    assert etag.matching_etag(None, "abc", now=NOW) is None
    assert etag.matching_etag("", "abc", now=NOW) is None
    assert etag.matching_etag("*", "abc", now=NOW) is None
    assert etag.matching_etag('W/"abc.soon"', "abc", now=NOW) is None


def test_the_opt_in_header_is_exact():
    assert etag.wants_etag({"X-POS-ETag": "1"})
    assert not etag.wants_etag({})
    assert not etag.wants_etag({"X-POS-ETag": "true"})


def test_without_any_time_relative_field_a_token_lasts_the_ceiling():
    assert etag.valid_until([], now=NOW) == int(NOW) + etag.TTL_CEILING
