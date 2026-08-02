from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.api.v1.payments import stripe_webhook as payments_stripe_webhook
from app.services import lalamove_service
from app.services.providers.lalamove_provider import LalamoveError

logger = logging.getLogger(__name__)

router = APIRouter()


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
