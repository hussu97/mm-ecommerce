"""
A migration cannot change a UI string, so it must not try.

`ui_translations` is owned by `scripts/seed_i18n.py`, which `app_setup` runs in
the API's lifespan. It executes on every boot and overwrites any row whose value
differs from the constant in that file. So a migration that edits a UI string
applies cleanly, the API restarts, the seed puts the old text straight back and
invalidates the Redis cache — and the restored value is serving within seconds.

That is not a theory. Migrations `121` and `122` were both written to change
banner copy, both reviewed, both deployed green, and both changed nothing that
outlived the deploy. Nothing failed, which is why it took two of them.

New migrations therefore fail here instead. The historical ones are listed with
what actually happened to each, because the list is the evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

#: Migrations that already write `ui_translations`. Every one of these predates
#: the rule; none may be added to.
ALREADY_SHIPPED = {
    # Four value rewrites. Whether they survived depends on whether the seed
    # was edited to match, which is exactly the coin-flip this rule removes.
    "025_fix_delivery_note_i18n.py",
    # Guarded on the old value, and paired with a seed edit — the shape that
    # works, arrived at by hand.
    "109_premises_copy_remainder.py",
    # The one that proved it. Deployed green, changed nothing that lasted.
    "121_promo_banner_delivery_figure.py",
}

#: An UPDATE is the operation that cannot work: the seed compares every row to
#: its constant and writes the constant back. DELETE is the sanctioned half of
#: retiring a key, and an INSERT is either the table's own creation (`007`) or a
#: downgrade restoring a row it removed — both survive, because the seed only
#: adds and updates, and neither is trying to change a string.
UPDATES_A_STRING = re.compile(
    r"UPDATE\s+ui_translations|update\(\s*UiTranslation",
    re.I,
)


def test_no_new_migration_writes_a_ui_string():
    offenders = sorted(
        path.name
        for path in VERSIONS.glob("*.py")
        if path.name not in ALREADY_SHIPPED
        and UPDATES_A_STRING.search(path.read_text())
    )

    assert not offenders, (
        "these migrations UPDATE `ui_translations`, which the boot seed "
        "overwrites on the next restart — they will apply and change nothing:\n"
        f"  {chr(10).join(offenders)}\n\n"
        "Edit ALL_TRANSLATIONS in apps/api/scripts/seed_i18n.py instead. "
        "Retiring a key needs both: remove the line there so it stops being "
        "restored, and a DELETE migration for the row that already exists."
    )


def test_the_seed_still_runs_on_boot():
    """
    The rule above is only true while this is.

    If the seed stops running in the lifespan, `ui_translations` becomes an
    ordinary table and rule 7 applies to it like anything else — at which point
    this file and the exception in CLAUDE.md should both go.
    """
    app_setup = (VERSIONS.parents[1] / "app" / "app_setup.py").read_text()

    assert "seed_i18n" in app_setup, (
        "app_setup no longer runs the i18n seed. If that is deliberate, delete "
        "this test and the `ui_translations` exception in CLAUDE.md rule 7 — "
        "the reason for both has gone."
    )


def test_seed_loads_translations_in_one_query():
    """
    A SELECT per key (~1,500 round-trips) is what made api-green sit in
    'Running i18n seed...' past the healthcheck start_period.
    """
    import inspect

    from scripts import seed_i18n

    source = inspect.getsource(seed_i18n.seed)
    assert "existing_by_key" in source
    assert "UiTranslation.key == key" not in source
