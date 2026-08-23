from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, status_vocabulary

if TYPE_CHECKING:
    from .branch import Branch


class DeviceTypeEnum(str, enum.Enum):
    CASHIER = "cashier"
    SUB_CASHIER = "sub_cashier"
    DISPLAY = "display"  # customer-facing display
    NOTIFIER = "notifier"  # order-ready caller screen


class DeviceStatusEnum(str, enum.Enum):
    AVAILABLE = "available"  # created, waiting to be paired
    USED = "used"  # paired to a physical device
    DISABLED = "disabled"


class Device(Base, UUIDMixin, TimestampMixin):
    """
    A registered terminal. An iPad running the POS app pairs itself to a Device
    row using the one-time `pairing_code`; from then on it authenticates with
    `token_hash` and every order it creates carries its `device_id`.
    """

    __tablename__ = "devices"

    #: Mirrors `124_device_build_platform`. The two app stores, spelled out —
    #: not a lifecycle, so not `status_vocabulary`, and adding a third should be
    #: a deliberate migration rather than an enum member nobody notices.
    __table_args__ = (
        CheckConstraint(
            "platform IS NULL OR platform IN ('ios', 'android')",
            name="ck_devices_platform_allowed",
        ),
        # Migration 138.
        status_vocabulary("devices", "status", DeviceStatusEnum),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=DeviceStatusEnum.AVAILABLE.value
    )

    # Pairing. `pairing_code` is short-lived and single-use; `token_hash` is the
    # long-lived credential the app stores in the keychain.
    pairing_code: Mapped[str | None] = mapped_column(
        String(12), unique=True, nullable=True, index=True
    )
    pairing_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_hash: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )

    # Telemetry reported by the app
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: All four are refreshed by `authenticate_device` on the terminal's own
    #: requests, from the `X-App-*` headers. Before that they were written once
    #: by pairing and never again, so an iPad paired months ago reported the
    #: build it was set up with rather than the one it is running.
    #:
    #: Nullable throughout: a terminal on a build that predates those headers
    #: keeps working and simply says nothing, which is what makes the server
    #: safe to deploy ahead of the app.
    app_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: `CFBundleVersion` on iOS, `versionCode` on Android. Text, because iOS
    #: permits `36.1` and CI produces those — see the migration.
    build_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: `ios` or `android`. Constrained by `__table_args__` above.
    platform: Mapped[str | None] = mapped_column(String(10), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model_identifier: Mapped[str | None] = mapped_column(String(60), nullable=True)

    #: Take the branch's online orders without waiting for somebody to press
    #: Accept.
    #:
    #: For a kitchen-only site with nobody watching the iPad, the press carries
    #: no information — nobody is deciding whether to take an order that is
    #: already paid for, they are being interrupted to confirm one. With this on
    #: the terminal accepts and prints by itself.
    #:
    #: **Which terminals see an order is not a per-device question.** It used to
    #: have a `receives_online_orders` column beside this one, distinguishing
    #: *show me the branch's online orders* from *wait for a human before taking
    #: one*. Nothing ever read it — no query, no service, no line of Swift — so a
    #: terminal switched off received them anyway, and the setting quietly lied
    #: for as long as it existed. The shop's answer is that the branch decides
    #: (`branches.receives_online_orders`) and every terminal at that branch
    #: sees the order; this flag remains, because *who presses Accept* really is
    #: a property of the individual iPad on the counter.
    auto_accept_online_orders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    category_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    branch: Mapped[Branch] = relationship("Branch", back_populates="devices")
    printers: Mapped[list[Printer]] = relationship(
        "Printer", back_populates="device", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Device {self.reference} ({self.type})>"


class PrinterConnectionEnum(str, enum.Enum):
    LAN = "lan"  # ESC/POS over TCP:9100
    BLUETOOTH = "bluetooth"  # ESC/POS over BLE / MFi
    USB = "usb"  # ESC/POS over Lightning/USB-C
    AIRPRINT = "airprint"  # rendered PDF via iOS printing
    CLOUD = "cloud"  # server-side spooled


class PrinterRoleEnum(str, enum.Enum):
    RECEIPT = "receipt"
    KITCHEN = "kitchen"
    LABEL = "label"
    REPORT = "report"


class Printer(Base, UUIDMixin, TimestampMixin):
    """
    A physical printer reachable from a device (or from the server, for cloud
    printers). Receipt printers also drive the cash drawer kick — the drawer is
    wired to the printer's RJ-11 port, so `has_cash_drawer` lives here.
    """

    __tablename__ = "printers"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=PrinterRoleEnum.RECEIPT.value
    )
    connection: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=PrinterConnectionEnum.LAN.value
    )

    # LAN
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="9100")
    # Bluetooth / USB
    identifier: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # ESC/POS formatting
    paper_width_mm: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="80"
    )
    characters_per_line: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="48"
    )
    codepage: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="cp864"
    )
    supports_arabic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    cut_after_print: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    has_cash_drawer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    copies: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Kitchen printers only print items routed to their station.
    kitchen_flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kitchen_flows.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    device: Mapped[Device | None] = relationship("Device", back_populates="printers")

    def __repr__(self) -> str:
        return f"<Printer {self.name} ({self.role}/{self.connection})>"
