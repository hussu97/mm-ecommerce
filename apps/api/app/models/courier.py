from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class UnbatchedPromiseEnum(str, enum.Enum):
    """
    What a courier promises when an order is not waiting for a shared run.

    Two shapes, because there are two kinds of knowledge. A courier we dispatch
    ourselves leaves when we say so, and the honest answer is a number of
    minutes from the moment the order is ready. A courier that collects on its
    own schedule is one we cannot see, and the only thing we can commit to is a
    day.
    """

    #: `unbatched_promise_minutes` from the moment the order can be worked on.
    MINUTES = "minutes"
    #: The next day the shop is trading. No hour, because it is not ours to name.
    NEXT_DAY = "next_day"


class Courier(Base, UUIDMixin, TimestampMixin):
    """
    A carrier, and the two things about it that decide a delivery promise.

    The provider was already a value on the polygon (`FulfilmentProviderEnum`).
    What it was not was *configurable*: "only Lalamove batches" lived in a
    property called `is_batched` that returned `is_lalamove`, and "noon Send
    means an hour" lived in a module constant applied to every courier alike.
    Both are commercial facts that change without the code changing — a courier
    renegotiates, a new one arrives, an SLA moves — and both were only
    changeable by a deploy.

    This table does not replace the enum. `delivery_polygons.fulfilment_provider`
    still holds the code, and `code` here is the same string. It hangs the
    settings off it.
    """

    __tablename__ = "couriers"

    #: Matches `FulfilmentProviderEnum`. The join key for everything else.
    code: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)

    #: Whether orders on this courier can wait for a shared run.
    #:
    #: Only Lalamove, today. A multi-drop Lalamove booking is one order with up
    #: to fifteen stops; noon Send's equivalent is a different product with a cap
    #: of three that we do not use, and a third party is collected on a schedule
    #: we cannot see. A batch group may only be attached to a courier where this
    #: is true — otherwise the schedule is a promise nothing can keep.
    supports_batching: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: What this courier promises when there is no batch to wait for — either
    #: because the polygon has no group, or because this courier cannot batch.
    unbatched_promise_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UnbatchedPromiseEnum.NEXT_DAY.value,
        server_default=UnbatchedPromiseEnum.NEXT_DAY.value,
    )
    #: Minutes from ready to door. Read only when the kind is `minutes`; null
    #: otherwise rather than a plausible number nothing uses.
    unbatched_promise_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    #: Days from the shop handing it over to the door. Read only when the kind
    #: is `next_day`.
    #:
    #: One is "tomorrow", which is what this rule always meant and therefore the
    #: default. It is a column because "next day" is a courier's *current* SLA
    #: and not a law: a partner covering Al Ain quotes two days, and moving that
    #: number used to be a deploy. Counted in calendar days from the day the
    #: kitchen can work on the order — the courier's van is not ours, so its
    #: transit does not pause for our holidays. What the holidays do move is the
    #: day we hand it over, and that is `days`' starting point rather than
    #: `days` itself.
    unbatched_promise_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    #: A public logo for this carrier, served from the same R2 bucket product
    #: images use (``.../couriers/{code}.png``). Null until seeded. The URL is a
    #: convention a frontend can rebuild from ``code`` alone; the column is the
    #: editable source of truth.
    logo_url: Mapped[str | None] = mapped_column(String(300), nullable=True)

    #: True for the marketplace channels (Talabat, Keeta, Noon Food, Deliveroo,
    #: Careem) — couriers only in the sense of who carries the bag. MM dispatches
    #: none of them, so they must never be offered as a fulfilment target; the
    #: table is read only by ``code`` (never enumerated for targets), so this is
    #: a label, not a gate, but it keeps the two kinds legible.
    is_aggregator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: What this carrier takes off an order, as a **percentage** — `25.00` is
    #: 25%. Quoted before VAT, because that is how every one of these contracts
    #: is written and how the invoice arrives; the 5% is added by
    #: `order_fees.compute`, exactly as it already is for a card processor's fee.
    #:
    #: Only ever set on an aggregator row: MM pays a marketplace a share of the
    #: basket, and pays a dispatch courier a booking fee that is quoted per run
    #: and recorded on `order_deliveries.cost_total` instead. A dispatch courier
    #: with a commission here would be counted twice.
    #:
    #: **Null means "we do not know", not "zero".** Only Noon Food's rates are
    #: agreed today; the rest are null until somebody supplies them, and a null
    #: leaves the order's fee null so the screens say "not itemised" rather than
    #: flattering the margin with a fee of nothing. See the `_percent` suffix
    #: convention on `Order.vat_rate`: anything named `_percent` gets a `/ 100`.
    commission_percent: Mapped[Any | None] = mapped_column(Numeric(5, 2), nullable=True)

    #: A flat amount the same carrier takes **on top of** `commission_percent`,
    #: in the order's currency and again before VAT. Several of these contracts
    #: are quoted as "25% plus two dirhams a order", and a percentage-only
    #: column silently dropped the second half of that sentence.
    #:
    #: Null and zero mean the same thing here and that is safe, because the
    #: pair is read together: a fee is "not known" only when *both* parts are
    #: null. One part set and the other null reads as "that part is nothing",
    #: which is what a contract quoting only a percentage actually says.
    commission_fixed: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)

    #: What this carrier's payment processing takes, as a **percentage**, on top
    #: of the commission. Same conventions as `commission_percent`: before VAT,
    #: null for unknown.
    #:
    #: A marketplace collects the customer's card itself and bills the merchant
    #: for having done so, so this is the aggregator's analogue of
    #: `payment_gateways.fee_percent` — and it lands in the same place on the
    #: order (`orders.payment_fee`) and on the same line of the same screen. One
    #: idea, one column, whoever took the card.
    payment_fee_percent: Mapped[Any | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    #: The flat half of the payment fee, before VAT — the direct analogue of
    #: `payment_gateways.fee_fixed`, which is exactly how a card processor's fee
    #: has always been quoted here ("2.9% + AED 1").
    payment_fee_fixed: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)

    # ── How the two rates above are *read* ───────────────────────────────────
    #
    # Everything above is a number; the flags below are the grammar for turning
    # it into a fee. They exist because the marketplaces do not all quote the
    # same sentence: Noon and Careem bill "25% + VAT", Keeta bills a figure that
    # already has the VAT inside it, and the flat part means different things to
    # different contracts. Each flag defaults to the plain reading
    # (`commission_percent`% of the basket, plus `commission_fixed`, plus 5%),
    # so a courier that says nothing special behaves exactly as it did before
    # these columns existed — Noon Food's numbers do not move.

    #: The commission is already **VAT-inclusive**; do not gross it up by 5%.
    #: True for Keeta, whose contract is quoted with the tax inside the rate
    #: ("VAT is included in the 25%"). False everywhere else, where the rate is
    #: before VAT and `order_fees` adds the 5% — the reading every existing row
    #: was written under.
    commission_vat_inclusive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: The payment fee is already **VAT-inclusive**. True for Keeta ("2% payment
    #: charge incl. VAT"); false for the contracts quoted as "2% + VAT".
    payment_fee_vat_inclusive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: The flat part is **netted out of the basket before the percentage**, i.e.
    #: `commission_fixed + commission_percent% × (basket − commission_fixed)`
    #: rather than the usual `commission_percent% × basket + commission_fixed`.
    #: True for Keeta alone, whose contract is written "4 AED + 25% of (the item
    #: value − the original 4 AED)". False everywhere else, where the flat part
    #: simply adds on top.
    commission_fixed_net_of_base: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: The payment fee is **not charged on a cash order**. True for Careem,
    #: whose 2% applies only when the customer paid the marketplace by card
    #: ("non-cash"). A cash order took no card, so it pays no card fee — the same
    #: reasoning `order_fees._own_channel_fees` already applies to a counter
    #: sale. False for Talabat, which bills the 2% on every order regardless.
    #: Read against `orders.aggregator_payment_type` (`postpaid` = cash).
    payment_fee_cash_exempt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: The flat part is charged **only to a loyalty/subscription customer** —
    #: Careem Plus, Talabat Pro/VIP. True for both; false everywhere else.
    #:
    #: Load-bearing and, today, dormant. GrubOps sends no flag that tells a Pro
    #: order from an ordinary one (checked across every payload we hold), so
    #: `orders.aggregator_customer_is_member` is never set true and this fee
    #: never actually applies. The rule is modelled in full so that the day a
    #: signal arrives — a GrubOps field, or a human toggling the order — the
    #: 4 AED starts landing on exactly the members it should, with no schema
    #: change. Until then, seeding it does not over-charge a single non-member.
    commission_fixed_requires_member: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    @property
    def promises_next_day(self) -> bool:
        return self.unbatched_promise_kind == UnbatchedPromiseEnum.NEXT_DAY.value

    def __repr__(self) -> str:
        return f"<Courier {self.code}>"
