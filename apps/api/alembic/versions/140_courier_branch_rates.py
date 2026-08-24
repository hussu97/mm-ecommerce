"""Per-branch and per-contract aggregator rates, and a recompute of what they cost.

`137_order_fees` gave every marketplace one commission rate and one payment rate,
both quoted before VAT and read the same plain way. That was enough for Noon
Food and no one else, because the other four contracts do not read the same:

  * **Deliveroo** takes 27% of a Sharjah basket and 31% of a Barsha one — the
    same courier, a different number per branch. Nothing on the `couriers` row
    could say that, so this adds `courier_branch_rate`: the exceptions, one row
    per (courier, branch) that differs, read ahead of the courier default.
  * **Keeta** is "4 AED + 25% of (the basket − 4 AED)", VAT already inside both
    the commission and the 2% payment fee. Two new grammar flags carry that —
    `commission_fixed_net_of_base` and the `*_vat_inclusive` pair.
  * **Careem** waives its 2% on a cash order (`payment_fee_cash_exempt`) and
    adds 4 AED only for a Careem-Plus member (`commission_fixed_requires_member`).
  * **Talabat** is 30% + VAT, 2% + VAT on every order, and 4 AED for a Pro/VIP
    member (the same member flag).

The member fee is modelled and **dormant**: GrubOps sends no field that tells a
member's order from anyone else's, so `orders.aggregator_customer_is_member` is
never set true and the 4 AED never lands. Seeding the rule over-charges nobody;
it just means the day a signal arrives, the fee is already wired.

Three per-order columns come with it: `aggregator_payment_type` (cash vs card,
read from GrubOps `paymentStatus`) so Careem's waiver has something to read, and
`aggregator_customer_is_member` for the dormant fee.

Finally the backfill. Every aggregator order is re-priced from the rates this
migration installs — the four channels that were null before now have real
fees, and Noon's recompute to exactly what they already were. The arithmetic
mirrors `order_fees` at the moment this ran; if that module's rules change later
this migration stays as written, because a historic row can only honestly say
what it cost on the day.

Revision ID: 140_courier_branch_rates
Revises: 139_pos_attr_backfill
Create Date: 2026-08-24
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "140_courier_branch_rates"
down_revision: Union[str, None] = "139_pos_attr_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VAT = Decimal("0.05")
_CENTS = Decimal("0.01")

#: GrubOps channel display name → our courier code. A subset of
#: `courier_catalog._CHANNEL_ALIASES`, inlined because a migration must not
#: import app code (it runs against a schema snapshot, not the live app).
_CHANNEL_ALIASES = {
    "talabat": "talabat",
    "keeta": "keeta",
    "keeta 2.0": "keeta",
    "keeta2.0": "keeta",
    "noon": "noon_food",
    "noon food": "noon_food",
    "noonfood": "noon_food",
    "deliveroo": "deliveroo",
    "careem": "careem",
    "careem now": "careem",
    "careemnow": "careem",
}


def _code_for_channel(channel: str | None) -> str | None:
    if not channel:
        return None
    key = channel.strip().lower()
    if key in _CHANNEL_ALIASES:
        return _CHANNEL_ALIASES[key]
    letters = "".join(c for c in key if c.isalpha() or c == " ").strip()
    return _CHANNEL_ALIASES.get(letters)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def upgrade() -> None:
    # ── 1. Grammar flags on the courier, override rates in a new table ────────
    for col in (
        "commission_vat_inclusive",
        "payment_fee_vat_inclusive",
        "commission_fixed_net_of_base",
        "payment_fee_cash_exempt",
        "commission_fixed_requires_member",
    ):
        op.add_column(
            "couriers",
            sa.Column(
                col, sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
        )

    op.add_column(
        "orders",
        sa.Column("aggregator_payment_type", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("aggregator_customer_is_member", sa.Boolean(), nullable=True),
    )

    op.create_table(
        "courier_branch_rate",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("courier_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("commission_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("commission_fixed", sa.Numeric(10, 2), nullable=True),
        sa.Column("payment_fee_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("payment_fee_fixed", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["courier_id"], ["couriers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("courier_id", "branch_id", name="uq_courier_branch_rate"),
    )
    op.create_index(
        "ix_courier_branch_rate_courier_id", "courier_branch_rate", ["courier_id"]
    )
    op.create_index(
        "ix_courier_branch_rate_branch_id", "courier_branch_rate", ["branch_id"]
    )

    # ── 2. Seed the agreed rates ─────────────────────────────────────────────
    #
    # Guarded like every rate seed here: it only fills a channel whose rate is
    # still null, so once a human edits one in the console — or a restored dump
    # already carries it — this matches nothing and does nothing.

    # Keeta: 4 + 25%·(basket − 4), VAT inside; 2% payment, VAT inside.
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = 25.00,
               commission_fixed = 4.00,
               commission_fixed_net_of_base = true,
               commission_vat_inclusive = true,
               payment_fee_percent = 2.00,
               payment_fee_vat_inclusive = true
         WHERE code = 'keeta'
           AND commission_percent IS NULL
           AND payment_fee_percent IS NULL
        """
    )

    # Careem: 25% + VAT, +4 AED for a member (dormant), 2% + VAT on card only.
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = 25.00,
               commission_fixed = 4.00,
               commission_fixed_requires_member = true,
               payment_fee_percent = 2.00,
               payment_fee_cash_exempt = true
         WHERE code = 'careem'
           AND commission_percent IS NULL
           AND payment_fee_percent IS NULL
        """
    )

    # Talabat: 30% + VAT, +4 AED for a member (dormant), 2% + VAT on every order.
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = 30.00,
               commission_fixed = 4.00,
               commission_fixed_requires_member = true,
               payment_fee_percent = 2.00
         WHERE code = 'talabat'
           AND commission_percent IS NULL
           AND payment_fee_percent IS NULL
        """
    )

    # Deliveroo: commission is per-branch (below), payment fee is a real zero —
    # an explicit 0, not the "unknown" a null would read as, so the net is
    # itemised in full rather than blanked.
    op.execute(
        """
        UPDATE couriers
           SET payment_fee_percent = 0.00
         WHERE code = 'deliveroo'
           AND payment_fee_percent IS NULL
        """
    )

    # Deliveroo's two branch rates: 27% in Sharjah (K001), 31% in Barsha (B001).
    # Matched on branch `reference` rather than a hardcoded id, and inserted only
    # where the pair does not already exist, so it is safe on a re-run and on a
    # database that already carries the override.
    for reference, percent in (("K001", "27.00"), ("B001", "31.00")):
        op.execute(
            f"""
            INSERT INTO courier_branch_rate (id, courier_id, branch_id, commission_percent)
            SELECT gen_random_uuid(), c.id, b.id, {percent}
              FROM couriers c, branches b
             WHERE c.code = 'deliveroo'
               AND b.reference = '{reference}'
               AND NOT EXISTS (
                   SELECT 1 FROM courier_branch_rate r
                    WHERE r.courier_id = c.id AND r.branch_id = b.id
               )
            """
        )

    # ── 3. Backfill the per-order payment type from the stored GrubOps payload ─
    op.execute(
        """
        UPDATE orders o
           SET aggregator_payment_type = CASE
                   WHEN upper(coalesce(m.raw->'orderHeader'->>'paymentStatus','')) = 'POSTPAID'
                     OR upper(coalesce(m.raw->'orderHeader'->>'paymentMethod','')) = 'CASH'
                        THEN 'postpaid'
                   WHEN upper(coalesce(m.raw->'orderHeader'->>'paymentStatus','')) = 'PREPAID'
                     OR upper(coalesce(m.raw->'orderHeader'->>'paymentMethod','')) = 'PREPAID'
                        THEN 'prepaid'
                   ELSE NULL
               END
          FROM grubops_order_map m
         WHERE m.mm_order_id = o.id
           AND o.source = 'aggregator'
           AND o.aggregator_payment_type IS NULL
           AND m.raw IS NOT NULL
        """
    )

    # ── 4. Recompute every aggregator order's fees from the rates just set ────
    #
    # Python rather than SQL: the grammar (net-of-base, VAT-inclusive, the
    # member gate, the cash waiver, a branch override) is a handful of branches
    # that read plainly here and would be an unreadable nest of CASE in SQL. The
    # loop is over a few dozen rows, once. It mirrors `order_fees` exactly.
    bind = op.get_bind()
    couriers = {
        row.code: row
        for row in bind.execute(
            sa.text(
                """
                SELECT id, code, commission_percent, commission_fixed,
                       payment_fee_percent, payment_fee_fixed,
                       commission_vat_inclusive, payment_fee_vat_inclusive,
                       commission_fixed_net_of_base, payment_fee_cash_exempt,
                       commission_fixed_requires_member
                  FROM couriers
                 WHERE is_aggregator IS TRUE
                """
            )
        )
    }
    overrides = {
        (row.courier_id, row.branch_id): row
        for row in bind.execute(
            sa.text(
                """
                SELECT courier_id, branch_id, commission_percent, commission_fixed,
                       payment_fee_percent, payment_fee_fixed
                  FROM courier_branch_rate
                """
            )
        )
    }
    orders = bind.execute(
        sa.text(
            """
            SELECT id, total, aggregator_channel, branch_id,
                   aggregator_payment_type, aggregator_customer_is_member
              FROM orders
             WHERE source = 'aggregator'
            """
        )
    ).all()

    for o in orders:
        code = _code_for_channel(o.aggregator_channel)
        courier = couriers.get(code) if code else None
        if courier is None:
            continue
        override = overrides.get((courier.id, o.branch_id))
        charged = Decimal(str(o.total or 0))
        agg_fee = _commission(charged, o, courier, override)
        pay_fee = _payment_fee(charged, o, courier, override)
        bind.execute(
            sa.text(
                "UPDATE orders SET aggregator_fee = :a, payment_fee = :p WHERE id = :id"
            ),
            {
                "a": agg_fee,
                "p": pay_fee,
                "id": o.id,
            },
        )


