from __future__ import annotations

import enum

from sqlalchemy import Boolean, Integer, String
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

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    @property
    def promises_next_day(self) -> bool:
        return self.unbatched_promise_kind == UnbatchedPromiseEnum.NEXT_DAY.value

    def __repr__(self) -> str:
        return f"<Courier {self.code}>"
