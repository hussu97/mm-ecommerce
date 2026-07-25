from __future__ import annotations

from app.models.role import (
    ALL_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    PERMISSION_GROUPS,
    Role,
)


def test_permission_slugs_are_unique():
    """A duplicate slug would make the role editor toggle two checkboxes at once."""
    assert len(ALL_PERMISSIONS) == len(set(ALL_PERMISSIONS))


def test_every_permission_has_a_description():
    assert set(PERMISSION_DESCRIPTIONS) == set(ALL_PERMISSIONS)
    assert all(desc.strip() for desc in PERMISSION_DESCRIPTIONS.values())


def test_slugs_are_namespaced_and_lowercase():
    for slug in ALL_PERMISSIONS:
        assert slug == slug.lower(), slug
        assert "." in slug, f"{slug} is missing a namespace prefix"
        assert " " not in slug, slug


def test_catalogue_covers_the_expected_domains():
    assert set(PERMISSION_GROUPS) == {
        "Orders",
        "Customers",
        "Inventory",
        "Menu",
        "Administration",
        "Reports",
        "Dashboard",
        "Cashier app",
    }


def test_role_grants_only_what_it_holds():
    role = Role()
    role.permissions = ["orders.read", "pos.register.access"]
    role.is_super_admin = False

    assert role.has("orders.read")
    assert role.has("pos.register.access")
    assert not role.has("orders.manage")
    assert not role.has("admin.settings.manage")


def test_super_admin_short_circuits_every_check():
    role = Role()
    role.permissions = []
    role.is_super_admin = True

    assert all(role.has(slug) for slug in ALL_PERMISSIONS)
    # Even a slug that does not exist yet — the owner is never locked out.
    assert role.has("some.future.permission")


def test_role_with_no_permissions_grants_nothing():
    role = Role()
    role.permissions = []
    role.is_super_admin = False
    assert not any(role.has(slug) for slug in ALL_PERMISSIONS)
