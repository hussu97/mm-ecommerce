"""
No test may invent a `GatewayEvent` shape a provider cannot emit (F-TST-1).

The F-ORD-1 bug hid for months behind a fixture that built a `SUCCEEDED` event
carrying BOTH a `session_id` and a differing `payment_id` — a shape
`payment_intent.succeeded` (payment id, no session) and Ziina's intent (session
== payment) each never produce. The test asserted the very stitching the
production code failed to do, so it stayed green while refunds silently returned
zero.

This guard closes that door. It reads the *actual* providers to learn which
correlation/amount fields each real webhook carries per event type, then scans
every `GatewayEvent(...)` construction (and the local factories that wrap one)
under `tests/`, and fails if any populates a combination of those fields that no
provider emits together.

The check is subset-based on purpose: a test that leaves fields unset is fine —
it simply is not exercising them. What is forbidden is a *combination* of set
fields that no single real event ever carries at once.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import time
from pathlib import Path

import stripe

from app.core.config import settings
from app.services.providers import stripe_provider as sp
from app.services.providers import ziina_provider as zp
from app.services.providers.base import GatewayEvent

#: The fields correlation and money live in. Everything else on a `GatewayEvent`
#: (ids, raw type, error strings) is not what impossible *combinations* are made
#: of, so the guard is scoped to these.
_TRACKED = (
    "order_number",
    "session_id",
    "payment_id",
    "amount_refunded",
    "amount_captured",
    "fully_refunded",
)

_STRIPE_SECRET = "whsec_guard_secret"
_ZIINA_SECRET = "ziina_guard_secret"


def _stripe_shapes() -> dict[str, set[frozenset[str]]]:
    settings.STRIPE_WEBHOOK_SECRET = _STRIPE_SECRET
    maximal = [
        (
            "payment_intent.succeeded",
            {
                "id": "pi",
                "amount_received": 100,
                "metadata": {"order_number": "MM"},
            },
        ),
        (
            "payment_intent.payment_failed",
            {
                "id": "pi",
                "metadata": {"order_number": "MM"},
            },
        ),
        (
            "payment_intent.canceled",
            {
                "id": "pi",
                "metadata": {"order_number": "MM"},
            },
        ),
        (
            "checkout.session.completed",
            {
                "id": "cs",
                "payment_intent": "pi",
                "metadata": {"order_number": "MM"},
            },
        ),
        (
            "checkout.session.expired",
            {
                "id": "cs",
                "payment_intent": "pi",
                "metadata": {"order_number": "MM"},
            },
        ),
        (
            "charge.refunded",
            {
                "id": "ch",
                "payment_intent": "pi",
                "amount_refunded": 50,
                "amount": 100,
                "refunded": True,
                "metadata": {"order_number": "MM"},
            },
        ),
        ("charge.dispute.created", {"id": "dp", "payment_intent": "pi"}),
        ("customer.updated", {"id": "cus"}),  # → UNHANDLED
    ]
    shapes: dict[str, set[frozenset[str]]] = {}
    for raw_type, obj in maximal:
        body = {"id": "evt", "type": raw_type, "data": {"object": obj}}
        payload = json.dumps(body).encode()
        ts = int(time.time())
        sig = stripe.WebhookSignature._compute_signature(
            f"{ts}.{payload.decode()}", _STRIPE_SECRET
        )
        event = sp.provider.parse_webhook(
            payload, {"stripe-signature": f"t={ts},v1={sig}"}
        )
        _record(shapes, event)
    return shapes


def _ziina_shapes(shapes: dict[str, set[frozenset[str]]]) -> None:
    settings.ZIINA_WEBHOOK_SECRET = _ZIINA_SECRET
    maximal = [
        {
            "event": "payment_intent.status.updated",
            "data": {"id": "pi", "status": "completed"},
        },
        {
            "event": "payment_intent.status.updated",
            "data": {"id": "pi", "status": "failed"},
        },
        {
            "event": "payment_intent.status.updated",
            "data": {"id": "pi", "status": "canceled"},
        },
        {
            "event": "payment_intent.status.updated",
            "data": {"id": "pi", "status": "pending"},
        },
        {
            "event": "refund.status.updated",
            "data": {
                "id": "rf",
                "payment_intent_id": "pi",
                "status": "completed",
                "amount": 50,
            },
        },
        {
            "event": "refund.status.updated",
            "data": {
                "id": "rf",
                "payment_intent_id": "pi",
                "status": "pending",
                "amount": 50,
            },
        },
    ]
    for body in maximal:
        payload = json.dumps(body).encode()
        sig = hmac.new(_ZIINA_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        event = zp.provider.parse_webhook(payload, {"X-Hmac-Signature": sig})
        _record(shapes, event)


def _present(event: GatewayEvent) -> frozenset[str]:
    return frozenset(f for f in _TRACKED if getattr(event, f) is not None)


def _record(shapes: dict[str, set[frozenset[str]]], event: GatewayEvent) -> None:
    shapes.setdefault(event.event_type.value, set()).add(_present(event))


def _emittable_shapes() -> dict[str, set[frozenset[str]]]:
    shapes = _stripe_shapes()
    _ziina_shapes(shapes)
    return shapes


# ── the AST scan ──────────────────────────────────────────────────────────────


def _factory_names(tree: ast.Module) -> set[str]:
    """Local functions that construct and return a `GatewayEvent`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "GatewayEvent"
                ):
                    names.add(node.name)
                    break
    return names


