"""
Every push a courier made us, verbatim, whether or not we understood it.

Distinct from `webhook_events`, which is a dedup ledger: one row per event we
*accepted*, holding an id and nothing else. This is the opposite — one row per
request that arrived, including the ones that matched no order, failed a key
check, carried no readable body, or blew up in the handler. The events table
answers "have I seen this before"; this one answers "what did they actually
send, and what did we do about it".

The case for it was made on 2026-08-05. MM-20260805-007 received four noon Send
pushes and MM-20260805-006 received one — but the only evidence either way was
`docker logs`, which a container restart had already taken. Whether the three
missing pushes were never sent, sent to the wrong URL, or accepted and dropped
is a question nobody can now answer. Their side does not retry and has nothing
to replay from, so a push we cannot see is a push that never happened.

Rows are purged after `LOG_RETENTION_DAYS` — see `app/services/log_retention.py`.
That is what makes it safe to keep every rider-position ping at full payload:
noon Send send one every 15-30 seconds per live task, and a bounded table can
afford to be complete where an unbounded one could not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, utcnow

__all__ = ["WebhookLog"]


class WebhookLog(Base, UUIDMixin):
    """One inbound webhook request, as it arrived and as it was answered."""

    __tablename__ = "webhook_logs"

    #: `lalamove` | `noon_send`. Not an enum: a push from something we have not
    #: integrated yet is exactly the kind of surprise this table exists to
    #: record, and a constraint would refuse to write it down.
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Which of a provider's endpoints took it — `status` or `tracking`. noon
    #: Send have two and they behave nothing alike; one arrives on a state
    #: change, the other every twenty seconds.
    endpoint: Mapped[str] = mapped_column(String(20), nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    #: Who connected. Their staging pushes arrive from a different address than
    #: their production ones, which is worth being able to see.
    remote_ip: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Four characters either end of whatever key they presented, never the key.
    #: Enough to tell two keys apart across two systems, which is the whole
    #: question when every push is being refused. See `webhooks._key_fingerprint`.
    api_key_fingerprint: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Whether the request authenticated. Null when the endpoint does not check —
    #: noon Send do not sign anything and send a key we never issued.
    signature_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    #: What kind of push, in the provider's own words: `picked_up`,
    #: `ORDER_STATUS_CHANGED`. Never translated — a webhook that says `EXPIRED`
    #: is evidence, and paraphrasing it loses the reason.
    event_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Their id for the booking, which is how a push is matched to an order.
    courier_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Whether it found an order at all. The single most useful column here: a
    #: run of `false` is either their bug or ours, and until now was invisible.
    matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    #: The body exactly as sent. Their fields change without notice and the raw
    #: copy is what makes a surprise diagnosable a week later.
    payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    #: What the handler returned, so "we accepted it" and "we acted on it" can
    #: be told apart.
    result: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # `received_at` descending because every read of this table is "what
        # just happened", and the retention sweep scans the other end of the
        # same index.
        Index("ix_webhook_logs_received_at", received_at.desc()),
        Index("ix_webhook_logs_provider", "provider", "endpoint"),
        Index("ix_webhook_logs_order_number", "order_number"),
        Index("ix_webhook_logs_courier_order_id", "courier_order_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookLog {self.provider}/{self.endpoint} "
            f"{self.event_type or '-'} matched={self.matched}>"
        )
