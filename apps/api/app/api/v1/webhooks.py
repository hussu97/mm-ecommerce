from __future__ import annotations

import hmac
import json
from typing import Any

import logging

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.api.v1.payments import process_gateway_webhook
from app.services import lalamove_service, noon_send_service
from app.services.providers.lalamove_provider import LalamoveError
from app.services.providers.noon_send_provider import NoonSendError
from app.services.webhook_log_service import Recorder

logger = logging.getLogger(__name__)

router = APIRouter()


def _key_fingerprint(presented: str | None) -> str:
    """
    Enough of a key to recognise it, never enough to use it.

    Logged so the mismatch that actually happened is diagnosable: noon Send sent
    a key we had never seen, every status push was refused, and the log said
    only that it did not match — which is true and useless. Four characters
    either end identifies which key is in play across two systems.
    """
    if not presented:
        return "(none)"
    if len(presented) <= 8:
        return f"len={len(presented)}"
    return f"{presented[:4]}…{presented[-4:]} (len={len(presented)})"


def _log_noon_send_push(
    kind: str, payload: Any, presented: str | None, request: Request
) -> None:
    """
    Everything that arrived, before anything is decided about it.

    The durable copy is the `webhook_logs` row; this is the same fact in the
    application log, where it is greppable next to whatever else the request
    did. Both exist because they fail differently — a restart takes the log, a
    migration could take the table — and three pushes were dropped with neither
    before either existed.
    """
    expected = (settings.NOON_SEND_WEBHOOK_API_KEY or "").strip()
    logger.info(
        "noon Send %s webhook from %s · key %s (configured %s) · payload %s",
        kind,
        request.client.host if request.client else "?",
        _key_fingerprint(presented),
        _key_fingerprint(expected) if expected else "(none)",
        json.dumps(payload, default=str)[:2000],
    )


def _noon_send_key_is_valid(presented: str | None) -> bool:
    """
    Whether this push should be acted on.

    noon Send does not sign requests, and the key they send is not the one we
    configured — their staging side has a value of its own that no screen of
    ours produced. Refusing on a mismatch dropped every status update for the
    trial: `assigned` and `picked_up` both arrived, two seconds after noon's own
    records show them, and both were thrown away with a warning nobody was
    watching.

    So by default the key is recorded and not enforced. What guards the endpoint
    meanwhile is the task number: a push only moves an order we already
    dispatched under that `mp_task_nr` — sixteen characters we never publish —
    and anything else is acknowledged and ignored, while the status rank guard
    stops even a correct guess walking an order backwards.

    The fingerprint of whatever they send is stored on every `webhook_logs` row
    next to ours. Once those two agree, set `NOON_SEND_ENFORCE_WEBHOOK_KEY=true`
    and this compares them — in constant time, so a mismatch cannot be found a
    character at a time.
    """
    if not settings.NOON_SEND_ENFORCE_WEBHOOK_KEY:
        return True
    expected = (settings.NOON_SEND_WEBHOOK_API_KEY or "").strip()
    if not expected:
        # Enforcement asked for with nothing to enforce against would refuse
        # every delivery update. Loud, and open, rather than silently dark.
        logger.error(
            "NOON_SEND_ENFORCE_WEBHOOK_KEY is set but no key is configured — "
            "accepting the push rather than dropping live deliveries"
        )
        return True
    return bool(presented) and hmac.compare_digest(presented, expected)


def _noon_send_recorder(endpoint: str, request: Request, key: str | None) -> Recorder:
    """One shape for both noon Send routes, so neither drifts from the other."""
    return Recorder(
        "noon_send",
        endpoint,
        request=request,
        key_fingerprint=_key_fingerprint(key),
    )


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Compatibility endpoint for Stripe dashboard webhooks."""
    return await process_gateway_webhook("stripe", request, db, mount="webhooks")


@router.post("/ziina", status_code=status.HTTP_200_OK)
async def ziina_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Ziina's push endpoint, mounted here as well as under `/payments`.

    Both mounts exist for Stripe because production was configured against
    `/webhooks/stripe` before the payments router had one. Ziina gets the pair
    from the start so that whichever of the two a future operator reaches for
    when registering the URL, they are right.

    Which of the two a processor is actually calling is recorded on every
    `webhook_logs` row as `endpoint`, so the question can be answered from the
    admin rather than by asking them.
    """
    return await process_gateway_webhook("ziina", request, db, mount="webhooks")


