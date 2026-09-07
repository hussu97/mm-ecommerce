"""
Where a request actually came from, behind the one nginx proxy.

Every request reaches the app through the single nginx container, so
`request.client.host` is nginx's address on the bridge network — `172.18.0.x`
on every request — which is useless as a rate-limit bucket (one global bucket
for the whole internet) and as an audit-trail IP (every row says "nginx").

nginx passes the real peer in `X-Forwarded-For` (`$proxy_add_x_forwarded_for`,
set at the http level in `nginx/nginx.conf`). That header is **append-only**:
nginx adds the address it actually saw to whatever the sender already put there,
so a client that opens with `X-Forwarded-For: 1.2.3.4` produces
`1.2.3.4, <its real address>` and only the **rightmost** hop is the one nginx
observed. Taking the leftmost would let any caller choose the value — pick the
rate-limit bucket it lands in, or the IP written to the audit log — which is
exactly the abuse both of those exist to resist.

There is exactly one hop to strip because the API containers publish no ports:
nginx is the only way in and the domain's A record points at the VM, not at a
further proxy. This mirrors the reasoning already written out in
`webhook_log_service._client_ip`; this is the shared helper the rate limiter,
the audit trail and the PIN/pairing keys all key on so the rule lives in one
place.
"""

from __future__ import annotations

from typing import Any

__all__ = ["client_ip"]


def client_ip(request: Any) -> str | None:
    """The caller's real address (rightmost `X-Forwarded-For` hop), or None.

    Falls back to `request.client.host` when no forwarded header is present —
    the shape a direct, un-proxied request (a test, a local run) takes.
    """
    headers = getattr(request, "headers", None)
    forwarded = headers.get("X-Forwarded-For") if headers is not None else None
    if forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    client = getattr(request, "client", None)
    return client.host if client else None
