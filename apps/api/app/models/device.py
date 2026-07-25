from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .branch import Branch


class DeviceTypeEnum(str, enum.Enum):
    CASHIER = "cashier"
    SUB_CASHIER = "sub_cashier"
    KDS = "kds"
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
    app_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model_identifier: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # KDS/display devices only serve the categories listed here (empty = all).
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
