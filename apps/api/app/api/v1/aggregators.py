"""The one write path into `aggregator_session`, and a health read.

The ingest itself takes no HTTP — it is a background loop. This router exists so
the bootstrap/warmer worker (which runs a browser elsewhere, off the app VM) can
hand a freshly captured session in over HTTPS, exactly the way the standalone
scraper pushed to `/api/ingest/bulk`. It authenticates on a shared bearer
(`AGGREGATOR_SESSION_PUSH_TOKEN`) rather than a user login, because the caller
is a machine, not a person — and an unset token closes the path rather than
leaving it open.

The health read is behind the same reporting permission the aggregator dashboard
uses, so an operator can see which sessions are live without holding the push
token.
"""

from __future__ import annotations

import hmac
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.core.exceptions import (
    BadRequestError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.core.permissions import require
from app.models.aggregator import (
    AGGREGATOR_CHANNELS,
    CHANNEL_KEETA,
    MATCH_MATCHED,
    MATCH_NO_MAKER_SIDE,
    MATCH_UNMATCHED_AGG,
    AggregatorReconciliation,
    AggregatorSession,
)
from app.models.branch import Branch
from app.schemas.aggregator import (
    AggregatorReconciliationList,
    AggregatorReconciliationOut,
    AggregatorSessionPush,
    AggregatorSessionResponse,
    KeetaOrdersPush,
    KeetaOrdersResult,
    ReconSummaryOut,
    ReconSummaryRow,
)
from app.services.aggregators import crypto, session_store
from app.services.aggregators.ingest import _upsert_order
from app.services.providers import keeta_provider

router = APIRouter()

#: A commission variance counts as raised at the same 1-fil tolerance the
#: reconciler flags it, so the summary count matches the `commission_variance`
#: flag on the rows exactly.
_COMMISSION_TOL = Decimal("0.01")


def _require_push_token(authorization: str | None = Header(None)) -> None:
    """Verify the worker's shared bearer in constant time.

    An unset `AGGREGATOR_SESSION_PUSH_TOKEN` is a closed door, not an open one —
    the same fail-closed posture the ingest flag takes.
    """
    expected = settings.AGGREGATOR_SESSION_PUSH_TOKEN
    if not expected:
        raise UnauthorizedError("aggregator session push is not configured")
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not hmac.compare_digest(presented, expected):
        raise UnauthorizedError("invalid aggregator session push token")


@router.post("/session", response_model=AggregatorSessionResponse)
async def push_session(
    body: AggregatorSessionPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> AggregatorSession:
    """Store (or replace) the session for one channel, sealed at rest."""
    if body.channel not in AGGREGATOR_CHANNELS:
        raise BadRequestError(f"unknown aggregator channel: {body.channel}")
    if not crypto.is_configured():
        raise ServiceUnavailableError(
            "AGGREGATOR_CONFIG_ENCRYPTION_KEY is unset; cannot store a session"
        )
    return await session_store.upsert_bootstrap(
        db,
        channel=body.channel,
        account_ref=body.account_ref,
        cookies=body.cookies,
        tokens=body.tokens,
        header_profile=body.header_profile,
        token_expires_at=body.token_expires_at,
        cookie_expires_at=body.cookie_expires_at,
    )


@router.get(
    "/sessions",
    response_model=list[AggregatorSessionResponse],
    dependencies=[Depends(require("reports.sales"))],
)
async def list_sessions(
    db: AsyncSession = Depends(get_db),
) -> list[AggregatorSession]:
    """Session health per channel — the monitoring read (no secrets exposed)."""
    rows = await db.scalars(
        select(AggregatorSession).order_by(AggregatorSession.channel)
    )
    return list(rows)


@router.post("/keeta/orders", response_model=KeetaOrdersResult)
async def push_keeta_orders(
    body: KeetaOrdersPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> KeetaOrdersResult:
    """Ingest a batch of in-page-fetched Keeta order payloads.

    Keeta is not on the httpx sweep (its `mtgsig` signing lives in the page), so
    the bootstrap worker fetches each `getOrders` response in-page and pushes the
    raw payloads here. Each is parsed by the Keeta provider and upserted exactly
    as the sweep upserts the other channels. Returns the count of orders written.
    """
    ingested = 0
    for payload in body.payloads:
        for order in keeta_provider.provider.parse_orders(payload):
            await _upsert_order(db, CHANNEL_KEETA, order)
            ingested += 1
    return KeetaOrdersResult(ingested=ingested)


def _flagged_clause():
    """An order is flagged iff an item/refund flag is set or `flags` is non-empty."""
    return or_(
        AggregatorReconciliation.item_flag.is_(True),
        AggregatorReconciliation.refund_flag.is_(True),
        func.coalesce(func.jsonb_array_length(AggregatorReconciliation.flags), 0) > 0,
    )


@router.get(
    "/reconciliation",
    response_model=AggregatorReconciliationList,
    dependencies=[Depends(require("reports.sales"))],
)
async def list_reconciliation(
    channel: str | None = Query(None),
    branch_id: UUID | None = Query(None),
    match_status: str | None = Query(None),
    flagged: bool | None = Query(
        None, description="Only orders carrying an item/refund flag or a flag code"
    ),
    limit: int = Query(50, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AggregatorReconciliationList:
    """The reconciliation rows for the dashboard, newest first, with the total."""
    stmt = select(AggregatorReconciliation, Branch.name).outerjoin(
        Branch, Branch.id == AggregatorReconciliation.branch_id
    )
    if channel:
        stmt = stmt.where(AggregatorReconciliation.channel == channel)
    if branch_id:
        stmt = stmt.where(AggregatorReconciliation.branch_id == branch_id)
    if match_status:
        stmt = stmt.where(AggregatorReconciliation.match_status == match_status)
    if flagged is True:
        stmt = stmt.where(_flagged_clause())
    elif flagged is False:
        stmt = stmt.where(~_flagged_clause())

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    stmt = (
        stmt.order_by(AggregatorReconciliation.reconciled_at.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    items = [
        AggregatorReconciliationOut(
            id=recon.id,
            channel=recon.channel,
            external_order_id=recon.external_order_id,
            branch_id=recon.branch_id,
            branch_name=branch_name,
            mm_order_id=recon.mm_order_id,
            match_status=recon.match_status,
            item_flag=recon.item_flag,
            refund_flag=recon.refund_flag,
            refund_agg=recon.refund_agg,
            refund_mm=recon.refund_mm,
            commission_expected=recon.commission_expected,
            commission_actual=recon.commission_actual,
            commission_variance=recon.commission_variance,
            commission_rate_effective=recon.commission_rate_effective,
            total_agg=recon.total_agg,
            total_mm=recon.total_mm,
            amount_variance=recon.amount_variance,
            flags=list(recon.flags or []),
            reconciled_at=recon.reconciled_at,
        )
        for recon, branch_name in rows
    ]
    return AggregatorReconciliationList(items=items, total=total)


@router.get(
    "/reconciliation/summary",
    response_model=ReconSummaryOut,
    dependencies=[Depends(require("reports.sales"))],
)
async def reconciliation_summary(
    channel: str | None = Query(None),
    branch_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ReconSummaryOut:
    """Per-channel reconciliation tallies plus one combined total, from SQL."""
    r = AggregatorReconciliation
    commission_variance_raised = func.abs(r.commission_variance) > _COMMISSION_TOL
    aggregates = (
        func.count().label("total"),
        func.count().filter(r.match_status == MATCH_MATCHED).label("matched"),
        func.count()
        .filter(r.match_status == MATCH_UNMATCHED_AGG)
        .label("unmatched_agg"),
        func.count()
        .filter(r.match_status == MATCH_NO_MAKER_SIDE)
        .label("no_maker_side"),
        func.count().filter(r.item_flag.is_(True)).label("item_flags"),
        func.count().filter(r.refund_flag.is_(True)).label("refund_flags"),
        func.count()
        .filter(commission_variance_raised)
        .label("commission_variance_count"),
        func.sum(r.commission_actual).label("commission_actual_sum"),
        func.avg(r.commission_rate_effective).label("avg_rate_effective"),
    )

    def _filtered(stmt):
        if channel:
            stmt = stmt.where(r.channel == channel)
        if branch_id:
            stmt = stmt.where(r.branch_id == branch_id)
        return stmt

    per_channel_stmt = _filtered(
        select(r.channel.label("channel"), *aggregates).group_by(r.channel)
    ).order_by(r.channel)
    by_channel = [
        ReconSummaryRow(
            channel=row.channel,
            total=row.total,
            matched=row.matched,
            unmatched_agg=row.unmatched_agg,
            no_maker_side=row.no_maker_side,
            item_flags=row.item_flags,
            refund_flags=row.refund_flags,
            commission_variance_count=row.commission_variance_count,
            commission_actual_sum=row.commission_actual_sum,
            avg_rate_effective=row.avg_rate_effective,
        )
        for row in (await db.execute(per_channel_stmt)).all()
    ]

    totals_row = (await db.execute(_filtered(select(*aggregates)))).one()
    totals = ReconSummaryRow(
        channel="all",
        total=totals_row.total,
        matched=totals_row.matched,
        unmatched_agg=totals_row.unmatched_agg,
        no_maker_side=totals_row.no_maker_side,
        item_flags=totals_row.item_flags,
        refund_flags=totals_row.refund_flags,
        commission_variance_count=totals_row.commission_variance_count,
        commission_actual_sum=totals_row.commission_actual_sum,
        avg_rate_effective=totals_row.avg_rate_effective,
    )
    return ReconSummaryOut(by_channel=by_channel, totals=totals)
