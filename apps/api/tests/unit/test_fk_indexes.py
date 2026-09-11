"""
F-OPS-23: every foreign-key column should lead an index, or be named in
`ALLOW_LIST` below with a reason it does not.

Postgres never indexes the referencing side of a foreign key on its own —
only the referenced side (via the primary/unique key it points at) — so an
FK column with no index of its own is a sequential scan waiting for its
table to grow. `orders`/`order_items` found five and four of these the hard
way (migration `210_fk_indexes`); this test is what stops the next one from
sitting there quietly until someone notices a slow query.

Not every FK column earns an index. Migration `102_order_items_product_id_idx`
said it plainly: an audit column "written once, read when a person asks about
one specific order, and never the driving side of a hot query" does not need
one, and "any of them can be added in its own migration the day a real query
wants it." `ALLOW_LIST` is that deferred list, not a blanket exemption —
trim an entry the day its column gets a real index, and `test_allow_list_has_
no_stale_entries` catches anyone who forgets.
"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models import Base

_AUDIT_ACTOR = (
    "Actor/audit FK: stamped once when the row is written or transitioned, "
    "read only when someone opens that one record — never the driving side "
    "of a query. Pre-existing gap, out of F-OPS-23's scope; add a real index "
    "the day a query needs one (migration 102's rule)."
)
_SMALL_LOOKUP = (
    "FK to a small, mostly-static lookup/config table, joined by primary key "
    "FROM the other side (the lookup row -> its users), never filtered from "
    "this side. Pre-existing gap, out of F-OPS-23's scope."
)
_INVENTORY_LOWER_TRAFFIC = (
    "Inventory-module FK: far lower write/read volume than orders, joined "
    "mostly by id from the parent side. Pre-existing gap, out of F-OPS-23's "
    "scope; add a real index the day a query needs one."
)
_RAW_MIGRATION_INDEX = (
    "Indexed in the database by a bare op.create_index in a migration (041 "
    "for order_items.course_id, 102 for order_items.product_id, 210 for the "
    "rest) rather than a model-level Index/index=True — the deliberate "
    "convention this codebase uses for reporting/operational indexes (see "
    "env.py's include_object docstring). Does not show up in SQLAlchemy's "
    "metadata; real index in the database, nothing to add."
)

# (table, column) -> why it is not in migration 210's list and does not (yet)
# have a leading index. Grouped by the shared reason above rather than one
# bespoke sentence per column — this is a catalogue of pre-existing debt, not
# an individually-argued audit; treat a removal from this list (because the
# column now has a real index) as the thing to celebrate, not an entry to
# re-justify.
ALLOW_LIST: dict[tuple[str, str], str] = {
    # F-OPS-23 / migration 210: indexed via a bare op.create_index, so the
    # index is real but invisible to Base.metadata (see _RAW_MIGRATION_INDEX).
    ("aggregator_order", "mm_order_id"): _RAW_MIGRATION_INDEX,
    ("orders", "device_id"): _RAW_MIGRATION_INDEX,
    ("orders", "table_id"): _RAW_MIGRATION_INDEX,
    ("orders", "driver_id"): _RAW_MIGRATION_INDEX,
    ("orders", "creator_id"): _RAW_MIGRATION_INDEX,
    ("orders", "closer_id"): _RAW_MIGRATION_INDEX,
    ("order_items", "kitchen_flow_id"): _RAW_MIGRATION_INDEX,
    ("order_items", "course_id"): _RAW_MIGRATION_INDEX,
    ("order_items", "creator_id"): _RAW_MIGRATION_INDEX,
    ("order_items", "voided_by_id"): _RAW_MIGRATION_INDEX,
    ("order_taxes", "tax_id"): _RAW_MIGRATION_INDEX,
    # Pre-existing gaps, out of F-OPS-23's scope:
    ("aggregator_menu_snapshot", "branch_id"): _SMALL_LOOKUP,
    ("aggregator_reconciliation", "aggregator_order_id"): _AUDIT_ACTOR,
    ("aggregator_reconciliation", "run_id"): _AUDIT_ACTOR,
    ("branch_business_days", "closed_by_id"): _AUDIT_ACTOR,
    ("branch_business_days", "opened_by_id"): _AUDIT_ACTOR,
    ("branches", "return_branch_id"): _SMALL_LOOKUP,
    ("branches", "tax_group_id"): _SMALL_LOOKUP,
    ("cart_items", "product_id"): _SMALL_LOOKUP,
    ("charges", "tax_group_id"): _SMALL_LOOKUP,
    ("combos", "category_id"): _SMALL_LOOKUP,
    ("combos", "tax_group_id"): _SMALL_LOOKUP,
    ("custom_orders", "created_by_id"): _AUDIT_ACTOR,
    ("custom_orders", "product_id"): _SMALL_LOOKUP,
    ("device_push_tokens", "device_id"): _SMALL_LOOKUP,
    ("device_push_tokens", "user_id"): _SMALL_LOOKUP,
    ("drawer_operations", "order_id"): _AUDIT_ACTOR,
    ("drawer_operations", "reason_id"): _SMALL_LOOKUP,
    ("external_item_map", "category_id"): _SMALL_LOOKUP,
    ("external_item_map", "inventory_item_id"): _SMALL_LOOKUP,
    ("external_item_map", "modifier_option_id"): _SMALL_LOOKUP,
    ("external_item_map", "product_id"): _SMALL_LOOKUP,
    ("grubops_sync_state", "external_item_map_id"): _SMALL_LOOKUP,
    ("inventory_categories", "parent_id"): _SMALL_LOOKUP,
    ("inventory_lots", "item_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_report_template_items", "item_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_source_events", "transaction_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transaction_items", "lot_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transaction_items", "recipe_version_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transactions", "creator_id"): _AUDIT_ACTOR,
    ("inventory_transactions", "order_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transactions", "other_branch_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transactions", "other_warehouse_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transactions", "poster_id"): _AUDIT_ACTOR,
    ("inventory_transactions", "purchase_order_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transactions", "reason_id"): _SMALL_LOOKUP,
    ("inventory_transactions", "reverses_transaction_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transactions", "supplier_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transfer_template_items", "item_id"): _INVENTORY_LOWER_TRAFFIC,
    ("inventory_transfer_templates", "destination_branch_id"): _INVENTORY_LOWER_TRAFFIC,
    ("order_charges", "charge_id"): _SMALL_LOOKUP,
    ("order_discounts", "applied_by_id"): _AUDIT_ACTOR,
    ("order_items", "product_id"): _RAW_MIGRATION_INDEX,
    ("order_items", "void_reason_id"): _AUDIT_ACTOR,
    ("order_payments", "till_id"): _AUDIT_ACTOR,
    ("order_payments", "user_id"): _AUDIT_ACTOR,
    ("order_status_events", "actor_id"): _AUDIT_ACTOR,
    ("orders", "original_order_id"): _AUDIT_ACTOR,
    ("orders", "void_reason_id"): _AUDIT_ACTOR,
    ("printers", "kitchen_flow_id"): _SMALL_LOOKUP,
    ("purchase_orders", "approver_id"): _AUDIT_ACTOR,
    ("purchase_orders", "creator_id"): _AUDIT_ACTOR,
    ("purchase_orders", "submitter_id"): _AUDIT_ACTOR,
    ("purchase_orders", "warehouse_id"): _INVENTORY_LOWER_TRAFFIC,
    ("recipe_versions", "activated_by"): _AUDIT_ACTOR,
    ("shift_inventory_report_comments", "author_id"): _AUDIT_ACTOR,
    ("shift_inventory_report_lines", "item_id"): _INVENTORY_LOWER_TRAFFIC,
    ("shift_inventory_reports", "approved_by"): _AUDIT_ACTOR,
    ("shift_inventory_reports", "submitted_by"): _AUDIT_ACTOR,
    ("shift_inventory_reports", "transaction_id"): _INVENTORY_LOWER_TRAFFIC,
    ("tables", "parent_id"): _SMALL_LOOKUP,
    ("tables", "revenue_center_tag_id"): _SMALL_LOOKUP,
    ("tills", "closed_by_id"): _AUDIT_ACTOR,
    ("transfer_orders", "creator_id"): _AUDIT_ACTOR,
    ("transfer_orders", "received_transaction_id"): _INVENTORY_LOWER_TRAFFIC,
    ("transfer_orders", "responder_id"): _AUDIT_ACTOR,
    ("transfer_orders", "sent_transaction_id"): _INVENTORY_LOWER_TRAFFIC,
    ("transfer_orders", "source_warehouse_id"): _INVENTORY_LOWER_TRAFFIC,
    ("transfer_orders", "submitter_id"): _AUDIT_ACTOR,
    ("transfer_orders", "warehouse_id"): _INVENTORY_LOWER_TRAFFIC,
}


def _leading_indexed_columns(table) -> set[str]:
    """Columns that already have SOME index/constraint with them in the
    leading position — a primary key, a single-column index, a composite
    index or unique constraint whose first column is this one."""
    covered = {c.name for c in table.primary_key.columns}
    for idx in table.indexes:
        cols = list(idx.columns)
        if cols:
            covered.add(cols[0].name)
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            cols = list(constraint.columns)
            if cols:
                covered.add(cols[0].name)
    return covered


def _fk_columns(table) -> set[str]:
    return {fk.parent.name for fk in table.foreign_keys}


def test_every_fk_column_leads_an_index_or_is_allow_listed():
    unexplained: list[str] = []
    for table_name, table in sorted(Base.metadata.tables.items()):
        fk_cols = _fk_columns(table)
        if not fk_cols:
            continue
        covered = _leading_indexed_columns(table)
        for column in sorted(fk_cols - covered):
            if (table_name, column) not in ALLOW_LIST:
                unexplained.append(f"{table_name}.{column}")
    assert not unexplained, (
        "FK column(s) with no leading index and no ALLOW_LIST entry in "
        f"test_fk_indexes.py: {unexplained}. Either index the column "
        "(migration + CONCURRENTLY on a live table) or add it to ALLOW_LIST "
        "with a reason."
    )


def test_allow_list_has_no_stale_entries():
    """
    An ALLOW_LIST entry for a column that now has an index (or no longer
    exists, or is no longer an FK) is stale — the day someone indexes one of
    these, this is what makes them delete the line instead of leaving a
    passing test that no longer means anything.
    """
    stale: list[str] = []
    for table_name, column in ALLOW_LIST:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            stale.append(f"{table_name}.{column} (table not found)")
            continue
        if column not in _fk_columns(table):
            stale.append(f"{table_name}.{column} (not an FK column any more)")
            continue
        if column in _leading_indexed_columns(table):
            stale.append(f"{table_name}.{column} (already indexed — trim me)")
    assert not stale, f"stale ALLOW_LIST entries in test_fk_indexes.py: {stale}"
