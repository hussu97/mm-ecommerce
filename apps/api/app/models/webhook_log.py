"""
Every webhook anyone pushed at us, verbatim, whether or not we understood it.

Distinct from `webhook_events`, which is a dedup ledger: one row per event we
*accepted*, holding an id and nothing else. This is the opposite — one row per
request that arrived, including the ones that matched no order, failed a
signature check, carried no readable body, or blew up in the handler. The events
table answers "have I seen this before"; this one answers "what did they
actually send, and what did we do about it".

The case for it was made on 2026-08-05. MM-20260805-007 received four noon Send
pushes and MM-20260805-006 received one — but the only evidence either way was
`docker logs`, which a container restart had already taken. Whether the three
missing pushes were never sent, sent to the wrong URL, or accepted and dropped
is a question nobody can now answer. Their side does not retry and has nothing
to replay from, so a push we cannot see is a push that never happened.

**The payment gateways were added for the same reason, and it applies harder.**
A courier push we lose costs tracking. A payment event we lose is money that
moved with the order none the wiser — and that has already happened once here,
when an SDK upgrade broke every `payment_intent.succeeded` for three days while
Stripe's dashboard showed nothing but successful deliveries. What was missing
was not retries; it was any record on *our* side of what had arrived.

Rows are purged after `LOG_RETENTION_DAYS` — see `app/services/log_retention.py`.
That is what makes it safe to keep every rider-position ping at full payload:
noon Send send one every 15-30 seconds per live task, and a bounded table can
afford to be complete where an unbounded one could not. Payment history is not
lost when a row ages out: `webhook_events` keeps the dedup ledger and
`payment_transactions` keeps the outcome, both permanently. What ages out is the
raw body, which is a debugging aid and not a record.
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

    #: `lalamove` | `noon_send` | `stripe` | `ziina`. Not an enum: a push from
    #: something we have not integrated yet is exactly the kind of surprise this
    #: table exists to record, and a constraint would refuse to write it down.
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Which of a provider's endpoints took it.
    #:
    #: `status` or `tracking` for noon Send, who have two that behave nothing
    #: alike — one arrives on a state change, the other every twenty seconds.
    #: For a payment gateway it is which *mount* answered, `payments` or
    #: `webhooks`: both do identical work, and which URL a processor is actually
    #: configured against is otherwise unanswerable without asking them.
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
    #: noon Send do not sign anything and send a key we never issued. Both
    #: payment gateways do sign, and a `false` here is the row worth alerting
    #: on: somebody is pushing unsigned payment events at us.
    signature_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    #: What kind of push, in the provider's own words: `picked_up`,
    #: `ORDER_STATUS_CHANGED`, `payment_intent.succeeded`,
    #: `payment_intent.status.updated:completed`. Never translated — a webhook
    #: that says `EXPIRED` is evidence, and paraphrasing it loses the reason.
    event_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Their id for whatever this push is about, and usually how it is matched
    #: to an order: a Lalamove booking, a noon Send task, a Stripe payment
    #: intent, a Ziina one. Called `courier_order_id` until the payment gateways
    #: started writing to it, which made the name a lie of exactly the kind the
    #: gateway split had just finished removing from `orders`.
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Whether it found an order at all. The single most useful column here: a
    #: run of `false` is either their bug or ours, and until now was invisible.
    #:
    #: Null means the question did not arise — a connection test, a payment
    #: event of a kind we act on nothing for, a duplicate we had already
    #: applied. Only `false` means "we should have found an order and did not",
    #: which is what keeps that signal worth watching.
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
        Index("ix_webhook_logs_external_id", "external_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookLog {self.provider}/{self.endpoint} "
            f"{self.event_type or '-'} matched={self.matched}>"
        )
