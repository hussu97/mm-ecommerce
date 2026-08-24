"""
The analytics surface, which is three unrelated products behind one prefix.

It was one 1,359-line module. The three groups share `_date_range` and nothing
else — different sources, different caching, different permissions:

* `commerce` — our own tables, cached for five minutes.
* `umami` — a proxy to somebody else's API, with its own client and timeouts,
  where being down is a degraded panel rather than a broken dashboard.
* `live_carts` — deliberately uncached and behind `customers.read` rather than
  `reports.sales`, because it names individual people.

Each mounts its own `APIRouter` here, so the routes and their prefix are
unchanged.
"""

from fastapi import APIRouter

from . import commerce, live_carts, umami

router = APIRouter()
router.include_router(commerce.router)
router.include_router(umami.router)
router.include_router(live_carts.router)

__all__ = ["router"]
