"""
Custom orders in the admin console: take, change, pack, send, finish, invoice.

Everything here is also on the register (`pos_custom_orders`) except the three
things that are the console's alone: choosing a courier, recording a
third-party one, and the invoice. Those routes exist only on this router, which
the POS sub-app does not mount.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadGatewayError
from app.core.permissions import require
from app.models.user import User
from app.schemas.custom_order import (
    CustomCakeItem,
    CustomOrderContactUpdate,
    CustomOrderCreate,
    CustomOrderDeliveryChoice,
    CustomOrderDeliveryQuote,
    CustomOrderDeliveryQuotes,
    CustomOrderRecipeUpdate,
    CustomOrderResponse,
    CustomOrdersStatus,
    CustomOrderUpdate,
    PaginatedCustomOrders,
)
from app.services import audit_service
from app.services.orders import custom_order_invoice, custom_order_service

router = APIRouter()

_MANAGE = "orders.custom.manage"


async def _respond(db: AsyncSession, order_id) -> CustomOrderResponse:
    order, custom = await custom_order_service.get_by_id(db, order_id)
    return await custom_order_service.to_response(db, order, custom)


async def _audit(db, request, admin: User, action: str, order, changes=None) -> None:
    await audit_service.log_action(
        db,
        action=action,
        entity_type="custom_order",
        entity_id=str(order.id),
        entity_label=order.order_number,
        admin=admin,
        changes=changes or {},
        request=request,
    )


@router.get("/status", response_model=CustomOrdersStatus)
async def channel_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """Whether the channel is set up, so the console can say what is missing."""
    try:
        cfg = await custom_order_service.config(db)
    except custom_order_service.CustomOrdersDisabled:
        return CustomOrdersStatus(enabled=False, branch_id=None, branch_name=None)
    return CustomOrdersStatus(
        enabled=True, branch_id=cfg.branch.id, branch_name=cfg.branch.name
    )


@router.get("/items", response_model=list[CustomCakeItem])
async def recipe_items(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """What a recipe may use, with the kitchen's stock of each."""
    return [
        custom_order_service.cake_item_out(item, on_hand)
        for item, on_hand in await custom_order_service.custom_cake_items(db)
    ]


