"""
Every route declares an authenticator, or is on an explicit PUBLIC allow-list.

The highest-value guard in this package. A permission check lives in a route's
dependency signature, so a route that simply omits `Depends(...)` authenticates
nobody — and nothing failed when that happened. `GET /modifiers` and
`/modifiers/{id}` shipped with no auth at all this way. This walks both apps'
route trees, and for every route asserts that some authenticator appears in its
dependency tree unless the route is named, deliberately, as public.

A route is "authenticated" if its dependant tree contains one of the known
authenticator dependencies, or any `require(...)` / `require_any(...)` closure
(detected by the `.permission` stamp those carry). New auth helpers must be added
to `ACCEPTED` below; new genuinely-public routes to `PUBLIC`. The allow-list is
keyed on `(method, path)` and is kept tight — adding to it is the deliberate act
the guard exists to force.

Note on the walk: FastAPI 0.141 no longer flattens `include_router` into
`app.routes`; it keeps a lazy `_IncludedRouter` wrapper whose real routes live on
`.original_router.routes`. `_leaf_routes` descends through those, so the walk sees
every `APIRoute` and its resolved `.dependant`. Leaf `APIRoute.path` is the
router-relative path (no `/api/v1` prefix), which is what `PUBLIC` is keyed on.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.api.v1.aggregators import _require_push_token
from app.api.v1.devices import authenticate_device, get_current_device
from app.core.deps import (
    get_admin_user,
    get_current_active_user,
    get_current_staff_user,
    get_current_user,
    get_optional_user,
)
from app.main import app as web_app
from app.pos_main import app as pos_app

#: The dependencies that count as authenticating a request. `get_optional_user`
#: is included because it is the deliberate marker for a guest-browsable route
#: (the storefront resolves the user if a token is present); routes that want to
#: be truly public without even that must be named in PUBLIC below.
ACCEPTED = {
    get_current_user,
    get_current_active_user,
    get_current_staff_user,
    get_admin_user,
    get_optional_user,
    get_current_device,
    authenticate_device,
    _require_push_token,
}

#: Never-authenticated by design. Kept tight; each entry is public for a reason.
PUBLIC: set[tuple[str, str]] = {
    # ── System probes ────────────────────────────────────────────────────────
    ("GET", "/ping"),
    ("GET", "/health"),
    ("GET", "/health/integrations"),
    # ── Auth: you cannot hold a token before you have one ────────────────────
    ("POST", "/register"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("POST", "/refresh"),
    ("POST", "/guest"),
    ("POST", "/reset-password"),
    ("GET", "/phone-verified"),
    ("POST", "/admin/login-options"),
    ("POST", "/admin/passkeys/login/options"),
    ("POST", "/admin/passkeys/login/verify"),
    # ── Terminal onboarding / cashier sign-in (credential is in the body) ────
    ("POST", "/pair"),
    ("POST", "/pin-login"),
    # ── Storefront browse & checkout furniture ───────────────────────────────
    ("GET", "/featured"),
    ("GET", "/cart-addons"),
    ("GET", "/pickup-points"),
    ("GET", "/apple-pay/eligibility"),
    ("GET", "/availability"),  # custom-orders public availability calendar
    # ── Public delivery quoting ──────────────────────────────────────────────
    ("GET", "/rates"),
    ("POST", "/calculate"),
    ("GET", "/area"),
    # ── Public content ───────────────────────────────────────────────────────
    ("GET", "/languages"),
    ("GET", "/translations/{locale}"),
    ("GET", "/public"),
    ("GET", "/public/{slug}"),
    ("GET", "/map"),
    ("POST", "/hit"),
    # ── Order self-service lookup (proof-of-ownership is in the request) ──────
    ("POST", "/track"),
    # ── Payment / courier webhooks (HMAC/signature verified in the handler) ──
    ("POST", "/stripe"),
    ("POST", "/ziina"),
    ("POST", "/webhooks/stripe"),
    ("POST", "/webhooks/ziina"),
    ("POST", "/webhooks/{gateway}"),
    ("POST", "/lalamove"),
    ("POST", "/noon-send"),
    ("POST", "/noon-send/tracking"),
    ("POST", "/slider"),
}


def _leaf_routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif type(route).__name__ == "_IncludedRouter":
            yield from _leaf_routes(route.original_router.routes)
        else:
            sub = getattr(route, "routes", None)
            if sub:
                yield from _leaf_routes(sub)


def _dependants(dependant):
    yield dependant
    for sub in dependant.dependencies:
        yield from _dependants(sub)


def _is_authenticated(route: APIRoute) -> bool:
    for dep in _dependants(route.dependant):
        call = dep.call
        if call in ACCEPTED:
            return True
        # `require(...)` / `require_any(...)` stamp the permission on the closure.
        if getattr(call, "permission", None) is not None:
            return True
    return False


def _unguarded(app) -> list[tuple[str, str, str]]:
    seen: set[int] = set()
    out: list[tuple[str, str, str]] = []
    for route in _leaf_routes(app.routes):
        if id(route) in seen:
            continue
        seen.add(id(route))
        if _is_authenticated(route):
            continue
        for method in sorted(route.methods or []):
            if method in ("HEAD", "OPTIONS"):
                continue
            if (method, route.path) in PUBLIC:
                continue
            endpoint = getattr(route.endpoint, "__qualname__", str(route.endpoint))
            out.append((method, route.path, endpoint))
    return out


@pytest.mark.parametrize("app", [web_app, pos_app], ids=["web", "pos"])
def test_every_route_declares_an_authenticator(app):
    offenders = _unguarded(app)
    assert not offenders, (
        "these routes declare no authenticator and are not on the PUBLIC "
        "allow-list — add an auth dependency, or (if truly public) a PUBLIC "
        "entry with a reason:\n  "
        + "\n  ".join(f"{m:6} {p}  → {ep}" for m, p, ep in offenders)
    )


def test_the_walk_actually_reaches_the_mounted_routes():
    """Guard against the guard passing because it saw nothing.

    FastAPI's lazy `_IncludedRouter` means a naive `app.routes` walk sees only a
    handful of app-level routes. If this count collapses, the walk has stopped
    descending and every assertion above is vacuously true.
    """
    assert len(list(_leaf_routes(web_app.routes))) > 200
    assert len(list(_leaf_routes(pos_app.routes))) > 80


def test_the_public_allow_list_has_no_stale_entries():
    """Every PUBLIC entry must correspond to a real unauthenticated route.

    A wildcard that no longer matches anything is a hole waiting for a future
    route to fall into silently.
    """
    live: set[tuple[str, str]] = set()
    for app in (web_app, pos_app):
        for route in _leaf_routes(app.routes):
            if _is_authenticated(route):
                continue
            for method in route.methods or []:
                live.add((method, route.path))
    stale = sorted(entry for entry in PUBLIC if entry not in live)
    assert not stale, f"PUBLIC entries that match no unauthenticated route: {stale}"
