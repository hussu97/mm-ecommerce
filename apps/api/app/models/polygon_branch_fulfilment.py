from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin
from .delivery_polygon import FulfilmentProviderEnum

#: The set of providers a `fulfilment_provider` column may hold, as a SQL CHECK
#: expression. Mirrors `FulfilmentProviderEnum` in text rather than a native PG
#: enum (CLAUDE.md rule 6): adding a courier is an insert everywhere else, and a
#: CHECK is the one place that has to be widened, in a migration.
_PROVIDER_CHECK = "fulfilment_provider IN ({})".format(
    ", ".join(f"'{p.value}'" for p in FulfilmentProviderEnum)
)


class PolygonBranchFulfilment(Base, UUIDMixin):
    """
    One branch's standing in a zone: its rank, its courier, its escapes.

    A zone used to name exactly one kitchen (`delivery_polygons.branch_id`) and
    one courier (`fulfilment_provider`). That is still true of the *preferred*
    branch — those columns are kept as the rank-1 mirror — but a zone now carries
    an ordered list of branches that can serve it, and the checkout walks that
    list in `rank` order and gives the order to the first branch that can make
    the whole basket.

    Everything a zone charges is unchanged and stays on `delivery_polygons`: the
    fee, the free-delivery threshold, and the geometry are the same whichever
    branch bakes. What differs per branch is only *who carries it* — the courier
    and its alternates — and therefore the time estimate, which was already
    courier-level. Those three live here.

    Owned by the per-version polygon row (`ondelete=CASCADE`): a map version is
    drafted by cloning its polygons, and these rows clone and delete with the
    polygon they belong to. A branch, by contrast, is `RESTRICT`ed — a branch
    with live assignments is a branch taking orders, and deleting it should fail
    loudly rather than orphan the map, exactly as `delivery_polygons.branch_id`
    already does.
    """

    __tablename__ = "polygon_branch_fulfilment"

    polygon_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_polygons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: 1 is preferred; the checkout walks assignments ascending and takes the
    #: first branch that can make the whole basket. Dense and unique per polygon
    #: (see the unique constraint) so "the next branch to try" is unambiguous.
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The courier this branch uses to serve this zone — priced and dispatched
    #: against, exactly like `delivery_polygons.fulfilment_provider` was, but now
    #: chosen per branch because the same pin costs a different run from a
    #: different kitchen. Third party is the safe default: "do what we always
    #: did", so a row added without thinking about dispatch books nothing real.
    fulfilment_provider: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=FulfilmentProviderEnum.THIRD_PARTY.value,
        server_default=FulfilmentProviderEnum.THIRD_PARTY.value,
    )
    #: The couriers an order this branch fulfils may be *moved* to when its
    #: preferred one will not carry it — the same per-zone escape list as
    #: `delivery_polygons.alternate_providers`, now per (zone, branch) because the
    #: plausible escapes depend on which kitchen the run leaves from. JSONB rather
    #: than a join table so it clones with the row when a map version is drafted.
    alternate_providers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    polygon = relationship("DeliveryPolygon", back_populates="branch_fulfilments")

    __table_args__ = (
        # A branch appears at most once in a zone.
        UniqueConstraint(
            "polygon_id", "branch_id", name="uq_polygon_branch_fulfilment_branch"
        ),
        # Ranks are unique within a zone, so "rank N" names one branch.
        UniqueConstraint(
            "polygon_id", "rank", name="uq_polygon_branch_fulfilment_rank"
        ),
        CheckConstraint(_PROVIDER_CHECK, name="ck_polygon_branch_fulfilment_provider"),
        CheckConstraint("rank >= 1", name="ck_polygon_branch_fulfilment_rank_positive"),
    )

    def __repr__(self) -> str:
        return (
            f"<PolygonBranchFulfilment rank={self.rank} "
            f"branch={self.branch_id} via {self.fulfilment_provider}>"
        )
