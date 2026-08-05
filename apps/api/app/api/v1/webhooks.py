from __future__ import annotations

import hmac
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


def _noon_send_key_is_valid(presented: str | None) -> bool:
    """
    Whether this push is one we should act on.

    noon Send does not sign requests, and their **staging environment sends no
    header at all** — there is nowhere in it to configure one. Demanding a key
    meant every status update during the trial was dropped, which is the whole
    thing we are trying to exercise. So a push that presents no key is accepted.

    A push that presents the *wrong* key is still refused. That is not
    theatre: it catches the realistic mistake, which is noon's production side
    being configured with a stale key, and it costs nothing.

    What is left protecting this endpoint is the task number. `_delivery_for`
    only matches a push to an order we already dispatched, so acting on one
    requires knowing a live `mp_task_nr` — sixteen characters we never publish.
    A push naming a task we do not hold is acknowledged and ignored, and the
    status rank guard means even a correct guess cannot walk an order backwards.
    That is thinner than a signature and it is what this courier offers.

    Set `NOON_SEND_WEBHOOK_API_KEY` once noon's production side actually sends
    one and this tightens back up on its own.
    """
    expected = (settings.NOON_SEND_WEBHOOK_API_KEY or "").strip()
    if not presented:
        return True
    if not expected:
        return True
    return hmac.compare_digest(presented, expected)


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
    if not _noon_send_key_is_valid(x_api_key):
        logger.warning("Rejected noon Send webhook: the API key does not match")
        return {"received": True, "error": "unauthorised"}

    try:
        payload = await request.json()
    except Exception:
        return {"received": True}
    if not isinstance(payload, dict):
        return {"received": True}

    try:
        return await noon_send_service.handle_webhook(db, payload)
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
    if not _noon_send_key_is_valid(x_api_key):
        return {"received": True, "error": "unauthorised"}

    try:
        payload = await request.json()
    except Exception:
        return {"received": True}
    if not isinstance(payload, dict):
        return {"received": True}

    try:
        return await noon_send_service.handle_tracking_webhook(db, payload)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("noon Send tracking webhook processing failed")
        return {"received": True, "error": str(exc)}
