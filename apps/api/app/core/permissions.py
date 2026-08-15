"""
The one home for staff permission checks.

The `_require(user, permission)` helper used to be copy-pasted into five
routers, which made every check an imperative first statement somebody had to
remember to write. One forgotten line already shipped as a hole: `add_item`
gated only its open-price case, so any authenticated active user could add
priced lines to any check whose id they could name (see the comment on
`pos_orders.add_item`). A check that lives in the route decorator's signature
cannot be forgotten the same way — the route either declares what it demands
or visibly declares nothing, and `grep 'require("'` audits the whole surface.

Two forms, one rule:

* `require(permission)` — a dependency factory for the normal case, where the
  permission is known when the route is declared. Use it as the parameter that
  used to be `Depends(get_current_active_user)`; it resolves the same user,
  refuses the same way, and returns the user for handlers that need it.
* `ensure(user, permission)` — the imperative form, for the handful of sites
  where the permission depends on the request body (open vs predefined
  discounts, refund vs payment) and a static dependency cannot express it.
  Each such site carries a comment saying why it stays inline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends

from app.core.deps import get_current_active_user
from app.core.exceptions import ForbiddenError
from app.models.user import User

__all__ = ["ensure", "require"]


def ensure(user: User, permission: str, *, message: str | None = None) -> None:
    """Refuse *user* unless they hold *permission*.

    The message defaults to naming the permission, which is what every router's
    copy of `_require` said; *message* exists for the one caller (the till
    spot check) whose wording predates this module and must not change under a
    refactor that promises identical behaviour.
    """
    if not user.can(permission):
        raise ForbiddenError(message or f"You do not have permission to {permission}")


def require(
    permission: str, *, message: str | None = None
) -> Callable[..., Awaitable[User]]:
    """A FastAPI dependency that admits only holders of *permission*.

    Returns the resolved user, so it slots in wherever
    `Depends(get_current_active_user)` stood — the handler keeps its `user`
    when it reads one, and names the parameter `_` when it does not.
    """

    async def _check(user: User = Depends(get_current_active_user)) -> User:
        ensure(user, permission, message=message)
        return user

    # Stamped on the closure so a test can assert which permission a route
    # demands without spelling the check out in source-string matching.
    _check.permission = permission  # type: ignore[attr-defined]
    return _check
