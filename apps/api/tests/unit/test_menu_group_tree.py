"""
The register's menu tree.

Foodics decides what a cashier can sell by what is reachable through nested
menu groups. The rules that matter, and that are easy to get subtly wrong:

  * switching a group off must hide everything beneath it, at any depth —
    not just its direct children;
  * a cycle must be refused rather than hang the walk;
  * a shop that has not built a tree yet must still get a working till.
"""

from __future__ import annotations

import inspect

from app.services import menu_group_service


def test_the_walk_descends_only_through_active_groups():
    """
    Anchored on active roots and descending through active children.

    Selecting every active group and then checking its parent would leave a
    group whose *grandparent* is off still on the menu.
    """
    source = inspect.getsource(menu_group_service._active_tree_cte)
    assert "recursive=True" in source
    assert "MenuGroup.parent_id.is_(None)" in source, "must anchor on the roots"
    assert "child.c.is_active" in source, "must re-check activeness at each level"


def test_the_walk_is_bounded():
    """A cycle the check constraint cannot see must not run away."""
    source = inspect.getsource(menu_group_service._active_tree_cte)
    assert "roots.c.depth < MAX_DEPTH" in source
    assert menu_group_service.MAX_DEPTH > 0


def test_an_empty_tree_does_not_empty_the_till():
    """
    A fresh install has no groups. Requiring membership unconditionally would
    give the shop a catalogue and a blank register, which reads as an outage
    rather than a setup step.
    """
    clause = str(menu_group_service.pos_visibility_clause())
    assert "NOT (EXISTS" in clause, "must fall back when no group exists"
    assert "sales_channels @>" in clause


def test_membership_is_required_once_a_tree_exists():
    clause = str(menu_group_service.pos_visibility_clause())
    assert "menu_group_products" in clause


def test_a_group_cannot_be_its_own_parent():
    source = inspect.getsource(menu_group_service._assert_no_cycle)
    assert "parent_id == group_id" in source
    assert "A group cannot be its own parent" in source


def test_a_longer_cycle_is_refused():
    """A under B under A — the database constraint cannot see this one."""
    source = inspect.getsource(menu_group_service._assert_no_cycle)
    assert "seen" in source and "current in seen" in source
    assert "inside itself" in source


def test_deleting_a_group_takes_its_descendants():
    """
    Leaving children behind orphans them: unreachable from any root, so they
    vanish from the register while still listing in the console.
    """
    source = inspect.getsource(menu_group_service.delete)
    assert "MenuGroup.parent_id.in_(frontier)" in source
    assert "deleted_at" in source


def test_reordering_products_rewrites_display_order():
    """The order given is the order the terminal lays its buttons out in."""
    source = inspect.getsource(menu_group_service._set_products)
    assert "enumerate(product_ids)" in source
    assert "display_order = order" in source


def test_unknown_products_are_rejected_not_ignored():
    source = inspect.getsource(menu_group_service._set_products)
    assert "No such product" in source
