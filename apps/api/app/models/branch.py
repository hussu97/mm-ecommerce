from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, business_date_format

if TYPE_CHECKING:
    from .aggregator import AggregatorBranchMap, FoodicsBranchMap
    from .device import Device
    from .grubops import GrubOpsLocationMap
    from .pos_table import Section
    from .tax import TaxGroup


class BranchTypeEnum(str, enum.Enum):
    """What a branch physically is. Mirrors Foodics `branch.type`."""

    RESTAURANT = "restaurant"
    KITCHEN = "kitchen"
    WAREHOUSE = "warehouse"


class Branch(Base, UUIDMixin, TimestampMixin):
    """
    A physical location — shop, production kitchen or warehouse.

    Almost every operational record in the POS domain (orders, tills, stock levels,
    devices, staff assignments) hangs off a branch.
    """

    __tablename__ = "branches"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=BranchTypeEnum.RESTAURANT.value
    )

    # Location & contact
    latitude: Mapped[Any | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Any | None] = mapped_column(Numeric(10, 7), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The city the branch is in, as a customer would name it — "Sharjah",
    #: "Dubai". Its own column rather than something parsed out of `address`:
    #: the address is one free-text line written for a driver, and the last
    #: comma-separated fragment of it is a guess, not a city.
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Arabic address and city, shown to a customer reading the site in Arabic.
    #: Alongside `name_localized` rather than folded into `translations`,
    #: because that is where the branch's Arabic name already lives and one
    #: shape for all three is easier to fill in than two.
    address_localized: Mapped[str | None] = mapped_column(Text, nullable=True)
    city_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Trading hours. `opening_from`/`opening_to` are "HH:MM" strings so a branch can
    # trade past midnight (e.g. 09:00 → 02:00) without date arithmetic.
    opening_from: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="00:00"
    )
    opening_to: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="23:59"
    )
    # Cut-off that rolls the trading day over — orders before it belong to the
    # previous business_date. Also used as the inventory end-of-day.
    business_day_start: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="04:00"
    )
    inventory_end_of_day_time: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="04:00"
    )

    # Receipt overrides — fall back to BusinessSettings when null.
    receipt_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_footer: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tax identity (printed on the invoice, required for UAE FTA compliance)
    tax_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tax_registration_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Courier identity. A branch is a pickup location as far as a courier is
    # concerned, so which outlet a courier collects from belongs here rather
    # than in the environment — the same place its coordinates and phone number
    # already live. Lalamove needs nothing but those two; noon Send additionally
    # requires the branch to be registered with them, and calls the result an
    # outlet code.
    #
    # Null means this branch cannot dispatch through noon Send. Register it with
    # `python -m scripts.register_noon_send_pickup --create --branch <ref>`,
    # which writes the code it gets back into this column.
    noon_send_outlet_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: The outlet's exact address revision — `addr::restaurant_outlet::ae::X::2`.
    #: Optional on noon's side and optional here: the outlet code alone
    #: identifies where a rider collects, and this is a nicety that pins the
    #: address revision with it. Longer than the code because it carries a
    #: registry, a country and a revision as well.
    noon_send_outlet_address_code: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )

    # Capabilities
    receives_online_orders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: Whether this branch handles cash — i.e. has a physical till drawer.
    #:
    #: A production kitchen that only ever fulfils aggregator and website orders
    #: never opens a drawer, so making its cashier count one at every shift open
    #: and close reconciles nothing and puts a phantom float on the books. When
    #: false the register skips the opening-float entry and the close-time cash
    #: count, and the till-close report drops its cash-reconciliation lines. The
    #: channel revenue summary is printed either way.
    cash_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: Whether a customer may choose to collect from here.
    #:
    #: Separate from `receives_online_orders`, which says the branch bakes
    #: website orders — a production kitchen can do that without being somewhere
    #: we would send a customer with a cake box and no counter. Inferring one
    #: from the other would put every new kitchen on the checkout the day it
    #: starts taking orders, which is not a decision that should happen by
    #: accident.
    offers_pickup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", index=True
    )
    accepts_reservations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    reservation_duration: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="60"
    )
    reservation_times: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", index=True
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: The IANA zone this branch trades in. Every branch is Asia/Dubai today and
    #: `core/trading_hours` long assumed it as a global constant; carried per
    #: branch so a future shop in another city reads its "HH:MM" hours in the
    #: right zone rather than Dubai's.
    timezone: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="Asia/Dubai"
    )

    # Relationships
    tax_group: Mapped[TaxGroup | None] = relationship("TaxGroup")
    #: Integration capability, expressed as mapping rows rather than boolean
    #: columns (the idiom `grubops_location_map` established): a branch is *on* a
    #: system iff it has an active row. Read-only here — the rows are written by
    #: the grubops/aggregator services — and `selectin` so the derived flags
    #: below are safe to read on any loaded branch under the async session.
    aggregator_maps: Mapped[list[AggregatorBranchMap]] = relationship(
        "AggregatorBranchMap", lazy="selectin", viewonly=True
    )
    foodics_map: Mapped[FoodicsBranchMap | None] = relationship(
        "FoodicsBranchMap", lazy="selectin", viewonly=True, uselist=False
    )
    grubops_location: Mapped[GrubOpsLocationMap | None] = relationship(
        "GrubOpsLocationMap", lazy="selectin", viewonly=True, uselist=False
    )
    devices: Mapped[list[Device]] = relationship(
        "Device", back_populates="branch", cascade="all, delete-orphan"
    )
    sections: Mapped[list[Section]] = relationship(
        "Section", back_populates="branch", cascade="all, delete-orphan"
    )
    business_days: Mapped[list[BranchBusinessDay]] = relationship(
        "BranchBusinessDay", back_populates="branch", cascade="all, delete-orphan"
    )
    holidays: Mapped[list[BranchHoliday]] = relationship(
        "BranchHoliday",
        back_populates="branch",
        cascade="all, delete-orphan",
        order_by="BranchHoliday.holiday_date",
    )
    #: The canonical per-day marketplace schedule (source of truth for the
    #: catalog-&-hours sync), distinct from the single `opening_from`/`opening_to`
    #: storefront window above. Empty until an operator fills it in.
    weekly_hours: Mapped[list[BranchWeeklyHours]] = relationship(
        "BranchWeeklyHours",
        back_populates="branch",
        cascade="all, delete-orphan",
        order_by="BranchWeeklyHours.weekday, BranchWeeklyHours.shift_index",
    )

    def _translated(self, field: str, locale: str) -> str | None:
        """
        One field of this branch in one locale, or None.

        `translations` is `{locale: {field: value}}` — the shape every other
        translatable model in the codebase uses, and the shape the storefront's
        `localizedField` reads. This used to index it one level deep and treat
        the result as a string, so it silently returned nothing for every branch
        that had translations at all.
        """
        if not isinstance(self.translations, dict):
            return None
        fields = self.translations.get(locale)
        if not isinstance(fields, dict):
            return None
        value = fields.get(field)
        return str(value) if value else None

    def name_for(self, locale: str = "en") -> str:
        if locale == "en":
            return self.name
        return self._translated("name", locale) or self.name_localized or self.name

    def address_for(self, locale: str = "en") -> str | None:
        if locale == "en":
            return self.address
        return (
            self._translated("address", locale)
            or self.address_localized
            or (self.address)
        )

    def city_for(self, locale: str = "en") -> str | None:
        if locale == "en":
            return self.city
        return self._translated("city", locale) or self.city_localized or self.city

    @property
    def maps_url(self) -> str | None:
        """
        A Google Maps link to this branch's own pin.

        Built from the coordinates rather than the address: a search by address
        text lands on whatever Google decides that string means, which for a
        shop inside a tower is regularly the tower's other entrance. `?api=1`
        with a `query` of `lat,lng` is the documented, app-opening form.
        """
        if self.latitude is None or self.longitude is None:
            return None
        return (
            "https://www.google.com/maps/search/?api=1"
            f"&query={self.latitude},{self.longitude}"
        )

    @property
    def has_grubops(self) -> bool:
        """On GrubOps iff there is an active location map — the OOS/order path."""
        return self.grubops_location is not None and self.grubops_location.is_active

    @property
    def has_foodics(self) -> bool:
        """Has a Foodics POS behind it iff there is an active branch map."""
        return self.foodics_map is not None and self.foodics_map.is_active

    @property
    def aggregators(self) -> list[str]:
        """The channels this branch trades on, active rows only, sorted."""
        return sorted(m.channel for m in self.aggregator_maps if m.is_active)

    def __repr__(self) -> str:
        return f"<Branch {self.reference} {self.name}>"


