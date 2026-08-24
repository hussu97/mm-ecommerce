"""
No money is quantised outside `app/core/money.py`.

There were eight private helpers under five names, and half of them called
`.quantize(Decimal("0.01"))` with no `rounding=`. That is `ROUND_HALF_EVEN`,
bankers' rounding, and the other half used `ROUND_HALF_UP`. Both were applied
to the same POS money, so 0.125 became 0.13 on the pricing path and 0.12 on the
report of that same sale.

Nothing failed. A report that disagrees with the till by one fils is not an
error anybody can point at, which is why it survived five names.

The check is an AST walk rather than a grep so that comments and strings cannot
trip it, and it is deliberately narrow: it reads a literal precision, so
`quantize(SOME_CONSTANT)` and a `Decimal` imported under another name both slip
past. Neither happens here, and widening it would cost more false positives
than it catches.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
MONEY_MODULE = APP_DIR / "core" / "money.py"

#: The precision this rule is about: two places, the figure a customer is
#: charged and a till is counted in. Four and six places are reached only
#: through `quantity()` and `unit_cost()`, and a `vat_rate` quantised to four
#: places is a rate rather than money — it rounds by its own rules.
MONEY_PRECISIONS = {"0.01"}


def _bare_quantize_calls(path: Path) -> list[str]:
    """Calls to `.quantize(<money precision>)` with no `rounding=` argument."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "quantize"):
            continue
        if any(kw.arg == "rounding" for kw in node.keywords):
            continue
        # Already rounded deliberately further up the same expression — e.g.
        # `amount.to_integral_value(rounding=ROUND_CEILING).quantize(...)`,
        # where the quantize only puts two places on an integer and has no tie
        # left to break.
        if any(
            isinstance(inner, ast.Call)
            and any(kw.arg == "rounding" for kw in inner.keywords)
            for inner in ast.walk(func.value)
        ):
            continue
        # Only flag a literal money precision. A variable argument is a
        # constant defined elsewhere and this test cannot see through it.
        for arg in node.args:
            literal = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literal = arg.value
            elif (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "Decimal"
                and arg.args
                and isinstance(arg.args[0], ast.Constant)
            ):
                literal = str(arg.args[0].value)
            if literal in MONEY_PRECISIONS:
                hits.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")
    return hits


def test_no_module_quantises_money_on_its_own():
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path == MONEY_MODULE:
            continue
        offenders.extend(_bare_quantize_calls(path))

    assert not offenders, (
        "these quantise a money figure without saying how to round it, which "
        "is bankers' rounding by default. Use app/core/money.py — money(), "
        f"quantity() or unit_cost():\n  {chr(10).join(offenders)}"
    )
