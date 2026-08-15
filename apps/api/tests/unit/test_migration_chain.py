"""
The migration chain, checked without a database.

The full down path had been broken for months and nobody knew, because nobody
runs it until the hour they need it. Four revisions had drifted into the same
asymmetry: a later migration drops a column (Postgres silently takes its
indexes and foreign keys with it), its downgrade puts the column back bare, and
the earlier revision that owned the index then fails dropping something that is
no longer there. `alembic downgrade base` stopped at the first one.

A real up/down/up cycle against Postgres is the only way to *prove* the chain
works, and it is run by hand on a throwaway database (see
`docs/architecture-audit-2026-08.md`). What lives here is the cheap half: the
structural checks that need no database and so can run on every commit — one
head, one root, no duplicate revision ids, no oversized ids, and the specific
create/drop asymmetries that caused the breakage, pinned so they cannot come
back the same way.
"""

from __future__ import annotations

import pathlib
import re

VERSIONS = pathlib.Path(__file__).parents[2] / "alembic" / "versions"


#: Both spellings in the tree: 13 of the early revisions declare bare
#: `down_revision = "..."`, the other 90 carry the annotated
#: `down_revision: Union[str, None] = "..."`. A parser that knows only one of
#: them reports phantom heads — which is exactly what the first draft of this
#: test did, calling a linear chain a three-way fork.
_ASSIGNMENT = r'^{name}\s*(?::[^=]+)?=\s*(?:"([^"]+)"|\'([^\']+)\'|None)'


def _value(text: str, name: str) -> str | None:
    match = re.search(_ASSIGNMENT.format(name=name), text, re.M)
    if not match:
        return None
    return match.group(1) or match.group(2)


def _revisions() -> dict[str, dict[str, str | None]]:
    found: dict[str, dict[str, str | None]] = {}
    for path in VERSIONS.glob("*.py"):
        text = path.read_text()
        revision = _value(text, "revision")
        if not revision:
            continue
        found[revision] = {"file": path.name, "down": _value(text, "down_revision")}
    return found


def test_every_migration_file_declares_a_revision():
    """A file the parser cannot read is a file these checks silently skip."""
    files = {path.name for path in VERSIONS.glob("*.py")} - {"__init__.py"}
    parsed = {meta["file"] for meta in _revisions().values()}
    assert files == parsed, f"unparsed migration files: {sorted(files - parsed)}"


def test_the_chain_is_linear_with_one_head_and_one_root():
    revisions = _revisions()
    downs = [meta["down"] for meta in revisions.values()]

    roots = [rev for rev, meta in revisions.items() if meta["down"] is None]
    assert len(roots) == 1, f"expected one root, found {roots}"

    heads = [rev for rev in revisions if rev not in downs]
    assert len(heads) == 1, f"expected one head, found {heads}"

    # A revision named as two migrations' parent is a fork: alembic will refuse
    # to upgrade, and the second branch's work never runs.
    named = [d for d in downs if d is not None]
    assert len(named) == len(set(named)), "a revision is the parent of two others"

    # Every parent must exist, or the chain has a hole in the middle.
    for revision, meta in revisions.items():
        if meta["down"] is not None:
            assert meta["down"] in revisions, (
                f"{meta['file']} descends from {meta['down']}, which is missing"
            )


def test_revision_ids_fit_the_version_column():
    """
    `alembic_version.version_num` is VARCHAR(32). A longer id runs every
    statement in the migration and *then* fails writing the stamp, which leaves
    the database changed and the chain thinking it never ran.
    """
    for revision, meta in _revisions().items():
        assert len(revision) <= 32, f"{meta['file']}: id is {len(revision)} chars"


def test_a_restored_column_brings_its_index_and_key_back():
    """
    The four downgrades that stranded the chain, pinned.

    Each of these puts back a column an earlier revision indexed, so each has
    to put the index (and, for `053`, the named foreign key) back too — the
    column alone is not the state the earlier revision left behind.
    """

    def downgrade_of(filename: str) -> str:
        text = (VERSIONS / filename).read_text()
        return text[text.index("def downgrade") :]

    down_088 = downgrade_of("088_batch_groups_and_couriers.py")
    assert "ix_delivery_batches_polygon_id" in down_088
    assert "ix_delivery_batch_windows_polygon_id" in down_088

    down_053 = downgrade_of("053_retire_regions.py")
    assert "ix_orders_region_id" in down_053
    assert "fk_orders_region_id" in down_053, "restored under 042's own name"

    down_045 = downgrade_of("045_product_sales_channels.py")
    assert "ix_products_is_web_visible" in down_045


def test_an_if_not_exists_create_does_not_drop_what_it_did_not_make():
    """
    `024` created its indexes with `IF NOT EXISTS` — so on any database that
    ran `002`, it created no `ix_refresh_tokens_user_id` at all. Dropping it on
    the way down destroyed `002`'s index and broke `002`'s own downgrade.
    """
    text = (VERSIONS / "024_missing_indexes.py").read_text()
    downgrade = text[text.index("def downgrade") :]
    assert "DROP INDEX IF EXISTS ix_refresh_tokens_user_id" not in downgrade
