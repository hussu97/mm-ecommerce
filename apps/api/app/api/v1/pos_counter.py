"""
Local-first counter checkout — the register's bundle, sale sync and promote.

Mounted at `/pos/counter` on BOTH the register app (`pos_router.py`) and the
main API (`router.py`). Contracts: `app/schemas/pos_counter.py`.

`GET /pos/counter/bundle` (device token)
    200 `CounterBundleResponse`, headers `ETag: "{hash}.{envelope_tag}"`,
    `Cache-Control: no-cache`, `X-Counter-Bundle-Hash`, `X-Counter-Mode`.
    Send the last ETag as `If-None-Match` → 304 (no body, same headers) when
    neither the bundle nor the envelope's 86 list / mode / prefix / business
    date changed. Every call also assigns the terminal its ticket prefix on first
    use and records the bundle for re-pricing.

`POST /pos/counter/sales` (device token) → `CounterSaleResponse`
    201 ingested · 200 replayed (same id, same fingerprint) · 202 quarantined
    (structurally unbookable; clear it from the outbox) · 409
    `code=counter_sale_conflict` (same id, different content — park it) · 426
    `code=upgrade_required` (build below `COUNTER_LOCAL_FIRST_MIN_BUILD`) · 422
    body validation (park it) · 401 device token.

`POST /pos/counter/shadow` (device token) → `CounterShadowResult`
    Shadow mode: the register's local figures for a SERVER check; compared,
    recorded on the order (`pricing_audit.shadow`, flag `shadow_mismatch`) and
    alerted when they differ. 404 for an order at another branch; 426 below the
    minimum build.

`POST /pos/counter/promote` (staff token, `pos.register.access`)
    → 201 `PosOrderResponse`: the untendered local check as an ordinary open
    server check with the same id (200 when it already exists). 426 below the
    minimum build; 4xx on a line the server rules refuse, like `POST /items`.

`admin_router` (main API only, `/pos/counter-sync`): the console's Counter sync
page.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UpgradeRequiredError,
)
from app.core.permissions import ensure, require
from app.core.pos_builds import COUNTER_LOCAL_FIRST_MIN_BUILD, build_at_least
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.device import Device
from app.models.order import Order
from app.models.pos_counter import CounterSaleQuarantine
from app.models.till import Till
from app.models.user import User
from app.schemas.pos_counter import (
    CounterBundleResponse,
    CounterDeviceSyncRow,
    CounterPromoteRequest,
    CounterQuarantineRow,
    CounterSaleRequest,
    CounterSaleResponse,
    CounterShadowReport,
    CounterShadowResult,
    CounterSyncOrderRow,
    CounterSyncOverview,
    QuarantineResolveRequest,
)
from app.schemas.pos_order import PosOrderResponse
from app.services.pos import counter_bundle_service, counter_ingest_service

from .devices import get_current_device
from .pos_orders import _serialise

router = APIRouter()
admin_router = APIRouter()


async def require_local_first_build(
    x_app_build: str | None = Header(None, alias="X-App-Build"),
) -> str | None:
    """426 below `COUNTER_LOCAL_FIRST_MIN_BUILD` (or with no build reported).

    A dependency rather than a first statement so it is decided before the
    body is validated: an old build is told to update, not that its body is
    malformed."""
    if not build_at_least(x_app_build, COUNTER_LOCAL_FIRST_MIN_BUILD):
        raise UpgradeRequiredError(
            f"Local-first counter sync needs app build {COUNTER_LOCAL_FIRST_MIN_BUILD}"
            " or later"
        )
    return x_app_build


def _conflict(message: str) -> ConflictError:
    error = ConflictError(message)
    # Coded so the register parks the sale on the code, not the wording.
    error.code = "counter_sale_conflict"
    return error


# ─── The bundle ───────────────────────────────────────────────────────────────


@router.get(
    "/bundle",
    response_model=CounterBundleResponse,
    responses={304: {"description": "Not modified — the ETag still holds"}},
)
async def get_bundle(
    device: Device = Depends(get_current_device),
    x_app_build: str | None = Header(None, alias="X-App-Build"),
    if_none_match: str | None = Header(None, alias="If-None-Match"),
    db: AsyncSession = Depends(get_db),
):
    """The config bundle this terminal prices counter sales from. See the
    module docstring for the ETag/304 behaviour."""
    served = await counter_bundle_service.serve(
        db, device=device, build_number=x_app_build
    )
    headers = {
        "ETag": served.etag,
        "Cache-Control": "no-cache",
        "X-Counter-Bundle-Hash": served.hash,
        "X-Counter-Mode": served.envelope.counter_local_first,
    }
    if if_none_match and if_none_match.strip() == served.etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    # Served as the very dict that was hashed (and stored), not re-validated
    # through the response model, so what the register holds is byte-for-byte
    # what `hash` names.
    return JSONResponse(
        content={
            "hash": served.hash,
            "bundle": served.body,
            "envelope": served.envelope.model_dump(mode="json"),
        },
        headers=headers,
    )


# ─── Sales ────────────────────────────────────────────────────────────────────


@router.post(
    "/sales",
    response_model=CounterSaleResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": CounterSaleResponse, "description": "Replayed"},
        202: {"model": CounterSaleResponse, "description": "Quarantined"},
        409: {"description": "Same id, different sale (code counter_sale_conflict)"},
        426: {"description": "App build too old (code upgrade_required)"},
    },
)
async def post_sale(
    sale: CounterSaleRequest,
    device: Device = Depends(get_current_device),
    _build: str | None = Depends(require_local_first_build),
    db: AsyncSession = Depends(get_db),
):
    """Book one local-first counter sale. See `counter_ingest_service`."""
    try:
        result = await counter_ingest_service.ingest(db, device=device, sale=sale)
    except counter_ingest_service.SaleConflict as exc:
        raise _conflict(str(exc)) from exc
    return JSONResponse(
        status_code=result.status_code,
        content=result.response.model_dump(mode="json"),
    )


@router.post(
    "/promote",
    response_model=PosOrderResponse,
    status_code=status.HTTP_201_CREATED,
    responses={426: {"description": "App build too old (code upgrade_required)"}},
)
async def promote_check(
    data: CounterPromoteRequest,
    _build: str | None = Depends(require_local_first_build),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.register.access")),
):
    """Move an untendered local check to the server as an open check."""
    if data.coupon_promotion_id is not None:
        # Imperative because it is conditional: only a check carrying a coupon
        # needs the coupon permission, and only the body says so.
        ensure(user, "pos.promotions.apply")
    branch = await db.get(Branch, data.branch_id)
    if branch is None or branch.deleted_at is not None:
        raise NotFoundError("Branch not found")
    till = None
    if data.till_id is not None:
        till = await db.get(Till, data.till_id)
        if till is None:
            raise NotFoundError("Till not found")
        if till.user_id != user.id and not user.is_admin:
            raise ForbiddenError("This till belongs to another user")
    existed = await db.get(Order, data.id) is not None
    try:
        order = await counter_ingest_service.promote(
            db,
            user=user,
            branch=branch,
            till=till,
            device_id=data.device_id,
            request=data,
        )
    except counter_ingest_service.SaleConflict as exc:
        raise _conflict(str(exc)) from exc
    payload = _serialise(order)
    return JSONResponse(
        status_code=status.HTTP_200_OK if existed else status.HTTP_201_CREATED,
        content=payload.model_dump(mode="json"),
    )


@router.post("/shadow", response_model=CounterShadowResult)
async def shadow_report(
    report: CounterShadowReport,
    device: Device = Depends(get_current_device),
    _build: str | None = Depends(require_local_first_build),
    db: AsyncSession = Depends(get_db),
):
    """Shadow mode: the register's local figures for a server check. Compared,
    and any difference recorded and alerted; the sale is never changed."""
    try:
        differences = await counter_ingest_service.shadow_compare(
            db, device=device, report=report
        )
    except LookupError as exc:
        raise NotFoundError("Order not found at this branch") from exc
    return CounterShadowResult(
        order_id=report.order_id,
        matches=not differences,
        differences=differences,
    )


# ─── Console: Counter sync ────────────────────────────────────────────────────


@admin_router.get("", response_model=CounterSyncOverview)
async def counter_sync_overview(
    branch_id: uuid.UUID | None = None,
    include_resolved: bool = False,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("orders.read")),
):
    """Synced counter sales that need a look — a pricing mismatch, an
    unverified re-price, a late till/day or any other ingest flag — the
    quarantined sales, and every terminal's unsynced counts."""
    limit = max(1, min(limit, 1000))
    # Any ingest/shadow flag, a late sync, or a re-price that did not verify.
    # Mirrors the partial index `ix_orders_counter_sync_attention` (284).
    attention = or_(
        func.cardinality(Order.ingest_flags) > 0,
        Order.ingested_late.is_(True),
        and_(
            Order.ingested_at.isnot(None),
            Order.pricing_status.is_distinct_from("verified"),
        ),
    )
    stmt = select(Order).where(attention)
    if branch_id:
        stmt = stmt.where(Order.branch_id == branch_id)
    orders = list(
        (await db.execute(stmt.order_by(Order.ingested_at.desc()).limit(limit)))
        .scalars()
        .all()
    )

    qstmt = select(CounterSaleQuarantine)
    if not include_resolved:
        qstmt = qstmt.where(CounterSaleQuarantine.resolved_at.is_(None))
    if branch_id:
        qstmt = qstmt.where(CounterSaleQuarantine.branch_id == branch_id)
    parked = list(
        (
            await db.execute(
                qstmt.order_by(CounterSaleQuarantine.received_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )

    dstmt = select(Device).where(
        Device.deleted_at.is_(None), Device.type.in_(["cashier", "sub_cashier"])
    )
    if branch_id:
        dstmt = dstmt.where(Device.branch_id == branch_id)
    devices = list((await db.execute(dstmt.order_by(Device.name))).scalars().all())

    return CounterSyncOverview(
        orders=[
            CounterSyncOrderRow(
                id=o.id,
                order_number=o.order_number,
                display_number=o.display_number,
                branch_id=o.branch_id,
                device_id=o.device_id,
                business_date=o.business_date,
                total=o.total,
                pricing_status=o.pricing_status,
                ingested_late=bool(o.ingested_late),
                ingest_flags=list(o.ingest_flags or []),
                closed_at=o.closed_at,
                ingested_at=o.ingested_at,
                pricing_audit=o.pricing_audit,
            )
            for o in orders
        ],
        quarantine=[
            CounterQuarantineRow(
                id=q.id,
                device_id=q.device_id,
                branch_id=q.branch_id,
                error=q.error,
                received_at=q.received_at,
                resolved_at=q.resolved_at,
                resolution_note=q.resolution_note,
                display_number=(q.payload or {}).get("display_number"),
                total=((q.payload or {}).get("totals") or {}).get("total"),
                payload=q.payload or {},
            )
            for q in parked
        ],
        devices=[
            CounterDeviceSyncRow(
                device_id=d.id,
                name=d.name,
                branch_id=d.branch_id,
                ticket_prefix=d.ticket_prefix,
                build_number=d.build_number,
                counter_mode=d.counter_mode,
                pending_sales=d.pending_sales,
                parked_sales=d.parked_sales,
                oldest_pending_sale_at=d.oldest_pending_sale_at,
                sync_reported_at=d.sync_reported_at,
                last_seen_at=d.last_seen_at,
            )
            for d in devices
        ],
    )


@admin_router.post("/quarantine/{sale_id}/resolve", response_model=CounterQuarantineRow)
async def resolve_quarantined_sale(
    sale_id: uuid.UUID,
    data: QuarantineResolveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("orders.manage")),
):
    """Mark a quarantined sale as dealt with (the note says how)."""
    row = await db.get(CounterSaleQuarantine, sale_id)
    if row is None:
        raise NotFoundError("Quarantined sale not found")
    row.resolved_at = utcnow()
    row.resolved_by_id = user.id
    row.resolution_note = data.note
    await db.flush()
    return CounterQuarantineRow(
        id=row.id,
        device_id=row.device_id,
        branch_id=row.branch_id,
        error=row.error,
        received_at=row.received_at,
        resolved_at=row.resolved_at,
        resolution_note=row.resolution_note,
        display_number=(row.payload or {}).get("display_number"),
        total=((row.payload or {}).get("totals") or {}).get("total"),
        payload=row.payload or {},
    )


__all__ = ["admin_router", "router"]
