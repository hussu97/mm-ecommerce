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


class TransferOrderStatusEnum(str, enum.Enum):
    """
    Mirrors the Foodics transfer-order lifecycle exactly.

    A transfer order is a *request* between locations. Accepting it does not
    move stock — sending does, and receiving completes it. Keeping the request
    separate from the movement is what lets a branch dispute a short delivery.
    """

    DRAFT = "draft"
    PENDING = "pending"  # submitted to the source location
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CLOSED = "closed"  # sent and received


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


class TransferOrder(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "transfer_orders"
    __table_args__ = (
        # Migration 099.
        status_vocabulary("transfer_orders", "status", TransferOrderStatusEnum),
        # Migration 100.
        business_date_format("transfer_orders"),
        # Migration 222 — mirrored here so the model states the same rule the DB does.
        CheckConstraint(
            "kind IN ('transfer', 'return')", name="ck_transfer_orders_kind"
        ),
        # Migration 224 — a till that retries a create+send (lost response) must not
        # ship the same box twice. The client sends a stable id; a second create
        # with it is refused, and the service returns the order already made.
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
        server_default=TransferOrderStatusEnum.DRAFT.value,
        index=True,
    )
    #: `transfer` (branch → branch) or `return` (branch → its return branch). See
    #: `TransferKindEnum`. Returns run the identical send/receive/variance flow.
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TransferKindEnum.TRANSFER.value,
        index=True,
    )
    #: Who is asking.
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
    #: Who is being asked.
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
    #: Client-supplied idempotency token for a POS create+send, unique when set.
    #: A retried create with the same token returns the order already made.
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    responder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    #: The transfer template this order was raised from, and the immutable record
    #: of that template as it stood at raise time. Nullable because returns and
    #: ad-hoc transfers are raised without a template. RESTRICT so a template with
    #: history cannot be hard-deleted out from under the orders that cite it —
    #: deactivate it instead. Downstream reads the ``template_snapshot``, never the
    #: live template, exactly as a shift report reads its own snapshot.
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

    items: Mapped[list[TransferOrderItem]] = relationship(
        "TransferOrderItem",
        back_populates="transfer_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<TransferOrder {self.reference} {self.status}>"


class TransferOrderItem(Base, UUIDMixin):
    __tablename__ = "transfer_order_items"

    transfer_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transfer_orders.id", ondelete="CASCADE"),
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
    #: What the source location actually agreed to send, which may be less.
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

    transfer_order: Mapped[TransferOrder] = relationship(
        "TransferOrder", back_populates="items"
    )

    def __repr__(self) -> str:
        return f"<TransferOrderItem item={self.item_id} qty={self.quantity}>"


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
