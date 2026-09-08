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
import re
from pathlib import Path

import pytest

from app.models.order import OrderStatusEnum

APP_DIR = Path(__file__).resolve().parents[2] / "app"

#: A raw `text("UPDATE orders … status …")` write to the orders table's status.
#: Scoped to the `orders` table by name so another table's raw UPDATE is ignored.
_RAW_ORDER_STATUS_UPDATE = re.compile(
    r"update\s+orders\b.*?\bstatus\b", re.IGNORECASE | re.DOTALL
)

#: The one module allowed to assign the column.
#:
#: Spelled out rather than globbed, and worth checking after any move: a path
#: that no longer exists exempts nothing, and this test would then flag the one
#: file it exists to protect. It survived `order_lifecycle` moving into
#: `services/orders/` only because line 324 assigns a variable, which
#: `_speaks_order_status` deliberately cannot see.
ALLOWED = {APP_DIR / "services" / "orders" / "order_lifecycle.py"}

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


def _status_attr_targets(target: ast.expr) -> list[ast.Attribute]:
    """The `x.status` attribute targets in an assignment target, descending into
    tuple/list unpacking so `order.status, other = new, y` is seen the same as a
    plain assignment. A list (not a generator) so emptiness is truthy-checkable."""
    if isinstance(target, (ast.Tuple, ast.List)):
        found: list[ast.Attribute] = []
        for elt in target.elts:
            found.extend(_status_attr_targets(elt))
        return found
    if isinstance(target, ast.Attribute) and target.attr == "status":
        return [target]
    return []


def _is_update_of_orders(node: ast.expr) -> bool:
    """Whether an expression is (or is built on) `update(Order)…` — so a
    `.values(status=…)` on it writes the orders table, not another one."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "update"
            and sub.args
            and isinstance(sub.args[0], ast.Name)
            and sub.args[0].id == "Order"
        ):
            return True
    return False


def _line_hits(tree: ast.AST) -> list[int]:
    """Every line that writes `Order.status` outside the lifecycle — in any of the
    forms a plain-`Assign` walk missed (F-TST-8): a `setattr`, an annotated
    assignment, a tuple-unpacking target, a SQLAlchemy `update(Order).values`, and
    a raw `text("UPDATE orders … status …")`."""
    hits: list[int] = []
    for node in ast.walk(tree):
        # `order.status = …`  and  `order.status, x = …, y`
        if isinstance(node, ast.Assign):
            if _speaks_order_status(node.value) and any(
                _status_attr_targets(t) for t in node.targets
            ):
                hits.append(node.lineno)
        # `order.status: T = …`
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Attribute)
                and node.target.attr == "status"
                and node.value is not None
                and _speaks_order_status(node.value)
            ):
                hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            # `setattr(order, "status", …)`
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 3
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "status"
                and _speaks_order_status(node.args[2])
            ):
                hits.append(node.lineno)
            # `update(Order).…values(status=…)`
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "values"
                and _is_update_of_orders(node.func.value)
                and any(
                    kw.arg == "status" and _speaks_order_status(kw.value)
                    for kw in node.keywords
                )
            ):
                hits.append(node.lineno)
            # `text("UPDATE orders … status …")`
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id == "text"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and _RAW_ORDER_STATUS_UPDATE.search(node.args[0].value)
            ):
                hits.append(node.lineno)
    return sorted(set(hits))


def _offending_assignments(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        f"{path.relative_to(APP_DIR.parent)}:{lineno}" for lineno in _line_hits(tree)
    ]


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


# ── the detector must actually catch each side-door form (F-TST-8) ────────────


def _flag(src: str) -> list[int]:
    return _line_hits(ast.parse(src))


_A_WORD = next(
    iter(ORDER_STATUS_WORDS)
)  # a real order-status literal, e.g. "delivered"

_CAUGHT = {
    "plain assign": "order.status = OrderStatusEnum.DELIVERED",
    "literal assign": f"order.status = {_A_WORD!r}",
    "setattr": 'setattr(order, "status", OrderStatusEnum.DELIVERED)',
    "annotated assign": "order.status: str = OrderStatusEnum.DELIVERED",
    "tuple target": "order.status, x = OrderStatusEnum.DELIVERED, 1",
    "bulk update": "update(Order).where(Order.id == i).values(status=OrderStatusEnum.DELIVERED)",
    "bulk update literal": f"update(Order).values(status={_A_WORD!r})",
    "raw sql": "text(\"UPDATE orders SET status = 'delivered' WHERE id = :id\")",
}

_IGNORED = {
    # Another table's status column speaks another vocabulary.
    "other enum assign": "ticket.status = KitchenTicketStatusEnum.FIRED",
    "other table update": f"update(KitchenTicket).values(status={_A_WORD!r})",
    "other table raw sql": "text(\"UPDATE kitchen_tickets SET status = 'fired'\")",
    # A variable is invisible to an AST — the runtime listener covers it.
    "via a variable": "order.status = new_status",
    # Reading the status is fine.
    "a read": "if order.status == OrderStatusEnum.DELIVERED: pass",
}


@pytest.mark.parametrize("src", _CAUGHT.values(), ids=list(_CAUGHT))
def test_the_guard_catches_every_side_door(src):
    assert _flag(src), "the guard would miss a way to write Order.status"


@pytest.mark.parametrize("src", _IGNORED.values(), ids=list(_IGNORED))
def test_the_guard_leaves_safe_shapes_alone(src):
    assert not _flag(src), "the guard flagged a write it should not"


def test_transition_map_still_reachable_from_order_service():
    # The map moved to `order_lifecycle`; the re-export keeps every caller and
    # test that has always found it on `order_service` working. If this import
    # breaks, so did they.
    from app.services.orders.order_lifecycle import VALID_TRANSITIONS as canonical
    from app.services.orders.order_service import VALID_TRANSITIONS as re_exported

    assert re_exported is canonical
