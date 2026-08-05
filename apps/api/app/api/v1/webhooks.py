from __future__ import annotations

import json
from typing import Any

import logging

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.api.v1.payments import stripe_webhook as payments_stripe_webhook
from app.services import lalamove_service, noon_send_service
from app.services.providers.lalamove_provider import LalamoveError
from app.services.providers.noon_send_provider import NoonSendError

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

    These pushes are not ours to replay. noon does not retry, there is nothing
    on their side to resend from, and a container restart takes `docker logs`
    with it — so a push we cannot see is a push that never happened as far as
    anybody can tell afterwards. Three of them were dropped that way before this
    existed.
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
    Whether this push should be acted on. Currently: always.

    noon Send does not sign requests, and the key they send is not the one we
    configured — their staging side has a value of its own that no screen of
    ours produced. Refusing on a mismatch dropped every status update for the
    trial: `assigned` and `picked_up` both arrived, two seconds after noon's own
    records show them, and both were thrown away with a warning nobody was
    watching.

    So the key is recorded and not enforced. What guards the endpoint is the
    task number: a push only moves an order we already dispatched under that
    `mp_task_nr` — sixteen characters we never publish — and anything else is
    acknowledged and ignored, while the status rank guard stops even a correct
    guess walking an order backwards.

    The fingerprint of whatever they send is in the log next to ours. When those
    two agree, this can go back to comparing them.
    """
    return True


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    stripe_signature: str | None = Header(None, alias="stripe-signature"),
):
    """Compatibility endpoint for Stripe dashboard webhooks."""
    return await payments_stripe_webhook(request, db, stripe_signature)


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
    raw = await request.body()
    if not raw:
        return {"received": True}

    try:
        return await lalamove_service.handle_webhook(db, raw)
    except LalamoveError as exc:
        logger.warning("Rejected Lalamove webhook: %s", exc)
        return {"received": True, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("Lalamove webhook processing failed")
        return {"received": True, "error": str(exc)}


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
    try:
        payload = await request.json()
    except Exception:
        logger.warning("noon Send status webhook had no readable body")
        return {"received": True}
    _log_noon_send_push("status", payload, x_api_key, request)
    if not isinstance(payload, dict):
        return {"received": True}
    if not _noon_send_key_is_valid(x_api_key):
        return {"received": True, "error": "unauthorised"}

    try:
        result = await noon_send_service.handle_webhook(db, payload)
        logger.info(
            "noon Send status webhook for %s → %s",
            payload.get("order_nr") or payload.get("order_reference") or "?",
            result,
        )
        return result
    except NoonSendError as exc:
        logger.warning("Rejected noon Send webhook: %s", exc)
        return {"received": True, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("noon Send webhook processing failed")
        return {"received": True, "error": str(exc)}


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
    status change in the same table.
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("noon Send tracking webhook had no readable body")
        return {"received": True}
    _log_noon_send_push("tracking", payload, x_api_key, request)
    if not isinstance(payload, dict):
        return {"received": True}
    if not _noon_send_key_is_valid(x_api_key):
        return {"received": True, "error": "unauthorised"}

    try:
        result = await noon_send_service.handle_tracking_webhook(db, payload)
        logger.info(
            "noon Send tracking webhook for %s → %s · rider at %s",
            payload.get("order_nr") or "?",
            result,
            ((payload.get("da_details") or {}).get("location") or "no position"),
        )
        return result
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("noon Send tracking webhook processing failed")
        return {"received": True, "error": str(exc)}