class BranchHoliday(Base, UUIDMixin, TimestampMixin):
    """
    A whole day this branch does not trade.

    **Exceptions only.** The shop works seven days a week, so there is no
    weekday rule to express and deliberately none here: a Saturday is a trading
    day, and the only thing that closes a branch is a row somebody wrote. That
    also means the absence of rows is the ordinary state and costs nothing —
    the same shape `branch_products` uses for "sold here".

    **Whole days only, and that is a design decision rather than a first cut.**
    A branch already has `opening_from` / `opening_to` for the hours it trades;
    giving a holiday its own hours would be a second answer to that question,
    free to disagree with the first. A half-day is a trading-hours change. A
    holiday is the day being gone.

    Dated as `YYYY-MM-DD` text rather than a `Date`, matching `business_date`
    on the table above — the two are read side by side, and one shape for a
    date in this schema beats two. The CHECK holds the format.

    Read by `services/branch_holiday_service`, which hands the set to
    `core/trading_hours`. Nothing else compares these strings by hand.
    """

    __tablename__ = "branch_holidays"
    __table_args__ = (
        UniqueConstraint("branch_id", "holiday_date", name="uq_branch_holiday_date"),
        # Migration 106. Format only, mirroring `business_date_format` — these
        # dates are compared against `date.isoformat()` output, and a row that
        # is not that shape silently never matches.
        CheckConstraint(
            r"holiday_date ~ '^\d{4}-\d{2}-\d{2}$'",
            name="ck_branch_holidays_holiday_date_format",
        ),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    holiday_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    #: What the shop calls it — "Eid al-Fitr", "Annual maintenance". Shown in
    #: the admin and written into the promise's `reason`, so "why is this
    #: quoting Thursday" has an answer without opening the table.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Anything the shop wants to remember about it. Never shown to a customer.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    branch: Mapped[Branch] = relationship("Branch", back_populates="holidays")

    def __repr__(self) -> str:
        return f"<BranchHoliday {self.holiday_date} {self.name}>"


class BranchWeeklyHours(Base, UUIDMixin, TimestampMixin):
    """One open shift, on one weekday. **One shift per day.**

    The **canonical per-weekday schedule** and the single source of truth for
    every "is the branch open" decision. Each weekday has at most one row — one
    open + one close — and a weekday with no row is **closed** (the same
    "absence is the ordinary state" idiom `branch_holidays` uses). One shift per
    day is deliberate: Melting Moments trades one continuous shift a day, and a
    second shift would be a second answer to "when does it open". `weekday` is
    0=Sunday…6=Saturday (the UAE week and the order every portal lists days in).

    `Branch.opening_from`/`opening_to` is now a **derived cache** of *today's*
    shift, stamped daily by `branch_hours_service` / the branch-hours cron so the
    storefront and any reader still on the single window stay correct. This table
    is what business logic and the marketplace fan-out both read.

    Times are "HH:MM" strings like the branch window, so a shift can cross
    midnight without date arithmetic; the CHECK holds the shape. `shift_index` is
    vestigial (always 0) — kept from the multi-shift scaffold so old INSERTs that
    name it still work; uniqueness is now `(branch_id, weekday)` (migration 173).
    """

    __tablename__ = "branch_weekly_hours"
    __table_args__ = (
        UniqueConstraint("branch_id", "weekday", name="uq_branch_weekly_hours_day"),
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6", name="ck_branch_weekly_hours_weekday"
        ),
        CheckConstraint("shift_index >= 0", name="ck_branch_weekly_hours_shift_index"),
        CheckConstraint(
            r"opens ~ '^\d{2}:\d{2}$' AND closes ~ '^\d{2}:\d{2}$'",
            name="ck_branch_weekly_hours_time_format",
        ),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: 0=Sunday … 6=Saturday.
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 0-based ordinal within the day, so two shifts on one weekday are two rows.
    shift_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    opens: Mapped[str] = mapped_column(String(5), nullable=False)
    closes: Mapped[str] = mapped_column(String(5), nullable=False)

    branch: Mapped[Branch] = relationship("Branch", back_populates="weekly_hours")

    def __repr__(self) -> str:
        return f"<BranchWeeklyHours {self.branch_id} d{self.weekday} {self.opens}-{self.closes}>"


class BranchBusinessDay(Base, UUIDMixin, TimestampMixin):
    """
    One trading day per branch. Opened when the first till of the day opens and
    closed by "end of day"; every order, till and drawer operation is stamped with
    its `business_date` so reporting is not distorted by post-midnight trading.
    """

    __tablename__ = "branch_business_days"
    __table_args__ = (
        UniqueConstraint("branch_id", "business_date", name="uq_branch_business_date"),
        # Migration 100: everything stamped with a business_date joins back to
        # this table through exact string equality.
        business_date_format("branch_business_days"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opened_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Z-report totals, frozen at end of day.
    total_sales: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    total_orders: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_discounts: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    total_returns: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    total_taxes: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )

    branch: Mapped[Branch] = relationship("Branch", back_populates="business_days")

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def __repr__(self) -> str:
        return f"<BranchBusinessDay {self.branch_id} {self.business_date}>"


DEFAULT_OPENING_AMOUNT = Decimal("0.00")
