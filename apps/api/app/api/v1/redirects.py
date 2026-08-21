from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete_pattern, cache_get, cache_set
from app.core.deps import get_db
from app.core.permissions import require
from app.models.user import User
from app.schemas.redirect import (
    RedirectCreate,
    RedirectHit,
    RedirectMap,
    RedirectResponse,
    RedirectRule,
    RedirectUpdate,
)
from app.services import audit_service, redirect_service

router = APIRouter()

_MAP_CACHE_KEY = "redirects:map"
_MAP_CACHE_TTL = 300


async def _invalidate() -> None:
    await cache_delete_pattern(_MAP_CACHE_KEY + "*")


# ── Public ───────────────────────────────────────────────────────────────────


@router.get("/map", response_model=RedirectMap)
async def redirect_map(response: Response, db: AsyncSession = Depends(get_db)):
    """
    Every live rule, for the storefront's middleware to hold in memory.

    Unauthenticated on purpose: it is a list of URLs that have moved, which is
    the least secret thing the shop owns, and the caller is `proxy.ts` running
    before any cookie has been read. Ordered longest-path-first by the service
    so the middleware can take the first match.

    Cached twice over. Redis spares the database a query per cold edge instance,
    and `Cache-Control` lets the CDN answer most of them without reaching us at
    all — five minutes being the longest anyone should wait for a redirect they
    just added in the console to start firing.
    """
    response.headers["Cache-Control"] = f"public, max-age={_MAP_CACHE_TTL}"

    cached = await cache_get(_MAP_CACHE_KEY)
    if cached is not None:
        return RedirectMap(rules=[RedirectRule(**r) for r in cached])

    rules = await redirect_service.active_rules(db)
    await cache_set(_MAP_CACHE_KEY, [r.model_dump() for r in rules], ttl=_MAP_CACHE_TTL)
    return RedirectMap(rules=rules)


@router.post("/hit", status_code=status.HTTP_204_NO_CONTENT)
async def record_hit(data: RedirectHit):
    """
    Report that a rule fired. Best effort, and deliberately toothless.

    The storefront calls this from `waitUntil` *after* the redirect has gone to
    the visitor, so nothing is waiting on the answer and there is nothing left to
    break. Unauthenticated for the same reason `/map` is — and the worst a bad
    actor achieves is an inflated count on a URL that has already moved.

    No `get_db`: the write runs on its own session inside the service, because a
    counter must not join, or roll back with, anything else. Convention 2.
    """
    await redirect_service.record_hit(data.from_path)


# ── Console ──────────────────────────────────────────────────────────────────


@router.get("", response_model=list[RedirectResponse])
async def list_redirects(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("content.manage")),
):
    """Every rule, live or retired, newest first."""
    return await redirect_service.list_all(db)


@router.post("", response_model=RedirectResponse, status_code=status.HTTP_201_CREATED)
async def create_redirect(
    request: Request,
    data: RedirectCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("content.manage")),
):
    result = await redirect_service.create(db, data)
    await _invalidate()
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="redirect",
        entity_id=str(result.id),
        entity_label=f"{result.from_path} → {result.to_path}",
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return result


@router.put("/{redirect_id}", response_model=RedirectResponse)
async def update_redirect(
    request: Request,
    redirect_id: UUID,
    data: RedirectUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("content.manage")),
):
    result = await redirect_service.update_one(db, redirect_id, data)
    await _invalidate()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="redirect",
        entity_id=str(redirect_id),
        entity_label=f"{result.from_path} → {result.to_path}",
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return result


@router.delete("/{redirect_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_redirect(
    request: Request,
    redirect_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("content.manage")),
):
    await redirect_service.delete(db, redirect_id)
    await _invalidate()
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="redirect",
        entity_id=str(redirect_id),
        entity_label=str(redirect_id),
        admin=admin,
        changes={"deleted_id": str(redirect_id)},
        request=request,
    )
