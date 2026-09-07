"""The journal of automatic daily-sales-report sends, one row per trading day."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, business_date_format, utcnow


class DailySalesSend(Base):
    """One row per business date the NIGHTLY report was mailed in full.

    The idempotency record for the daily sales email (F-POS-18). It used to be
    inferred from `email_logs` by a subject `LIKE '%<date>%'`, which had two
    faults: a manual send from the admin console wrote an `email_logs` row with
    the date in its subject and so silently suppressed that night's automatic
    send; and a run where one recipient failed still left a `sent` row, so the
    day was treated as done and never retried.

    An explicit journal fixes both. Only the automatic loop writes here, and only
    once EVERY recipient has succeeded — a manual send never touches this table
    (so it cannot suppress the nightly one), and a partial failure leaves no row
    (so the next tick retries).
    """

    __tablename__ = "daily_sales_sends"
    __table_args__ = (business_date_format("daily_sales_sends"),)

    #: The trading day this report covered. The primary key, so a day is recorded
    #: at most once and a re-send races to a no-op rather than a duplicate.
    business_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    #: The addresses the report reached, all of which succeeded.
    recipients: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
