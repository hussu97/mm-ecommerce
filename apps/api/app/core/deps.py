from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionFactory
from app.core.security import decode_token
from app.models import User
from app.models.branch import Branch

logger = logging.getLogger(__name__)

__all__ = [
    "get_admin_user",
    "get_current_active_user",
    "get_current_user",
    "get_db",
    "get_db_lazy",
    "get_optional_user",
    "oauth2_scheme",
]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


class _LazyDBSession:
    """An `AsyncSession` stand-in that defers checking a connection out of the
    pool until it is actually used (WP5, F-OPS-3).

    The cached public routes (translations, redirects, featured products…) answer
    most requests from Redis and never touch the database. With `Depends(get_db)`
    they still open a session — and hold one of the thirteen request-pool
    connections — on every one of those cache hits, which is exactly the
    fan-out that saturates the pool under a storefront burst. Injected instead of
    `get_db`, this opens nothing up front: the first `await db.<method>(...)` (on
    a cache MISS) opens a real session, and from there behaviour is identical to
    `get_db` — commit on success, rollback on error, close at teardown. On a hit
    the handler returns before touching `db`, so no connection is ever taken.

    It proxies only the async `AsyncSession` methods a read path uses
    (`execute`, `scalar`, `scalars`, `get`, `stream`…). It deliberately does NOT
    support sync access (`.add`, `.begin_nested`, attribute reads) before the
    session is open — those are write-path operations, and a write path uses
    `get_db`, whose connection cost is not what this exists to avoid.
    """

    __slots__ = ("_cm", "_session")

    def __init__(self) -> None:
        self._cm: object | None = None
        self._session: AsyncSession | None = None

    async def _ensure(self) -> AsyncSession:
        if self._session is None:
            self._cm = AsyncSessionFactory()
            self._session = await self._cm.__aenter__()
        return self._session

    @property
    def opened(self) -> bool:
        return self._session is not None

    def __getattr__(self, name: str):
        async def _proxy(*args, **kwargs):
            session = await self._ensure()
            return await getattr(session, name)(*args, **kwargs)

        return _proxy

    async def _finish(self, exc: BaseException | None) -> None:
        if self._session is None:
            return
        try:
            if exc is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            assert self._cm is not None
            await self._cm.__aexit__(type(exc) if exc else None, exc, None)


async def get_db_lazy() -> AsyncGenerator[_LazyDBSession, None]:
    """A DB dependency that opens a session only if the handler actually uses it.

    For the cached, read-only public routes only. See `_LazyDBSession`.
    """
    db = _LazyDBSession()
    try:
        yield db
        await db._finish(None)
    except Exception as exc:
        await db._finish(exc)
        raise


async def _get_user_from_token(
    token: str | None,
    db: AsyncSession,
    required: bool = True,
) -> User | None:
    if not token:
        if required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise JWTError("Wrong token type")
        user_id_str: str | None = payload.get("sub")
        if not user_id_str:
            raise JWTError("Missing subject")
        user_id = uuid.UUID(user_id_str)
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user and required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    resolved = request.cookies.get("mm_access_token") or token
    return await _get_user_from_token(resolved, db, required=True)  # type: ignore[return-value]


async def get_current_active_user(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive"
        )
    # A customer's token and a cashier's token are the same format from the same
    # `create_access_token`, and this only ever checked `is_active` — so a
    # storefront customer's JWT satisfied every POS route's authentication and
    # was stopped only by `user.can(...)` returning False for a role-less
    # account. That is one missing permission check away from a customer editing
    # a live check, which is exactly what `add_item` was.
    #
    # Staff-only is true of the register API by definition: the terminal is the
    # only client, and every person holding one is on the payroll.
    if getattr(request.app.state, "is_pos_app", False) and not (
        current_user.is_staff or current_user.is_admin
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required",
        )
    return current_user


async def get_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return current_user


async def get_optional_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    # Lazy: a guest with no token never reaches the `db.execute` below, so an
    # anonymous cached-route request checks out no connection at all (F-OPS-3).
    db: AsyncSession = Depends(get_db_lazy),
) -> User | None:
    """Returns the current user if authenticated, otherwise None (for guest browsing)."""
    resolved = request.cookies.get("mm_access_token") or token
    return await _get_user_from_token(resolved, db, required=False)


async def browsing_branch(
    branch_id: uuid.UUID | None = Query(
        None,
        description=(
            "The kitchen the shopper's pin resolves to. Given one, the "
            "storefront answers for what that branch can make; omitted, for "
            "what any branch can. Read `branch_id` off GET /delivery/area."
        ),
    ),
    # Lazy: with no `branch_id` the body returns before the `db.get` below, so a
    # request that names no branch checks out no connection here (F-OPS-3).
    db: AsyncSession = Depends(get_db_lazy),
) -> uuid.UUID | None:
    """
    The branch this shopper is browsing as, or None to answer for the estate.

    Validated rather than trusted. The id arrives from a cookie the browser
    wrote and can be stale — a branch closed since the tab was opened, or a
    hand-edited value — and a catalogue filtered on a branch that no longer
    takes orders would quietly go empty. None is the widest honest answer:
    everything some branch can still make.

    A dependency rather than a helper because every storefront read needs the
    same three lines, and the one that grows its own copy is the one that ends
    up trusting the parameter.
    """
    if branch_id is None:
        return None
    branch = await db.get(Branch, branch_id)
    if branch is None or branch.deleted_at is not None or not branch.is_active:
        logger.info("Ignoring browsing branch %s, which cannot take orders", branch_id)
        return None
    return branch.id
