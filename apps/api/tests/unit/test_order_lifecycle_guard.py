"""
No code outside `order_lifecycle` may assign `Order.status`.

The state machine was fragmented once already: thirteen write sites behind five
independent guard sets, which drifted until a courier and the console disagreed
about whether `undelivered` was terminal — and the automatic refund fired on
only one of the paths. `order_lifecycle.transition()` is the repair, and this
test is what keeps it repaired: a new direct assignment fails CI here, before
it ships as the sixth rulebook.

The check is an AST walk rather than a grep so that comments and strings
cannot trip it, and it is deliberately narrow: it flags an assignment to an
attribute named `status` only when the value clearly speaks the order-status
vocabulary — an `OrderStatusEnum` member, or one of its literal values. Other
tables' status columns (`TransferOrderStatusEnum`, item statuses, kitchen
tickets) speak other vocabularies and pass untouched. A writer sneaking a
status through a variable would slip past this test but not past the runtime
warning listener in `order_lifecycle` — the two cover for each other.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.models.order import OrderStatusEnum

APP_DIR = Path(__file__).resolve().parents[2] / "app"

#: The one module allowed to assign the column.
ALLOWED = {APP_DIR / "services" / "order_lifecycle.py"}

ORDER_STATUS_WORDS = {member.value for member in OrderStatusEnum}


def _speaks_order_status(node: ast.expr) -> bool:
    """Whether an assigned value is unmistakably an order status."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            if sub.value.id == "OrderStatusEnum":
                return True
        if isinstance(sub, ast.Constant) and sub.value in ORDER_STATUS_WORDS:
            return True
    return False


def _offending_assignments(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "status"
                and _speaks_order_status(node.value)
            ):
                hits.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")
    return hits


def test_order_status_is_only_assigned_in_order_lifecycle():
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path in ALLOWED:
            continue
        offenders.extend(_offending_assignments(path))

    assert not offenders, (
        "Order.status assigned outside order_lifecycle.transition(). Route "
        "these through the lifecycle so the transition is validated and its "
        "consequences (refund, restock, register void, publish) fire:\n  "
        + "\n  ".join(offenders)
    )


def test_transition_map_still_reachable_from_order_service():
    # The map moved to `order_lifecycle`; the re-export keeps every caller and
    # test that has always found it on `order_service` working. If this import
    # breaks, so did they.
    from app.services.order_lifecycle import VALID_TRANSITIONS as canonical
    from app.services.order_service import VALID_TRANSITIONS as re_exported

    assert re_exported is canonical
