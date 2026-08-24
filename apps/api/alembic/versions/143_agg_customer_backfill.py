"""Re-map the customer on aggregator orders already in the table.

The ingest used to store the customer straight off GrubOps, which put a
Deliveroo customer's Apple private-relay *email* in the name row, dropped the
access code that makes its generic number callable, and left the email blank.
`_customer_fields` sorts this out for new orders; this repairs the ones already
taken, from the raw GrubOps payload the ledger kept (`grubops_order_map.raw`).

Guarded the way content backfills are (canon rule 7): it only writes a row whose
computed values actually differ from what is stored, so a re-run — or a database
restored from a later dump — matches nothing and does nothing. Aggregator
customer fields have no admin edit path, so re-deriving them from the marketplace
payload cannot overwrite a human's correction; there is none to overwrite.

Revision ID: 143_agg_customer_backfill
Revises: 142_agg_driver_info
Create Date: 2026-08-24
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "143_agg_customer_backfill"
down_revision: Union[str, None] = "142_agg_driver_info"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UNKNOWN = {"", "0", "unknown", "unknown unknown", "none", "null"}


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _UNKNOWN:
        return None
    return text


def _looks_like_email(value: str) -> bool:
    return "@" in value and " " not in value


def _fields(customer: dict) -> tuple[str | None, str | None, str]:
    """Same rules as `grubops_orders_service._customer_fields`, inlined so the
    migration does not import app code that may change out from under it."""
    raw_name = _clean(customer.get("customerName"))
    email = _clean(customer.get("customerEmail"))
    name = raw_name
    if raw_name and _looks_like_email(raw_name):
        email = email or raw_name
        name = None
    phone = _clean(customer.get("customerMobile")) or _clean(customer.get("customerId"))
    code = _clean(customer.get("customerPhoneCode"))
    if phone and code:
        phone = f"{phone} (Access code {code})"
    return name, phone, email or ""


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT o.id,
                   o.customer_name,
                   o.customer_phone,
                   o.email,
                   m.raw -> 'customer' AS customer
            FROM orders o
            JOIN grubops_order_map m ON m.mm_order_id = o.id
            WHERE o.source = 'aggregator'
              AND m.raw IS NOT NULL
            """
        )
    ).fetchall()

    update = sa.text(
        "UPDATE orders SET customer_name = :name, customer_phone = :phone, "
        "email = :email WHERE id = :id"
    )

    fixed = 0
    for oid, cur_name, cur_phone, cur_email, customer in rows:
        if customer is None:
            continue
        if isinstance(customer, str):
            try:
                customer = json.loads(customer)
            except (ValueError, TypeError):
                continue
        if not isinstance(customer, dict):
            continue

        name, phone, email = _fields(customer)
        # Only write when something actually changes — the guard that makes this
        # idempotent and keeps it from churning rows that are already right.
        if (name, phone, email) == (cur_name, cur_phone, cur_email or ""):
            continue
        conn.execute(update, {"name": name, "phone": phone, "email": email, "id": oid})
        fixed += 1

    print(f"143_agg_customer_backfill: re-mapped {fixed} aggregator order(s)")


def downgrade() -> None:
    # A data repair; the previous values were wrong and are not worth restoring.
    pass
