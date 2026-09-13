from __future__ import annotations

import enum

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class UnbatchedPromiseEnum(str, enum.Enum):
    """
    What a courier promises for an order.

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
    A carrier, and the thing about it that decides a delivery promise.

    The provider was already a value on the polygon (`FulfilmentProviderEnum`).
    What it was not was *configurable*: "noon Send means an hour" lived in a
    module constant applied to every courier alike. That is a commercial fact
    that changes without the code changing — a courier renegotiates, a new one
    arrives, an SLA moves — and it was only changeable by a deploy.

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

    #: What this courier promises for an order.
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

    @property
    def promises_next_day(self) -> bool:
        return self.unbatched_promise_kind == UnbatchedPromiseEnum.NEXT_DAY.value

    def __repr__(self) -> str:
        return f"<Courier {self.code}>"