@router.post("/lalamove", status_code=status.HTTP_200_OK)
async def lalamove_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Courier status pushes: driver matched, picked up, delivered, cancelled.

    Always answers 200. Lalamove retries anything else ten times over a day and
    then disables the URL entirely, which would leave every subsequent order
    with no status at all — a far worse outcome than swallowing one malformed
    event. The connection test sends an empty body and must be answered too.

    The signature is checked inside the service; a payload that fails it is
    logged and dropped rather than acted on.
    """
    recorder = Recorder("lalamove", "status", request=request)
    raw = await request.body()

    try:
        if not raw:
            # Their connection test. Recorded rather than answered silently —
            # "did the URL we gave them ever get called" is a real question.
            recorder.event_type = "connection_test"
            recorder.finish(result={"received": True})
            return {"received": True}

        try:
            recorder.payload = json.loads(raw)
        except Exception:
            recorder.payload = raw.decode("utf-8", "replace")
        if isinstance(recorder.payload, dict):
            recorder.event_type = (
                str(recorder.payload.get("eventType") or "").strip() or None
            )

        try:
            result = await lalamove_service.handle_webhook(db, raw)
            recorder.signature_valid = True
            recorder.finish(result=result)
            return result
        except LalamoveError as exc:
            # The signature check lives inside the service, so this is where a
            # failed one surfaces.
            recorder.signature_valid = False
            logger.warning("Rejected Lalamove webhook: %s", exc)
            recorder.finish(
                result={"received": True, "error": str(exc)}, error=str(exc)
            )
            return {"received": True, "error": str(exc)}
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("Lalamove webhook processing failed")
            recorder.finish(
                result={"received": True, "error": str(exc)}, error=str(exc)
            )
            return {"received": True, "error": str(exc)}
    finally:
        await recorder.save()


@router.post("/noon-send", status_code=status.HTTP_200_OK)
async def noon_send_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """
    noon Send task status pushes: assigned, picked up, delivered, undelivered.

    Answers 200 to everything for the same reason the Lalamove endpoint does — a
    retried-then-disabled webhook URL would leave every later order with no
    status at all, which is worse than swallowing one bad event. A push that
    fails the key check is logged and dropped rather than acted on.
    """
    recorder = _noon_send_recorder("status", request, x_api_key)
    try:
        try:
            payload = await request.json()
        except Exception:
            logger.warning("noon Send status webhook had no readable body")
            recorder.finish(result={"received": True}, error="unreadable body")
            return {"received": True}

        recorder.payload = payload
        _log_noon_send_push("status", payload, x_api_key, request)
        if not isinstance(payload, dict):
            recorder.finish(result={"received": True}, error="body was not an object")
            return {"received": True}

        recorder.event_type = str(payload.get("status_code") or "").strip() or None
        recorder.external_id = str(payload.get("order_nr") or "") or None
        recorder.order_number = str(payload.get("order_reference") or "") or None

        if not _noon_send_key_is_valid(x_api_key):
            recorder.signature_valid = False
            recorder.finish(result={"received": True, "error": "unauthorised"})
            return {"received": True, "error": "unauthorised"}

        try:
            result = await noon_send_service.handle_webhook(db, payload)
            logger.info(
                "noon Send status webhook for %s → %s",
                payload.get("order_nr") or payload.get("order_reference") or "?",
                result,
            )
            recorder.finish(result=result)
            return result
        except NoonSendError as exc:
            logger.warning("Rejected noon Send webhook: %s", exc)
            recorder.finish(
                result={"received": True, "error": str(exc)}, error=str(exc)
            )
            return {"received": True, "error": str(exc)}
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("noon Send webhook processing failed")
            recorder.finish(
                result={"received": True, "error": str(exc)}, error=str(exc)
            )
            return {"received": True, "error": str(exc)}
    finally:
        await recorder.save()


@router.post("/noon-send/tracking", status_code=status.HTTP_200_OK)
async def noon_send_tracking_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """
    Rider position, pushed every 15-30 seconds while a task is live.

    Kept on its own route rather than folded into the status one: it arrives at
    a completely different rate, it carries no status, and it is deliberately
    not journalled in `webhook_events` — a row per ping would bury every real
    status change in that table.

    It *is* recorded in `webhook_logs`, at full payload. That table is purged on
    a retention window rather than kept forever, which is what makes completeness
    affordable there and not here: thirteen pings arriving in eight minutes and
    moving nothing is precisely the shape of the bug this endpoint has already
    had once, and it was invisible.
    """
    recorder = _noon_send_recorder("tracking", request, x_api_key)
    try:
        try:
            payload = await request.json()
        except Exception:
            logger.warning("noon Send tracking webhook had no readable body")
            recorder.finish(result={"received": True}, error="unreadable body")
            return {"received": True}

        recorder.payload = payload
        _log_noon_send_push("tracking", payload, x_api_key, request)
        if not isinstance(payload, dict):
            recorder.finish(result={"received": True}, error="body was not an object")
            return {"received": True}

        recorder.event_type = "tracking"
        recorder.external_id = str(payload.get("order_nr") or "") or None
        recorder.order_number = str(payload.get("order_reference") or "") or None

        if not _noon_send_key_is_valid(x_api_key):
            recorder.signature_valid = False
            recorder.finish(result={"received": True, "error": "unauthorised"})
            return {"received": True, "error": "unauthorised"}

        try:
            result = await noon_send_service.handle_tracking_webhook(db, payload)
            logger.info(
                "noon Send tracking webhook for %s → %s · rider at %s",
                payload.get("order_nr") or "?",
                result,
                ((payload.get("da_details") or {}).get("location") or "no position"),
            )
            recorder.finish(result=result)
            return result
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("noon Send tracking webhook processing failed")
            recorder.finish(
                result={"received": True, "error": str(exc)}, error=str(exc)
            )
            return {"received": True, "error": str(exc)}
    finally:
        await recorder.save()