@router.get("", response_model=PaginatedCustomOrders)
async def list_custom_orders(
    status_group: str | None = Query(
        None, pattern="^(pending|packed|on_the_way|delivered|cancelled)$"
    ),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    rows, total = await custom_order_service.list_orders(
        db,
        status_group=status_group,
        date_from=date_from,
        date_to=date_to,
        q=q,
        page=page,
        per_page=per_page,
    )
    return PaginatedCustomOrders(
        items=await custom_order_service.to_list_items(db, rows),
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page if total else 0,
    )


@router.post(
    "", response_model=CustomOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_custom_order(
    request: Request,
    data: CustomOrderCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Take a custom order. It is confirmed at once and its kitchen docket is
    sent to the custom-orders branch's registers."""
    from app.services import push_service

    order = await custom_order_service.create(db, data, user=admin, via="admin")
    await _audit(db, request, admin, "CREATE", order)
    await push_service.notify_custom_order_created(db, order)
    return await _respond(db, order.id)


@router.get("/{order_number}", response_model=CustomOrderResponse)
async def get_custom_order(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    order, custom = await custom_order_service.get(db, order_number)
    return await custom_order_service.to_response(db, order, custom)


@router.put("/{order_number}", response_model=CustomOrderResponse)
async def update_custom_order(
    request: Request,
    order_number: str,
    data: CustomOrderUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    order, custom = await custom_order_service.get(db, order_number)
    await custom_order_service.update(db, order, custom, data, user=admin)
    await _audit(db, request, admin, "UPDATE", order, data.model_dump(mode="json"))
    return await _respond(db, order.id)


@router.put("/{order_number}/contact", response_model=CustomOrderResponse)
async def update_custom_order_contact(
    request: Request,
    order_number: str,
    data: CustomOrderContactUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    order, _ = await custom_order_service.get(db, order_number)
    await custom_order_service.update_contact(
        db, order, customer=data.customer, address=data.address
    )
    await _audit(db, request, admin, "UPDATE_CONTACT", order)
    return await _respond(db, order.id)


@router.put("/{order_number}/recipe", response_model=CustomOrderResponse)
async def update_custom_order_recipe(
    request: Request,
    order_number: str,
    data: CustomOrderRecipeUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    order, custom = await custom_order_service.get(db, order_number)
    await custom_order_service.update_recipe(db, order, custom, data.recipe)
    await _audit(
        db, request, admin, "UPDATE_RECIPE", order, data.model_dump(mode="json")
    )
    return await _respond(db, order.id)


@router.post("/{order_number}/pack", response_model=CustomOrderResponse)
async def pack_custom_order(
    request: Request,
    order_number: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Boxed and ready: the recipe is consumed from the kitchen's stock."""
    order, _ = await custom_order_service.get(db, order_number)
    await custom_order_service.pack(db, order, user=admin, via="admin")
    await _audit(db, request, admin, "PACK", order)
    return await _respond(db, order.id)


@router.post("/{order_number}/collected", response_model=CustomOrderResponse)
async def mark_custom_order_collected(
    request: Request,
    order_number: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    order, _ = await custom_order_service.get(db, order_number)
    await custom_order_service.mark_collected(db, order, user=admin, via="admin")
    await _audit(db, request, admin, "COLLECTED", order)
    return await _respond(db, order.id)


@router.post("/{order_number}/cancel", response_model=CustomOrderResponse)
async def cancel_custom_order(
    request: Request,
    order_number: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    order, _ = await custom_order_service.get(db, order_number)
    await custom_order_service.cancel(db, order, user=admin, via="admin")
    await _audit(db, request, admin, "CANCEL", order)
    return await _respond(db, order.id)


# ─── Delivery (admin only) ─────────────────────────────────────────────────────


@router.get("/{order_number}/delivery-quotes", response_model=CustomOrderDeliveryQuotes)
async def custom_order_delivery_quotes(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    """Live Slider (car) and Lalamove fares from the custom-orders branch to the
    customer's pin, each with its fare or the reason it cannot go."""
    from app.services.delivery import fulfilment_reassignment

    order, _custom = await custom_order_service.get(db, order_number)
    reason = custom_order_service.delivery_unavailable_reason(order)
    if reason:
        return CustomOrderDeliveryQuotes(quotes=[], unavailable_reason=reason)
    quotes = []
    for target in sorted(fulfilment_reassignment.FIRST_BOOKING_TARGETS, reverse=True):
        priced = await fulfilment_reassignment.price_target(db, order, target)
        quotes.append(
            CustomOrderDeliveryQuote(
                provider=target,
                available=priced.cost is not None,
                fare=priced.cost,
                reason=priced.reason,
                quotation_id=priced.quotation_id,
                expires_at=priced.expires_at,
            )
        )
    return CustomOrderDeliveryQuotes(quotes=quotes, unavailable_reason=None)


@router.post("/{order_number}/delivery", response_model=CustomOrderResponse)
async def choose_custom_order_delivery(
    request: Request,
    order_number: str,
    data: CustomOrderDeliveryChoice,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Book Slider (car) or Lalamove, or record a third-party courier and its
    fee (which finishes the order as delivered)."""
    from app.models.order_status_event import StatusSourceEnum, acting_as
    from app.services.delivery import fulfilment_reassignment

    order, _custom = await custom_order_service.get(db, order_number)
    if data.mode == "third_party":
        await custom_order_service.finish_third_party(
            db, order, courier_fee=data.courier_fee, user=admin
        )
    else:
        with acting_as(
            StatusSourceEnum.ADMIN.value, actor_id=admin.id, actor_label=admin.email
        ):
            await fulfilment_reassignment.book_first(
                db, order, data.mode, quotation_id=data.quotation_id
            )
    await _audit(db, request, admin, "DELIVERY", order, data.model_dump(mode="json"))
    return await _respond(db, order.id)


# ─── Invoice (admin only) ──────────────────────────────────────────────────────


@router.get("/{order_number}/invoice.pdf")
async def view_custom_order_invoice(
    order_number: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require(_MANAGE)),
):
    order, _custom = await custom_order_service.get(db, order_number)
    pdf = await custom_order_invoice.render_invoice_pdf(db, order)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{order.order_number}.pdf"'},
    )


@router.post("/{order_number}/invoice/send", response_model=CustomOrderResponse)
async def send_custom_order_invoice(
    request: Request,
    order_number: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require(_MANAGE)),
):
    """Email the invoice PDF to the customer, copying the owners."""
    order, _custom = await custom_order_service.get(db, order_number)
    result = await custom_order_invoice.send_invoice(db, order)
    await _audit(db, request, admin, "SEND_INVOICE", order, {"result": result})
    if result.get("status") != "sent":
        # The attempt is journalled in `email_logs` either way; the admin who
        # pressed Send is told it did not go rather than shown a success.
        raise BadGatewayError(
            f"The invoice email was not sent: {result.get('error') or 'unknown error'}"
        )
    return await _respond(db, order.id)
