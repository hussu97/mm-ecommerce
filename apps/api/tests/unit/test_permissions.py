"""
The `require`/`ensure` pair in `app.core.permissions`.

Permission checks used to be an imperative first statement copy-pasted into
five routers, and one forgotten line shipped as a hole (`pos_orders.add_item`
gated only its open-price case). These tests pin the replacement's contract:
the dependency refuses exactly the way the old helpers did, hands back the
user for handlers that need one, and stays introspectable so a route's
demanded permission can be asserted without string-matching its source.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.exceptions import ForbiddenError
from app.core.permissions import ensure, require

pytestmark = pytest.mark.asyncio


def _user(*permissions: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        can=lambda permission: permission in permissions,
    )


async def test_a_holder_of_the_permission_gets_the_user_back():
    """`Depends(require(...))` stands where `Depends(get_current_active_user)`
    stood, so it must return the user for handlers that read one."""
    user = _user("pos.orders.void")
    assert await require("pos.orders.void")(user) is user


async def test_a_non_holder_is_refused_with_the_helpers_exact_message():
    """The wording is what every router's `_require` copy said — a refactor
    that promises identical behaviour includes identical words."""
    with pytest.raises(ForbiddenError) as raised:
        await require("pos.orders.void")(_user("orders.read"))
    assert raised.value.detail == "You do not have permission to pos.orders.void"
    assert raised.value.status_code == 403


async def test_a_custom_message_survives():
    """The till spot check's wording predates this module and must not change
    under the refactor; `message` exists for it."""
    dep = require("pos.spot_check", message="You do not have permission to run a check")
    with pytest.raises(ForbiddenError) as raised:
        await dep(_user())
    assert raised.value.detail == "You do not have permission to run a check"


async def test_the_dependency_names_its_permission():
    """Stamped on the closure so a test can assert which permission a route
    demands off its signature rather than by grepping source."""
    assert require("reports.sales").permission == "reports.sales"


async def test_ensure_is_the_same_check_in_imperative_form():
    """For the call sites where the permission depends on the request body
    (open vs predefined discounts, refund vs payment) and a static dependency
    cannot express it."""
    ensure(_user("pos.discounts.open"), "pos.discounts.open")  # does not raise
    with pytest.raises(ForbiddenError):
        ensure(_user(), "pos.discounts.open")
