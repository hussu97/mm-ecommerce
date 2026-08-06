from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.core.deps import get_current_active_user, get_db
from app.core.limiter import limiter
from app.models.user import User
from app.services import payment_service

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateSessionRequest(BaseModel):
    order_number: str
    provider: str  # currently: stripe | cod


class CreateSessionResponse(BaseModel):
    provider: str
    session_id: str | None = None
    checkout_url: str | None = None
    confirmed: bool = False


class PaymentStatusResponse(BaseModel):
    order_number: str
    payment_provider: str | None
    payment_method: str | None
    payment_id: str | None
    paid: bool
    order_status: str


@router.post(
    "/create-session",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def create_payment_session(
    request: Request,
    data: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Create a payment checkout session for an order.
    Returns a provider-specific checkout URL for the customer to complete payment.

    Authentication is required and the order must belong to the caller. This
    used to take an optional user it never read, which left confirming a
    stranger's pickup order as `cod` a matter of guessing an order number.
    """
    result = await payment_service.create_session(
        db,
        data.order_number,
        data.provider,
        user_id=current_user.id,
        admin=current_user.is_admin,
    )
    return CreateSessionResponse(**result)


@router.post("/webhooks/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    stripe_signature: str | None = Header(None, alias="stripe-signature"),
):
    """
    Stripe webhook endpoint. Verifies the signature and applies payment events.

    **A failure here must not answer 200.** It used to: every exception was
    logged and swallowed into a success, on the reasoning that Stripe retries
    anything else. What that actually bought was three days of
    `payment_intent.succeeded` events failing on an SDK change while Stripe's
    dashboard showed an unbroken wall of green and paid orders sat in `created`.

    So the two kinds of failure are now told apart:

    * A **bad signature or unparseable body** is a 400. Retrying will never fix
      it, and Stripe surfaces it as a failed delivery instead of hiding it.
    * **Anything else** is ours — a bug, a database blip — and it propagates as
      a 500 so Stripe retries for up to three days. The dedup row is written in
      the same transaction as the work, so a rolled-back attempt leaves nothing
      behind and the retry is free to try again.
    """
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    payload = await request.body()

    try:
        return await payment_service.handle_stripe_webhook(
            db, payload, stripe_signature
        )
    except BadRequestError as e:
        logger.warning("Rejected Stripe webhook: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        # CRITICAL, not ERROR: a payment event we failed to apply is money that
        # moved without the order knowing.
        logger.critical("Stripe webhook processing failed", exc_info=True)
        raise


@router.post("/webhooks/tabby", status_code=status.HTTP_200_OK)
async def tabby_webhook(
    request: Request,
    x_tabby_signature: str | None = Header(None, alias="x-tabby-signature"),
):
    """Tabby webhook stub — acknowledges all events."""
    payload = await request.body()
    return await payment_service.handle_tabby_webhook(payload, x_tabby_signature or "")


@router.post("/webhooks/tamara", status_code=status.HTTP_200_OK)
async def tamara_webhook(
    request: Request,
    x_tamara_signature: str | None = Header(None, alias="x-tamara-signature"),
):
    """Tamara webhook stub — acknowledges all events."""
    payload = await request.body()
    return await payment_service.handle_tamara_webhook(
        payload, x_tamara_signature or ""
    )


@router.get("/{order_number}/status", response_model=PaymentStatusResponse)
@limiter.limit("30/minute")
async def get_payment_status(
    request: Request,
    order_number: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get payment status for an order. Restricted to the order's owner."""
    result = await payment_service.get_status(
        db,
        order_number,
        user_id=current_user.id,
        admin=current_user.is_admin,
    )
    return PaymentStatusResponse(**result)
