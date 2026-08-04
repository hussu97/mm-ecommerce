from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class PushPlatformEnum(str, enum.Enum):
    """Where a token can be delivered to.

    Only Apple for now — the register is an iPad and the companion an iPhone —
    but the column exists so adding Android is a row rather than a migration.
    """

    APNS = "apns"


class DevicePushToken(Base, UUIDMixin, TimestampMixin):
    """
    One device that wants to be told when an order arrives.

    Keyed by the token itself rather than by device, because the token is what
    Apple actually addresses and it is the thing that changes: iOS reissues it
    on reinstall, restore and occasionally for its own reasons. A device that
    re-registers with a new token leaves the old row behind, which is why
    `revoked_at` exists — APNs answers 410 Gone for a dead token, and the only
    correct response is to stop using it.

    **The branch is the point.** A push goes to the kitchen the order is for, not
    to every register in the business, so a token is useless without knowing
    which counter it is standing on.
    """

    __tablename__ = "device_push_tokens"
    __table_args__ = (
        # The same token twice is the same device registering again — an update,
        # never a second row.
        UniqueConstraint("token", name="uq_device_push_token"),
    )

    #: Apple's device token, hex. 64 characters today; Apple has warned for
    #: years that it may grow, so the column is not sized to today's value.
    token: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=PushPlatformEnum.APNS.value
    )
    #: Which app is listening — the APNs topic. The iPad register and the phone
    #: companion are separate bundle ids and a push must name the right one.
    bundle_id: Mapped[str] = mapped_column(String(100), nullable=False)

    #: The counter this device stands on. Null means it registered before
    #: choosing a branch, and it receives nothing until it does.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: The POS device row, where the terminal has one. Kept for the admin's
    #: benefit — "which iPad is this" is otherwise an opaque hex string.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Who was signed in when it registered, for tracing a stray notification
    #: back to a person.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Sandbox tokens are not valid against the production APNs host and vice
    #: versa. A TestFlight build and an App Store build of the same app produce
    #: different ones, and sending to the wrong host fails with a
    #: `BadDeviceToken` that names nothing useful.
    is_sandbox: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When APNs told us this token is dead. Kept rather than deleted so a
    #: device that goes quiet is distinguishable from one that never registered.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and self.branch_id is not None

    def __repr__(self) -> str:
        return f"<DevicePushToken {self.bundle_id} {self.token[:12]}…>"
