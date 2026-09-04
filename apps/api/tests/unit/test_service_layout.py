"""
`app/services/` stays grouped, and the barrel stays empty.

It was 74 flat modules. Grouping them was the easy half; the half that decays
is keeping it that way, because adding one more file to the root is always the
smaller change in the moment.

Two rules, both cheap to check and both easy to break by accident:

* The barrel exports nothing. It used to re-export nineteen of the seventy-four
  with no stated criterion, so `from app.services import order_service` and
  `from app.services.orders import order_service` were both correct and neither
  was canonical. One idiom, and the import line says which part of the system a
  file reaches into.
* The root does not grow. New modules belong in a domain; the list below is the
  set that genuinely belongs to none, and adding to it should be a decision
  somebody makes on purpose rather than a default.
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[2] / "app" / "services"

#: The domains. Each has a docstring saying what belongs in it.
SUBPACKAGES = {
    "catalog",
    "couriers",
    "delivery",
    "grubops",
    "inventory",
    "orders",
    "payments",
    "pos",
    "providers",
}

#: Modules that belong to no single domain: audit, transport, caching, auth,
#: and the generic helpers. Not a parking space — if a new module fits a
#: domain, it goes in the domain.
CROSS_CUTTING = {
    "address_service",
    "audit_service",
    "blog_service",
    "branch_holiday_service",
    "cart_service",
    "cms_service",
    "crud_service",
    "custom_order_service",
    "email_copy",
    "email_service",
    "firebase_auth_service",
    "i18n_service",
    "image_warm_service",
    "indexnow_service",
    "log_retention",
    "option_snapshot",
    "promo_code_service",
    "push_service",
    "redirect_service",
    "reference_integrity",
    "turnstile_service",
    "webhook_log_service",
}


def test_the_barrel_exports_nothing():
    """
    Read the source, not the imported module.

    `vars(app.services)` also lists every submodule anything has imported —
    Python binds those on the package as a side effect — so inspecting the
    module object reports `email_service` as "exported" the moment some router
    imports it. The question here is what `__init__.py` itself does.
    """
    source = (SERVICES / "__init__.py").read_text()
    tree = ast.parse(source)
    statements = [
        ast.unparse(node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign))
    ]

    assert not statements, (
        "app/services/__init__.py is re-exporting again, which gives every "
        "module it names two correct import paths and no canonical one:\n  "
        + "\n  ".join(statements)
    )


def test_every_domain_package_says_what_belongs_in_it():
    undocumented = sorted(
        name
        for name in SUBPACKAGES
        if not (SERVICES / name / "__init__.py").read_text().strip().startswith('"""')
    )

    assert not undocumented, (
        "these subpackages have no docstring, so nothing tells the next person "
        f"what belongs in them:\n  {chr(10).join(undocumented)}"
    )


def test_no_new_module_lands_at_the_root():
    at_root = {p.stem for p in SERVICES.glob("*.py") if p.stem != "__init__"}
    strays = sorted(at_root - CROSS_CUTTING)

    assert not strays, (
        "these sit at app/services/ root rather than in a domain. If one of "
        "them genuinely belongs to no domain, add it to CROSS_CUTTING here and "
        f"say why in the review:\n  {chr(10).join(strays)}"
    )


def test_the_list_here_has_not_gone_stale():
    """A name in `CROSS_CUTTING` that no longer exists hides a real stray."""
    at_root = {p.stem for p in SERVICES.glob("*.py") if p.stem != "__init__"}
    gone = sorted(CROSS_CUTTING - at_root)

    assert not gone, (
        "these are listed as cross-cutting but no longer exist at the root — "
        f"remove them, or the list stops catching anything:\n  {chr(10).join(gone)}"
    )
