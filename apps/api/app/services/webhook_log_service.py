"""
Writing down what arrived, before anything is decided about it.

Two properties matter, and both are why this is a module rather than three
lines inside each route.

**It uses its own session.** The handler's transaction can and does roll back —
a `NoonSendError`, a constraint, an unhandled anything — and a record that
disappears with the failure it was recording is worse than no record at all. The
same reasoning, and the same shape, as `email_service._log`.

**It never raises.** A sender that gets a 500 because we could not write a log
row would retry (Lalamove, ten times over a day, then disabling the URL; Stripe,
for three days) or would not (noon Send, ever). All of those outcomes are worse
than a missing row, and for a payment gateway the worst of them is a *successful*
payment retried into a 500 forever because of a logging fault. So every failure
here is swallowed into the application log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete

from app.core.database import AsyncSessionFactory
from app.models.webhook_log import WebhookLog

logger = logging.getLogger(__name__)

__all__ = ["Recorder", "purge_older_than"]


def _string(value: Any, limit: int) -> str | None:
    """A column-safe string, or nothing. Truncates rather than refusing a row."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


class Recorder:
    """
    One inbound request, filled in as the handler learns things about it.

    Built at the top of a route with what is knowable before any parsing, then
    told the outcome and saved. Deliberately a small mutable object rather than
    a function with eleven arguments: the route learns `matched` several lines
    after it learns `event_type`, and threading that through a call signature is
    how one of them ends up not being passed.

        recorder = Recorder("noon_send", "status", request=request, key=x_api_key)
        recorder.payload = payload
        ...
        recorder.finish(result=result)
        await recorder.save()
    """

    def __init__(
        self,
        provider: str,
        endpoint: str,
        *,
        request: Any = None,
        key: str | None = None,
        key_fingerprint: str | None = None,
    ) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.started = datetime.now(timezone.utc)
        self.remote_ip: str | None = None
        if request is not None and getattr(request, "client", None):
            self.remote_ip = request.client.host
        self.api_key_fingerprint = key_fingerprint
        self.signature_valid: bool | None = None
        self.event_type: str | None = None
        self.order_number: str | None = None
        self.external_id: str | None = None
        self.matched: bool | None = None
        self.payload: Any = None
        self.result: Any = None
        self.error: str | None = None
        self.http_status: int = 200
        # Kept only so a caller that has the raw key but not our fingerprinting
        # helper can hand it over; the fingerprint is what is ever stored.
        self._key = key

    def read(self, result: Any) -> None:
        """
        Take what the handler learned from whatever it returned.

        Every handler answers with a dict carrying these keys, so this saves each
        route restating the same six lines — and, more to the point, stops one
        of them quietly not restating them.

        `matched` is read only when the key is *present*. A handler that never
        got as far as looking for an order — a connection test, a duplicate, a
        payment event of a kind we act on nothing for — must leave the column
        null rather than assert `false`, because `false` is the alarming value
        and it should keep meaning "we should have found one and did not".
        """
        self.result = result
        if isinstance(result, dict):
            if "matched" in result:
                self.matched = bool(result["matched"])
            self.event_type = self.event_type or _string(result.get("event_type"), 60)
            self.order_number = self.order_number or _string(
                result.get("order_number"), 30
            )
            self.external_id = self.external_id or _string(
                result.get("external_id"), 64
            )

    def finish(self, *, result: Any = None, error: str | None = None) -> None:
        if result is not None:
            self.read(result)
        if error is not None:
            self.error = error[:2000]

    async def save(self) -> None:
        """Write the row. Its own session, and it never raises. See the module docstring."""
        try:
            duration_ms = int(
                (datetime.now(timezone.utc) - self.started).total_seconds() * 1000
            )
            async with AsyncSessionFactory() as db:
                db.add(
                    WebhookLog(
                        provider=_string(self.provider, 20) or "unknown",
                        endpoint=_string(self.endpoint, 20) or "unknown",
                        received_at=self.started,
                        remote_ip=_string(self.remote_ip, 60),
                        api_key_fingerprint=_string(self.api_key_fingerprint, 60),
                        signature_valid=self.signature_valid,
                        event_type=_string(self.event_type, 60),
                        order_number=_string(self.order_number, 30),
                        external_id=_string(self.external_id, 64),
                        matched=self.matched,
                        payload=_jsonable(self.payload),
                        result=_jsonable(self.result),
                        error=self.error,
                        http_status=self.http_status,
                        duration_ms=duration_ms,
                    )
                )
                await db.commit()
        except Exception as exc:  # pragma: no cover — defensive by design
            logger.error("Could not record the %s webhook: %s", self.provider, exc)


def _jsonable(value: Any) -> Any:
    """
    Whatever will survive a JSONB column.

    A payload that is not a dict or a list is still worth keeping — an empty
    connection test, a string, a number — so it is wrapped rather than dropped.
    """
    if value is None or isinstance(value, (dict, list)):
        return value
    return {"raw": str(value)[:10_000]}


async def purge_older_than(db, cutoff: datetime) -> int:
    """Delete every row received before `cutoff`. Returns how many."""
    result = await db.execute(delete(WebhookLog).where(WebhookLog.received_at < cutoff))
    return result.rowcount or 0
