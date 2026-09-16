"""F-INV-19: exported cells cannot smuggle a spreadsheet formula.

Every export column is filled from catalogue names, SKUs, references and
descriptions an operator can type, so a value like `=cmd|'/c calc'!A1` in a
product name would execute the moment someone opens the workbook. `_safe`
prefixes a leading formula trigger with a single quote so a spreadsheet reads the
cell as text.
"""

from __future__ import annotations

import pytest

from app.services.inventory.export_service import _safe, _safe_row


@pytest.mark.parametrize(
    "dangerous",
    [
        "=1+1",
        "+1",
        "-1+1",
        "@SUM(A1:A9)",
        "=cmd|'/c calc'!A1",
        "\ttab-led",
        "\rreturn-led",
    ],
)
def test_a_formula_leading_cell_is_quoted(dangerous):
    out = _safe(dangerous)
    assert out == f"'{dangerous}"


@pytest.mark.parametrize(
    "safe",
    ["Chocolate Cake", "SKU-1001", "A perfectly normal note", "1.5 kg", ""],
)
def test_ordinary_text_is_left_alone(safe):
    assert _safe(safe) == safe


def test_integers_pass_through_numeric():
    # Numbers cannot open a formula, and keeping them numeric preserves the
    # workbook's numeric cells and the import round-trip.
    assert _safe(7) == 7
    assert _safe(0) == 0


def test_safe_row_covers_every_cell():
    assert _safe_row(["=BAD", "ok", 3, "@x"]) == ["'=BAD", "ok", 3, "'@x"]
