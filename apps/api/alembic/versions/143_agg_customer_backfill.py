"""Normalize customer phones, add country/type/access-code, re-map aggregator customers.

Two things land together here:

1. **The aggregator customer re-map.** The ingest used to store the customer
   straight off GrubOps, which put a Deliveroo customer's Apple private-relay
   *email* in the name row, dropped the access code that makes its generic number
   callable, and left the email blank. This repairs the orders already taken from
   the raw GrubOps payload the ledger kept (`grubops_order_map.raw`).

2. **One phone format, everywhere.** `customer_phone` was E.164 on website orders
   and whatever-was-typed on POS/aggregator/custom ones. Every stored number is
   re-normalised to E.164, with its ISO country ("AE") and line type
   ("mobile"/"landline"/"toll_free") written to the two new columns beside it. The
   Deliveroo access code gets its own column too, kept apart from the number.

Self-contained: the phone logic uses `phonenumbers` directly (a stable external
lib) rather than importing app code that could move under a fresh-DB replay.
Guarded the way content backfills are (canon rule 7) — every write is conditional
on the value actually changing, so a re-run, or a restore from a later dump, does
nothing.

Revision ID: 143_agg_customer_backfill
Revises: 142_agg_driver_info
Create Date: 2026-08-24
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import phonenumbers
import sqlalchemy as sa
from phonenumbers import PhoneNumberType

from alembic import op

revision: str = "143_agg_customer_backfill"
down_revision: Union[str, None] = "142_agg_driver_info"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UNKNOWN = {"", "0", "unknown", "unknown unknown", "none", "null"}
_TYPE_LABELS = {
    PhoneNumberType.FIXED_LINE: "landline",
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "mobile",
    PhoneNumberType.TOLL_FREE: "toll_free",
}


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _UNKNOWN:
        return None
    return text


def _looks_like_email(value: str) -> bool:
    return "@" in value and " " not in value


def _describe(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """E.164, ISO region and line type for `raw`, mirroring `core.phone`."""
    if not raw:
        return None, None, None
    number = None
    for candidate in (raw, "".join(c for c in raw if c.isdigit() or c == "+")):
        try:
            parsed = phonenumbers.parse(candidate, "AE")
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            number = parsed
            break
    if number is None:
        return None, None, None
    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    region = phonenumbers.region_code_for_number(number)
    label = _TYPE_LABELS.get(phonenumbers.number_type(number), "other")
    return e164, region, label


def _agg_customer(customer: dict) -> tuple[str | None, str | None, str | None, str]:
    """(name, raw phone, access code, email) — the aggregator untangle, no append."""
    raw_name = _clean(customer.get("customerName"))
    email = _clean(customer.get("customerEmail"))
    name = raw_name
    if raw_name and _looks_like_email(raw_name):
        email = email or raw_name
        name = None
    phone = _clean(customer.get("customerMobile")) or _clean(customer.get("customerId"))
    code = _clean(customer.get("customerPhoneCode"))
    if phone is None:
        code = None
    return name, phone, code, email or ""


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("customer_phone_country", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("customer_phone_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("customer_phone_access_code", sa.String(length=20), nullable=True),
    )

    conn = op.get_bind()

    # ── 1. Aggregator customer re-map (name / raw phone / access code / email) ──
    agg = conn.execute(
        sa.text(
            """
            SELECT o.id, o.customer_name, o.customer_phone, o.email,
                   o.customer_phone_access_code, m.raw -> 'customer' AS customer
            FROM orders o
            JOIN grubops_order_map m ON m.mm_order_id = o.id
            WHERE o.source = 'aggregator' AND m.raw IS NOT NULL
            """
        )
    ).fetchall()
    agg_update = sa.text(
        "UPDATE orders SET customer_name = :name, customer_phone = :phone, "
        "email = :email, customer_phone_access_code = :code WHERE id = :id"
    )
    remapped = 0
    for oid, cur_name, cur_phone, cur_email, cur_code, customer in agg:
        if customer is None:
            continue
        if isinstance(customer, str):
            try:
                customer = json.loads(customer)
            except (ValueError, TypeError):
                continue
        if not isinstance(customer, dict):
            continue
        name, phone, code, email = _agg_customer(customer)
        if (name, phone, email, code) == (
            cur_name,
            cur_phone,
            cur_email or "",
            cur_code,
        ):
            continue
        conn.execute(
            agg_update,
            {"name": name, "phone": phone, "email": email, "code": code, "id": oid},
        )
        remapped += 1

    # ── 2. Normalise every order's phone to E.164 + country + type ──────────────
    rows = conn.execute(
        sa.text(
            "SELECT id, customer_phone, customer_phone_country, customer_phone_type "
            "FROM orders WHERE customer_phone IS NOT NULL AND customer_phone <> ''"
        )
    ).fetchall()
    norm_update = sa.text(
        "UPDATE orders SET customer_phone = :phone, "
        "customer_phone_country = :country, customer_phone_type = :type WHERE id = :id"
    )
    normalised = 0
    for oid, phone, country, ptype in rows:
        e164, region, label = _describe(phone)
        new_phone = e164 or phone
        if (new_phone, region, label) == (phone, country, ptype):
            continue
        conn.execute(
            norm_update,
            {"phone": new_phone, "country": region, "type": label, "id": oid},
        )
        normalised += 1

    # ── 3. Custom orders: normalise the number too (no country/type columns) ────
    custom = conn.execute(
        sa.text(
            "SELECT id, customer_phone FROM custom_orders "
            "WHERE customer_phone IS NOT NULL AND customer_phone <> ''"
        )
    ).fetchall()
    custom_update = sa.text(
        "UPDATE custom_orders SET customer_phone = :phone WHERE id = :id"
    )
    custom_fixed = 0
    for oid, phone in custom:
        e164, _region, _label = _describe(phone)
        if not e164 or e164 == phone:
            continue
        conn.execute(custom_update, {"phone": e164, "id": oid})
        custom_fixed += 1

    print(
        f"143: re-mapped {remapped} aggregator customer(s), "
        f"normalised {normalised} order phone(s) and {custom_fixed} custom-order phone(s)"
    )


def downgrade() -> None:
    op.drop_column("orders", "customer_phone_access_code")
    op.drop_column("orders", "customer_phone_type")
    op.drop_column("orders", "customer_phone_country")