def _pick(override, courier, field):
    """The override's value for `field` if it set one, else the courier's."""
    if override is not None:
        value = getattr(override, field)
        if value is not None:
            return value
    return getattr(courier, field)


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _commission(charged, order, courier, override):
    percent = _pick(override, courier, "commission_percent")
    fixed = _pick(override, courier, "commission_fixed")
    if (
        courier.commission_fixed_requires_member
        and not order.aggregator_customer_is_member
    ):
        fixed = None
    if percent is None and fixed is None:
        return None
    if (
        courier.commission_fixed_net_of_base
        and percent is not None
        and fixed is not None
    ):
        before = _dec(fixed) + (_dec(percent) / 100) * (charged - _dec(fixed))
    else:
        before = charged * (_dec(percent) / 100) + _dec(fixed)
    return _money(before if courier.commission_vat_inclusive else before * (1 + _VAT))


def _payment_fee(charged, order, courier, override):
    if courier.payment_fee_cash_exempt and order.aggregator_payment_type == "postpaid":
        return Decimal("0.00")
    percent = _pick(override, courier, "payment_fee_percent")
    fixed = _pick(override, courier, "payment_fee_fixed")
    if percent is None and fixed is None:
        return None
    before = charged * (_dec(percent) / 100) + _dec(fixed)
    return _money(before if courier.payment_fee_vat_inclusive else before * (1 + _VAT))