def _member(node: ast.AST) -> str | None:
    """The `PaymentEventType.X` member name a node refers to, if it is one."""
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _construction_calls(tree: ast.Module, factories: set[str]):
    """Yield (call, event_type_or_None) for every event construction site."""
    targets = {"GatewayEvent"} | factories
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in targets:
            continue
        # A `**kwargs` spread hides the fields — that is the factory's internal
        # `GatewayEvent(**kw)`, whose real shape is at the factory's call sites,
        # which are scanned separately. Skip it rather than guess.
        if any(kw.arg is None for kw in node.keywords):
            continue
        event_type = None
        for kw in node.keywords:
            if kw.arg == "event_type":
                event_type = _member(kw.value)
        if event_type is None and node.func.id != "GatewayEvent" and node.args:
            # Factory convention: event_type is the first positional argument.
            event_type = _member(node.args[0])
        yield node, event_type


def _present_fields(call: ast.Call) -> frozenset[str]:
    return frozenset(
        kw.arg for kw in call.keywords if kw.arg in _TRACKED and not _is_none(kw.value)
    )


_TESTS_DIR = Path(__file__).resolve().parent.parent


def test_no_test_fixture_invents_a_gateway_event_field_combination():
    shapes = _emittable_shapes()
    all_shapes = [s for group in shapes.values() for s in group]

    violations: list[str] = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        factories = _factory_names(tree)
        for call, event_type in _construction_calls(tree, factories):
            present = _present_fields(call)
            if event_type is not None and event_type in shapes:
                candidates = shapes[event_type]
            else:
                # event_type not statically knowable (parametrised) — accept the
                # combination if any real event of any type carries it.
                candidates = all_shapes
            if not any(present <= shape for shape in candidates):
                violations.append(
                    f"{path.relative_to(_TESTS_DIR.parent)}:{call.lineno} "
                    f"event_type={event_type} fields={sorted(present)} — no "
                    f"provider emits this combination"
                )

    assert not violations, "impossible GatewayEvent shapes:\n" + "\n".join(violations)


def test_the_guard_can_see_the_providers_shapes():
    """A smoke check: the derivation actually produced shapes to test against."""
    shapes = _emittable_shapes()
    assert shapes["succeeded"]
    assert shapes["refunded"]
    # amount_captured now rides on a succeeded intent (F-ORD-19).
    assert any("amount_captured" in s for s in shapes["succeeded"])
