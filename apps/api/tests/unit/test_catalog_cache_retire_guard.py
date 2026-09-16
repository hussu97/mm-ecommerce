"""F-INV-17: every catalogue write retires the storefront cache.

The storefront answers the catalogue — categories, products, their "from" prices
and add-on trays — from `catalogue_cache`, keyed per branch. A write that changes
any of that has to retire the cache or the shopper keeps seeing the old answer
until it ages out. Most write routes already do; the ones that did not (modifier
and modifier-option edits, bulk status/visibility, product↔modifier links) were
the F-INV-17 gap.

This is the guard that keeps the gap closed: every non-GET route in the catalog
routers must, in its own body, reach a cache-retiring call — `catalogue_cache.retire`
directly, or one of the routers' `_invalidate*` helpers that wraps it. A new write
route that forgets fails here rather than in production. The allow-list is
shrink-only: a route may be exempted only with a written reason, and never grow.
"""

from __future__ import annotations

import ast
from pathlib import Path

# apps/api/app/api/v1/<router>.py
_V1 = Path(__file__).resolve().parents[2] / "app" / "api" / "v1"

#: The routers that serve the storefront catalogue. Menu groups are deliberately
#: not here — they are an internal grouping/printing concept, not a storefront
#: catalogue surface with a per-branch cache.
_CATALOG_ROUTERS = ("categories", "products", "modifiers", "bulk")

_WRITE_METHODS = {"post", "put", "patch", "delete"}

#: (router, function) pairs allowed not to retire, each with a reason. Empty —
#: every catalog write retires today. May only SHRINK.
_ALLOW_LIST: set[tuple[str, str]] = set()


def _is_write_route(func: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    for dec in func.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in _WRITE_METHODS
        ):
            return True
    return False


def _reaches_a_retire(func: ast.AST) -> bool:
    """True if the function body calls anything that retires the catalogue cache:
    `catalogue_cache.retire(...)` or any `_invalidate*` helper."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (
            callee.attr
            if isinstance(callee, ast.Attribute)
            else callee.id
            if isinstance(callee, ast.Name)
            else ""
        )
        if name == "retire" or "invalidate" in name:
            return True
    return False


def test_every_catalog_write_route_retires_the_cache():
    offenders: list[str] = []
    for router in _CATALOG_ROUTERS:
        tree = ast.parse((_V1 / f"{router}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not _is_write_route(node):
                continue
            if (router, node.name) in _ALLOW_LIST:
                continue
            if not _reaches_a_retire(node):
                offenders.append(f"{router}.py::{node.name}")

    assert not offenders, (
        "These catalog write routes do not retire `catalogue_cache` — a shopper "
        "will keep seeing the pre-edit catalogue until it ages out. Call the "
        "router's `_invalidate_catalogue_caches()` (or `catalogue_cache.retire()`) "
        "after the write, or add the route to _ALLOW_LIST with a reason:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_the_allow_list_only_shrinks():
    # A route named here must still exist and still be a write route; a stale
    # entry means the exemption outlived its route and must be dropped.
    for router, fn in _ALLOW_LIST:
        tree = ast.parse((_V1 / f"{router}.py").read_text(encoding="utf-8"))
        matched = any(
            isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            and n.name == fn
            and _is_write_route(n)
            for n in ast.walk(tree)
        )
        assert matched, f"stale _ALLOW_LIST entry: {router}.py::{fn}"