def downgrade() -> None:
    # Return the four channels this migration seeded to their pre-migration null
    # state — but only where they still hold *exactly* what it wrote, so a rate a
    # human has since edited in the console is left untouched. Without this a bare
    # 25/30 would survive the drop of its grammar flags, and a re-upgrade (whose
    # seed guards on `IS NULL`) would then read Keeta's VAT-inclusive rate as a
    # plain before-VAT one — a silent money error born of an up/down/up cycle.
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = NULL, commission_fixed = NULL,
               payment_fee_percent = NULL
         WHERE code = 'keeta'
           AND commission_percent = 25.00 AND commission_fixed = 4.00
           AND payment_fee_percent = 2.00
        """
    )
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = NULL, commission_fixed = NULL,
               payment_fee_percent = NULL
         WHERE code = 'careem'
           AND commission_percent = 25.00 AND commission_fixed = 4.00
           AND payment_fee_percent = 2.00
        """
    )
    op.execute(
        """
        UPDATE couriers
           SET commission_percent = NULL, commission_fixed = NULL,
               payment_fee_percent = NULL
         WHERE code = 'talabat'
           AND commission_percent = 30.00 AND commission_fixed = 4.00
           AND payment_fee_percent = 2.00
        """
    )
    op.execute(
        """
        UPDATE couriers
           SET payment_fee_percent = NULL
         WHERE code = 'deliveroo' AND payment_fee_percent = 0.00
        """
    )

    op.drop_index("ix_courier_branch_rate_branch_id", table_name="courier_branch_rate")
    op.drop_index("ix_courier_branch_rate_courier_id", table_name="courier_branch_rate")
    op.drop_table("courier_branch_rate")
    op.drop_column("orders", "aggregator_customer_is_member")
    op.drop_column("orders", "aggregator_payment_type")
    op.drop_column("couriers", "commission_fixed_requires_member")
    op.drop_column("couriers", "payment_fee_cash_exempt")
    op.drop_column("couriers", "commission_fixed_net_of_base")
    op.drop_column("couriers", "payment_fee_vat_inclusive")
    op.drop_column("couriers", "commission_vat_inclusive")
