"""
The rate limiter and audit trail must key on the caller's real IP.

Behind the single nginx proxy `request.client.host` is nginx's own address, so
keying on it put the entire internet in one bucket: eleven bad PIN attempts from
anywhere locked every till, and `/auth/login` was 5/min for the whole world.
`client_ip` reads the address nginx actually saw (the rightmost `X-Forwarded-For`
hop) and nothing the caller can set for itself. These pin that.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.api.v1.staff import _pin_login_rate_key
from app.core.limiter import rate_limit_key
from app.core.request_ip import client_ip


def _request(*, xff: str | None = None, peer: str | None = "172.18.0.5", body=None):
    headers = {}
    if xff is not None:
        headers["X-Forwarded-For"] = xff
    client = SimpleNamespace(host=peer) if peer is not None else None
    req = SimpleNamespace(headers=headers, client=client)
    if body is not None:
        req._body = body
    return req


class TestClientIp:
    def test_takes_the_rightmost_forwarded_hop(self):
        # nginx appends the peer it saw, so the rightmost hop is the real client;
        # the leftmost is whatever the caller chose to send.
        req = _request(xff="1.2.3.4, 203.0.113.9")
        assert client_ip(req) == "203.0.113.9"

    def test_a_single_hop_is_the_client(self):
        assert client_ip(_request(xff="203.0.113.9")) == "203.0.113.9"

    def test_a_spoofed_leftmost_value_is_ignored(self):
        # A caller opening the header with a value of its own cannot choose the
        # bucket: nginx's appended peer still wins on the right.
        req = _request(xff="8.8.8.8", peer=None)
        # With one hop and no peer, the one hop is what nginx saw.
        assert client_ip(req) == "8.8.8.8"
        req2 = _request(xff="8.8.8.8, 203.0.113.9")
        assert client_ip(req2) == "203.0.113.9"

    def test_falls_back_to_the_peer_without_a_forwarded_header(self):
        assert client_ip(_request(xff=None, peer="198.51.100.2")) == "198.51.100.2"

    def test_none_when_nothing_is_known(self):
        assert client_ip(_request(xff=None, peer=None)) is None

    def test_ignores_blank_hops(self):
        assert client_ip(_request(xff="1.2.3.4, , 203.0.113.9")) == "203.0.113.9"


class TestLimiterKey:
    def test_key_is_the_real_ip(self):
        assert rate_limit_key(_request(xff="1.2.3.4, 203.0.113.9")) == "203.0.113.9"

    def test_key_is_never_none(self):
        # slowapi needs a string bucket even when the IP is unknowable.
        assert rate_limit_key(_request(xff=None, peer=None)) == "unknown"


class TestPinLoginKey:
    def test_key_pairs_branch_and_ip(self):
        body = json.dumps({"branch_id": "b-123", "pin": "0000"}).encode()
        req = _request(xff="1.2.3.4, 203.0.113.9", body=body)
        assert _pin_login_rate_key(req) == "203.0.113.9:b-123"

    def test_different_branches_at_one_ip_get_different_buckets(self):
        a = _request(xff="203.0.113.9", body=json.dumps({"branch_id": "a"}).encode())
        b = _request(xff="203.0.113.9", body=json.dumps({"branch_id": "b"}).encode())
        assert _pin_login_rate_key(a) != _pin_login_rate_key(b)

    def test_falls_back_to_ip_only_without_a_body(self):
        assert _pin_login_rate_key(_request(xff="203.0.113.9")) == "203.0.113.9:"
