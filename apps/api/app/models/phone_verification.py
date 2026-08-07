from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, utcnow


class PhoneVerification(Base, UUIDMixin):
    """
    A phone number somebody proved they hold, and when.

    Kept as a row rather than handed back to the browser as a signed token,
    because the people this exists for are guests. A guest has no account to
    hang a flag on and no session worth trusting across a checkout that may
    span a page reload, an OTP SMS, and a switch to the messages app and back.
    A row keyed on the number survives all of that and is readable by the
    coupon check without the client being involved at all.

    Deliberately not unique on `phone`. A household shares a number, somebody
    re-verifies after changing handset, and the history is worth having when a
    discount is later disputed. The lookups all ask for the most recent row.

    `user_id` is whoever was signed in at the time, and is usually nobody.
    """

    __tablename__ = "phone_verifications"

    #: E.164, exactly as Firebase issued it. The coupon rule counts identities
    #: by this string, so the format has to be the one the order also stores.
    phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    #: Firebase's own id for the account that did the proving. Not used to
    #: authenticate anything — it is a support breadcrumb for the day two
    #: customers argue about one number.
    firebase_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: When the OTP was actually completed, taken from the token's `auth_time`
    #: rather than from our own clock at the moment the request landed.
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    #: `SET NULL`, not `CASCADE`: a guest row being tidied up must not erase the
    #: evidence that a number was verified, or the coupon it earned becomes
    #: claimable again.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<PhoneVerification {self.phone} @ {self.verified_at}>"
