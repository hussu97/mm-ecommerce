"""
Operational entities that close the last gaps against Foodics:
transfer orders, inventory spot checks, reservations and notification rules.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    TimestampMixin,
    UUIDMixin,
    business_date_format,
    status_vocabulary,
)


class TransferKindEnum(str, enum.Enum):
    """What a transfer order is for.

    A ``return`` is a transfer to a branch's mapped return branch (goods sent
    back — expired, damaged, surplus). It reuses the whole transfer machinery,
    differing only in how the destination is chosen and that each line carries a
    reason. Keeping them in one table means the ledger, the two-leg send/receive
    and the variance handling are written once.
    """

    TRANSFER = "transfer"
    RETURN = "return"


class TransferStatusEnum(str, enum.Enum):
    """The lifecycle of one *child* transfer — a single source→destination leg of
    a transfer order.

    Admin creates the order and every child is ``pending`` with no stock moved.
    The source branch's till ships one destination at a time: marking a child
    sent posts the ``TRANSFER_SEND`` and moves it to ``sent`` (in transit). The
    destination books it in and it becomes ``closed``. ``cancelled`` is a child
    voided before it shipped.

    Movement still lives in the two link columns, not the status: ``sent`` is set
    the instant ``sent_transaction_id`` is, and ``closed`` gates on
    ``received_transaction_id`` — the status column is the cheap projection of
    that composite for list filtering, the same way ``InventoryLevel`` projects
    the ledger.
    """

    PENDING = "pending"
    SENT = "sent"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TransferOrderStatusEnum(str, enum.Enum):
    """The *derived* status of a parent transfer order, rolled up from its
    children (see ``transfer_service._recompute_parent_status``). Never assigned
    directly by a request — recomputed on every child transition."""

    PENDING = "pending"
    PARTIALLY_SENT = "partially_sent"
    SENT = "sent"
    PARTIALLY_RECEIVED = "partially_received"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TransferOrder(Base, UUIDMixin, TimestampMixin):
    """One admin action: from a single source branch, a fan-out of stock to
    several other branches at once. It holds one child :class:`Transfer` per
    destination, so the sheet the admin fills (a quantity column per branch)
    totals across the whole order. Creating it moves no stock — each child is
    shipped from the source till later, one at a time.
    """

    __tablename__ = "transfer_orders"
    __table_args__ = (
        # Migration 228 — parent status is derived; the vocab is the backstop.
        status_vocabulary("transfer_orders", "status", TransferOrderStatusEnum),
        business_date_format("transfer_orders"),
        CheckConstraint(
            "kind IN ('transfer', 'return')", name="ck_transfer_orders_kind"
        ),
        # An admin create that retries after a lost response must not raise the
        # same fan-out twice. The client sends a stable id; a second create with
        # it is refused, and the service returns the order already made.
        Index(
            "uq_transfer_orders_client_request_id",
            "client_request_id",
            unique=True,
            postgresql_where=text("client_request_id IS NOT NULL"),
        ),
    )

    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TransferOrderStatusEnum.PENDING.value,
        index=True,
    )
    #: `transfer` (branch → branch) or `return` (branch → its return branch).
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TransferKindEnum.TRANSFER.value,
        index=True,
    )
    #: The single branch every child ships *from*, chosen in the admin's first step.
    source_branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    required_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Client-supplied idempotency token for an admin create, unique when set.
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The ``correction_group_id`` stamped on every shortfall top-up adjustment
    #: posted when the admin overrode on-hand at create time (see
    #: ``transfer_service.create_transfer_order``). Null when nothing was
    #: overridden; the mini stock-adjustment report reads the group by this id.
    adjustment_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Provenance — the transfer template this order was raised from and its
    #: immutable snapshot at raise time. Null for a return or an ad-hoc order.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transfer_templates.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    children: Mapped[list[Transfer]] = relationship(
        "Transfer",
        back_populates="transfer_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<TransferOrder {self.reference} {self.status}>"


class Transfer(Base, UUIDMixin, TimestampMixin):
    """One source→destination leg of a :class:`TransferOrder`. This is the object
    that actually moves stock, through the two-leg send/receive the ledger has
    always used; the parent is only the grouping the admin created it under.
    """

    __tablename__ = "transfers"
    __table_args__ = (
        status_vocabulary("transfers", "status", TransferStatusEnum),
        business_date_format("transfers"),
        CheckConstraint("kind IN ('transfer', 'return')", name="ck_transfers_kind"),
    )

    transfer_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transfer_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TransferStatusEnum.PENDING.value,
        index=True,
    )
    #: Denormalised from the parent so the POS lists (which query children
    #: directly) stay self-describing — the receive screen badges a return
    #: without a join. Copied at create; a child's kind never diverges.
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TransferKindEnum.TRANSFER.value,
        index=True,
    )
    #: Destination.
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Source (mirrors the parent — denormalised so the send leg needs no join).
    source_branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Set when the sending transaction is posted.
    sent_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    received_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: When the source till auto-printed this transfer's packing list — stamped
    #: once, the first time a till opens on the order's date, so a later till
    #: opening the same day does not reprint it. Null until then. The manual
    #: Print button is independent and never touches this.
    auto_printed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    transfer_order: Mapped[TransferOrder] = relationship(
        "TransferOrder", back_populates="children"
    )
    items: Mapped[list[TransferLine]] = relationship(
        "TransferLine",
        back_populates="transfer",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Transfer {self.reference} {self.status}>"


class TransferLine(Base, UUIDMixin):
    __tablename__ = "transfer_items"

    transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transfers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)
    #: Vestigial since there is no accept step — set equal to ``quantity`` at
    #: create so ``send``'s existing ``approved_quantity or quantity`` read is
    #: untouched. Kept for the ledger's immutable-history compatibility.
    approved_quantity: Mapped[Any | None] = mapped_column(Numeric(16, 4), nullable=True)
    sent_quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    received_quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    unit: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="storage"
    )
    conversion_factor: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="1"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: For a return, why the goods are going back (expiry / damaged / missing); for
    #: a transfer, the receiver's note when what arrived differs from what was sent.
    variance_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    transfer: Mapped[Transfer] = relationship("Transfer", back_populates="items")

    def __repr__(self) -> str:
        return f"<TransferLine item={self.item_id} qty={self.quantity}>"


class InventoryTransferTemplate(Base, UUIDMixin, TimestampMixin):
    """A saved list of items a branch typically transfers, so a cashier creating a
    transfer picks a template and fills quantities rather than searching the whole
    catalogue — the same idea as a shift-report template, at the sending branch.

    ``destination_branch_id`` is optional: a template can be for one destination or
    left open for the cashier to choose.

    Append-only versioned, exactly like a shift-report template. A template is
    identified to staff by its ``(source_branch_id, name)`` lineage, not by one
    immutable row: editing it inserts a new row at the next ``version_number`` and
    leaves the old revision as history. "Current" is the highest ``version_number``
    per lineage. A transfer raised from a template stamps a snapshot of it onto the
    ``TransferOrder``, so the order's provenance survives a later edit or
    deactivation of the live template.
    """

    __tablename__ = "inventory_transfer_templates"
    __table_args__ = (
        # Migration 226. A name identifies a transfer-template family to staff, not
        # one row: an operator must be able to create v2 under the same name. The
        # revision tuple is the stable identity, and the service serializes
        # allocation — this constraint is the database backstop for any other writer.
        UniqueConstraint(
            "source_branch_id",
            "name",
            "version_number",
            name="uq_inventory_transfer_template_revision",
        ),
    )

    source_branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    destination_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    display_order: Mapped[int] = mapped_column(
        Numeric(6, 0), nullable=False, server_default="0"
    )
    version_number: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    items: Mapped[list[InventoryTransferTemplateItem]] = relationship(
        "InventoryTransferTemplateItem",
        back_populates="template",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InventoryTransferTemplateItem.display_order",
    )


class InventoryTransferTemplateItem(Base, UUIDMixin):
    __tablename__ = "inventory_transfer_template_items"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "item_id", name="uq_inventory_transfer_template_item"
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transfer_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(
        Numeric(6, 0), nullable=False, server_default="0"
    )
    template: Mapped[InventoryTransferTemplate] = relationship(
        "InventoryTransferTemplate", back_populates="items"
    )


class NotificationRule(Base, UUIDMixin, TimestampMixin):
    """
    Who gets told when something happens — a void over a threshold, a till
    variance, stock below minimum. Foodics calls these notification rules.
    """

    __tablename__ = "notification_rules"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    #: Domain event, e.g. "order.voided", "till.closed", "inventory.below_minimum".
    event: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    #: Only fire above this value, where the event carries an amount.
    threshold: Mapped[Any | None] = mapped_column(Numeric(12, 2), nullable=True)
    branch_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    recipient_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    recipient_emails: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    channels: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{email}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    meta: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<NotificationRule {self.event}>"
