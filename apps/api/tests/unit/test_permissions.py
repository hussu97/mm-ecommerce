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

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.exceptions import ForbiddenError
from app.core.permissions import assert_no_escalation, ensure, require

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
    dep = require(
        "pos.till.manage", message="You do not have permission to run a check"
    )
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


#: Splits a module into its top-level `def`/`async def` blocks, so a helper can
#: be judged on its own body rather than on the file's.
_TOP_LEVEL_DEF = re.compile(r"^(?:async )?def (\w+)\(", re.MULTILINE)


def _private_permission_helpers(source: str) -> list[str]:
    """
    Private functions that decide a permission and refuse — i.e. copies of
    `_require`.

    Recognised by what the helper *does*, not what it is called: it asks
    `user.can(...)` and raises `ForbiddenError`. That deliberately spares the
    neighbours a name match would catch — `tills._assert_can_touch` (ownership,
    not the permission catalogue) and `staff._validate_permissions` (checks that
    a permission string exists at all).
    """
    matches = list(_TOP_LEVEL_DEF.finditer(source))
    offenders = []
    for index, match in enumerate(matches):
        name = match.group(1)
        if not name.startswith("_"):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        body = source[match.end() : end]
        if ".can(" in body and "ForbiddenError" in body:
            offenders.append(name)
    return offenders


async def test_no_router_keeps_a_private_copy_of_the_permission_check():
    """
    The point of `app.core.permissions` is that there is one definition.

    Five routers held a copy of `_require` when this module was written, and a
    sixth was found afterwards in `marketing.py` — where nothing had ever called
    it, so the file looked gated and was not. A grep is the only thing that
    catches the seventh, so the grep lives here rather than in a reviewer's
    memory: a new private helper fails this test naming the file it is in.
    """
    routers = Path(__file__).resolve().parents[2] / "app" / "api" / "v1"
    offenders = {
        path.name: helpers
        for path in sorted(routers.glob("*.py"))
        if (helpers := _private_permission_helpers(path.read_text()))
    }
    assert offenders == {}, (
        f"{offenders} define their own permission helper. Import `require`/"
        f"`ensure` from app.core.permissions instead."
    )


# ─── The catalogue cannot drift back into a wish list ──────────────────────────


def _enforced_slugs() -> set[str]:
    """Every permission any `require(...)`, `ensure(...)` or `.can(...)` names.

    Source text rather than imports, because the point is to catch a slug that
    no code path mentions at all — an import graph would only ever show the
    ones that are already wired up.
    """
    from app.models.role import ALL_PERMISSIONS

    app_dir = Path(__file__).resolve().parents[2] / "app"
    source = "".join(
        path.read_text() for path in app_dir.rglob("*.py") if path.name != "role.py"
    )
    return {slug for slug in ALL_PERMISSIONS if f'"{slug}"' in source}


async def test_every_permission_is_enforced_somewhere():
    """
    A slug nobody checks is a checkbox that lies to whoever ticks it.

    The catalogue was the Foodics authority matrix copied whole: 108 slugs, 61
    of which no route in this codebase ever asked for. Somebody could build a
    role called "Stock controller", tick `inventory.spot_check.submit`, hand it
    out, and the holder would have exactly the access they had before. Worse in
    the other direction — all 21 `admin.*` slugs were decorative while the
    console ran on the `is_admin` boolean, so a role that looked restricted was
    either everything or nothing.

    So: a permission earns its place by being enforced. If this fails because
    you added a slug, wire it to the route that needs it in the same commit; if
    it fails because you deleted a route, delete the slug and give it a
    `PERMISSION_MIGRATION` entry.
    """
    from app.models.role import ALL_PERMISSIONS

    unenforced = sorted(set(ALL_PERMISSIONS) - _enforced_slugs())
    assert unenforced == [], (
        f"{len(unenforced)} permission(s) are in the catalogue but enforced "
        f"nowhere in app/: {unenforced}"
    )


async def test_the_migration_map_accounts_for_every_retired_slug():
    """
    No live grant may be dropped by a rename.

    `PERMISSION_MIGRATION` is what `105_consolidate_role_perms` rewrites
    `roles.permissions` through. A retired slug missing from it is a permission
    a role still holds that the API will reject as unknown the next time
    somebody opens the role editor.
    """
    from app.models.role import ALL_PERMISSIONS, PERMISSION_MIGRATION

    live = set(ALL_PERMISSIONS)

    # Nothing retired is also live, and nothing live is also retired.
    both = sorted(live & set(PERMISSION_MIGRATION))
    assert both == [], f"{both} are both live and marked retired"

    # Every target is a slug that exists today.
    unknown = sorted({t for t in PERMISSION_MIGRATION.values() if t is not None} - live)
    assert unknown == [], (
        f"the migration map points at slugs that do not exist: {unknown}"
    )


async def test_the_migration_map_matches_the_one_in_the_migration():
    """
    The migration deliberately holds its own copy — it has to keep describing
    the database as it was on the day it ran, not follow today's catalogue. A
    copy that silently diverges is worse than either, so they are compared here
    rather than trusted to stay in step.
    """
    import importlib.util

    from app.models.role import PERMISSION_MIGRATION

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "105_consolidate_role_permissions.py"
    )
    spec = importlib.util.spec_from_file_location("_mig105", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.FORWARD == PERMISSION_MIGRATION


# ─── Delegating the role editor without handing over the keys ──────────────────


def _actor(*permissions: str, is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_admin=is_admin,
        can=lambda permission: is_admin or permission in permissions,
    )


async def test_a_non_admin_may_compose_a_role_out_of_what_they_hold():
    """Downwards delegation is the point: a shift manager who can run the
    register should be able to make a role that runs the register."""
    assert_no_escalation(
        _actor("admin.users.manage", "pos.register.access", "pos.payment.perform"),
        permissions=["pos.register.access", "pos.payment.perform"],
    )


async def test_a_non_admin_cannot_grant_beyond_what_they_hold():
    """
    Otherwise `admin.users.manage` is not one permission, it is all of them:
    write a role holding everything, assign it to yourself, and the grant you
    were given becomes the grant you took.
    """
    with pytest.raises(ForbiddenError) as raised:
        assert_no_escalation(
            _actor("admin.users.manage", "pos.register.access"),
            permissions=["pos.register.access", "admin.payments.manage"],
        )
    assert "admin.payments.manage" in raised.value.detail
    # And it names only what was actually beyond them.
    assert "pos.register.access" not in raised.value.detail


async def test_a_non_admin_cannot_mint_a_super_admin_role():
    """`is_super_admin` short-circuits every check there is, so it is the one
    field that turns the role editor into the whole console."""
    with pytest.raises(ForbiddenError):
        assert_no_escalation(_actor("admin.users.manage"), is_super_admin=True)


async def test_an_admin_is_unrestricted():
    """Same reason `User.can()` short-circuits on `is_admin`: that is the owner
    account, and it is what hands out `admin.users.manage` in the first place."""
    assert_no_escalation(
        _actor(is_admin=True),
        permissions=["admin.payments.manage", "admin.data.manage"],
        is_super_admin=True,
    )
