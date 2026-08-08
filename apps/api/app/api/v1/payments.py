from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.deps import get_current_active_user, get_db
from app.core.limiter import limiter
from app.models.user import User
from app.services import payment_service
from app.services.webhook_log_service import Recorder

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateSessionRequest(BaseModel):
    order_number: str

    #: What the customer chose: `card` or `cod`.
    method: str | None = None

    #: The previous name for the same field, when it carried `stripe | cod`.
    #:
    #: Kept because this deploys in two pieces: the browser holding the previous
    #: bundle posts `provider`, and it keeps working through the rollout. It is
    #: no longer a gateway request and never was honoured as one — `stripe` here
    #: means "card", and which processor settles it is ours to decide.
    provider: str | None = None

    @model_validator(mode="after")
    def _one_of(self) -> "CreateSessionRequest":
        if not (self.method or self.provider):
            raise ValueError("one of 'method' or 'provider' is required")
        return self

    @property
    def payment_method(self) -> str:
        return self.method or self.provider or ""


class CreateSessionResponse(BaseModel):
    #: The gateway that was actually chosen — `stripe`, `ziina`, `cod`, `none`.
    #: Reported so the client can log which processor a checkout went through;
    #: it is not shown to the customer and it is not something they asked for.
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

    The body names a *method* — card or cash. For a card, which gateway settles
    it is decided server-side from the `payment_gateways` table, so a client
    cannot pick its own processor and a Stripe outage is answered without
    shipping anything.

    Authentication is required and the order must belong to the caller. This
    used to take an optional user it never read, which left confirming a
    stranger's pickup order as `cod` a matter of guessing an order number.
    """
    result = await payment_service.create_session(
        db,
        data.order_number,
        data.payment_method,
        user_id=current_user.id,
        admin=current_user.is_admin,
    )
    return CreateSessionResponse(**result)


async def process_gateway_webhook(
    gateway: str, request: Request, db: AsyncSession, *, mount: str
) -> dict:
    """
    The one webhook path, shared by every gateway and every mounted route.

    **A failure here must not answer 200.** It used to: every exception was
    logged and swallowed into a success, on the reasoning that Stripe retries
    anything else. What that actually bought was three days of
    `payment_intent.succeeded` events failing on an SDK change while Stripe's
    dashboard showed an unbroken wall of green and paid orders sat in `created`.

    So the two kinds of failure are told apart:

    * A **bad signature or unparseable body** is a 400. Retrying will never fix
      it, and the gateway surfaces it as a failed delivery instead of hiding it.
    * **Anything else** is ours — a bug, a database blip — and it propagates as
      a 500 so the gateway retries. The dedup row is written in the same
      transaction as the work, so a rolled-back attempt leaves nothing behind
      and the retry is free to try again.

    Ziina retries three times and then stops, where Stripe retries for three
    days. That is not a reason to swallow errors here — it is a reason the
    reconciliation path exists (`ziina_provider.fetch_payment_intent`).

    **Every request is recorded in `webhook_logs`, whatever it does**, on the
    recorder's own session so the row outlives a rolled-back handler. That is
    the point: the three-day outage above was invisible precisely because the
    failing requests left nothing behind, and the only evidence — container
    stdout — had already been taken by a restart. A rejected signature and an
    unplaceable payment are the two rows most worth having, and both are ones a
    log written only on success would not contain.
    """
    recorder = Recorder(gateway, mount, request=request)
    payload = await request.body()
    recorder.payload = _readable(payload)

    try:
        try:
            result = await payment_service.handle_webhook(
                db, gateway, payload, request.headers
            )
        except NotFoundError as e:
            recorder.http_status = 404
            recorder.finish(error=str(e))
            raise HTTPException(status_code=404, detail=str(e)) from e
        except BadRequestError as e:
            # The signature is the only thing checked before the body is read,
            # so a rejection that mentions it is an authentication failure and
            # the rest are malformed-payload failures. Told apart because
            # `signature_valid = false` is the row that means somebody is
            # pushing forged payment events at us, and it should not be diluted
            # by every unparseable body that ever arrives.
            if "signature" in str(e).lower():
                recorder.signature_valid = False
            recorder.http_status = 400
            recorder.finish(error=str(e))
            logger.warning("Rejected %s webhook: %s", gateway, e)
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            # CRITICAL, not ERROR: a payment event we failed to apply is money
            # that moved without the order knowing.
            recorder.http_status = 500
            # Left null rather than guessed. A 500 can land either side of the
            # signature check, and "we do not know whether this was authentic"
            # is a different fact from "it was".
            recorder.finish(error=str(e))
            logger.critical("%s webhook processing failed", gateway, exc_info=True)
            raise

        # Reaching here means the provider verified it.
        recorder.signature_valid = True
        recorder.finish(result=result)
        return result
    finally:
        await recorder.save()


def _readable(payload: bytes):
    """
    The body as something a JSONB column will take, however malformed it is.

    A payload that will not parse is the most interesting one in the table —
    it is what a misconfigured sender or a truncated request looks like — so it
    is kept as text rather than dropped for failing to be JSON.
    """
    if not payload:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return payload.decode("utf-8", "replace")[:10_000]


@router.post("/webhooks/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe webhook endpoint. Named explicitly because it is what production
    is already configured to call — the generic route below would serve it
    identically, and changing the URL of a live webhook buys nothing."""
    return await process_gateway_webhook("stripe", request, db, mount="payments")


@router.post("/webhooks/ziina", status_code=status.HTTP_200_OK)
async def ziina_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Ziina webhook endpoint.

    Registered with Ziina by API rather than in a dashboard — one URL per
    account, set by `POST /webhook`, and any later call overwrites it. See
    `ziina_provider.register_webhook`.
    """
    return await process_gateway_webhook("ziina", request, db, mount="payments")


@router.post("/webhooks/{gateway}", status_code=status.HTTP_200_OK)
async def gateway_webhook(
    gateway: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """
    Every other gateway, on the same code path.

    A third processor needs a provider and a row, and nothing here. An unknown
    gateway is a 404 rather than a 200: acknowledging a webhook for something
    that does not exist is how a misconfigured URL looks healthy for a month.
    """
    return await process_gateway_webhook(gateway.lower(), request, db, mount="payments")


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
