"""The write paths into aggregator auth, and the health reads.

The ingest itself takes no HTTP — it is a background loop. This router exists so
the bootstrap/warmer worker can hand in a freshly captured session
(`POST /session`) and the durable login recipe (`PUT /account`). Both
authenticate on a shared bearer (`AGGREGATOR_SESSION_PUSH_TOKEN`) rather than a
user login, because the caller is a machine — and an unset token closes the
path rather than leaving it open.

The health reads are behind the same reporting permission the aggregator
dashboard uses. Session health never returns cookies; account health never
returns the password.
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
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.core.permissions import require
from app.models.aggregator import (
    AGGREGATOR_CHANNELS,
    MATCH_MATCHED,
    MATCH_NO_MAKER_SIDE,
    MATCH_UNMATCHED_AGG,
    AggregatorBranchMap,
    AggregatorReconciliation,
    AggregatorSession,
    AggregatorSyncRun,
)
from app.models.branch import Branch
from app.schemas.aggregator import (
    AggregatorAccountPublic,
    AggregatorAccountPush,
    AggregatorBranchMapIn,
    AggregatorBranchMapOut,
    AggregatorReconciliationList,
    AggregatorReconciliationOut,
    AggregatorSessionPush,
    AggregatorSessionResponse,
    AggregatorSyncRunList,
    AggregatorSyncRunOut,
    AggregatorWorkerAccount,
    AggregatorWorkerSession,
    DeliverooFinancePush,
    DeliverooFinanceResult,
    KeetaFinancePush,
    KeetaFinanceResult,
    KeetaOrdersPush,
    KeetaOrdersResult,
    ReconSummaryOut,
    ReconSummaryRow,
    SettlementReconOut,
)
from app.services.aggregators import (
    account_store,
    crypto,
    ingest,
    mapping,
    session_store,
    settlement_reconcile,
)

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
    tokens = dict(body.tokens)
    header_profile = dict(body.header_profile)
    if body.channel == "noon":
        acct = await account_store.load(db, body.channel, body.account_ref)
        if acct is not None:
            merged = session_store.merge_noon_scope_from_extras(
                session_store.LoadedSession(
                    channel=body.channel,
                    account_ref=body.account_ref,
                    cookies=body.cookies,
                    tokens=tokens,
                    header_profile=header_profile,
                ),
                acct.extras or {},
            )
            tokens = merged.tokens
            header_profile = merged.header_profile
    return await session_store.upsert_bootstrap(
        db,
        channel=body.channel,
        account_ref=body.account_ref,
        cookies=body.cookies,
        tokens=tokens,
        header_profile=header_profile,
        storage_state=body.storage_state,
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


@router.get(
    "/worker/sessions",
    response_model=list[AggregatorWorkerSession],
)
async def hydrate_sessions(
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Decrypted sessions for the worker to write as local storage_state.

    Authenticated with the push bearer, not a user login: this is the deploy
    and restart path, and it carries live credentials. The admin health read
    at GET /sessions never returns these blobs.
    """
    if not crypto.is_configured():
        raise ServiceUnavailableError(
            "AGGREGATOR_CONFIG_ENCRYPTION_KEY is unset; cannot read a session"
        )
    return await session_store.list_worker_bundles(db)


@router.put("/account", response_model=AggregatorAccountPublic)
async def upsert_account(
    body: AggregatorAccountPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Store (or replace) the login recipe for one channel, sealed at rest.

    Worker/CLI write path. The admin console uses POST `/accounts`.
    """
    return await _store_account(body, db)


@router.post(
    "/accounts",
    response_model=AggregatorAccountPublic,
    dependencies=[Depends(require("catalogue.manage"))],
)
async def upsert_account_admin(
    body: AggregatorAccountPush,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create or update a login recipe from the admin Logins tab."""
    return await _store_account(body, db)


async def _store_account(body: AggregatorAccountPush, db: AsyncSession) -> dict:
    if body.channel not in AGGREGATOR_CHANNELS:
        raise BadRequestError(f"unknown aggregator channel: {body.channel}")
    if not crypto.is_configured():
        raise ServiceUnavailableError(
            "AGGREGATOR_CONFIG_ENCRYPTION_KEY is unset; cannot store an account"
        )
    mailbox = (
        body.mailbox.model_dump(exclude_unset=True)
        if body.mailbox is not None
        else None
    )
    await account_store.upsert(
        db,
        channel=body.channel,
        account_ref=body.account_ref,
        login_method=body.login_method,
        email=body.email,
        password=body.password,
        mailbox=mailbox,
        clear_mailbox=body.clear_mailbox,
        extras=body.extras,
    )
    loaded = await account_store.load(db, body.channel, body.account_ref)
    assert loaded is not None
    return account_store.public_view(loaded)


@router.get(
    "/accounts",
    response_model=list[AggregatorAccountPublic],
    dependencies=[Depends(require("catalogue.manage"))],
)
async def list_accounts(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Login recipes per channel — method, OTP, mailbox host; never a password."""
    if not crypto.is_configured():
        return []
    return await account_store.list_public(db)


@router.get(
    "/worker/accounts",
    response_model=list[AggregatorWorkerAccount],
)
async def hydrate_accounts(
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Decrypted login recipes for the worker to drive a portal.

    Authenticated with the push bearer. The admin health read at GET
    `/accounts` never returns passwords.
    """
    if not crypto.is_configured():
        raise ServiceUnavailableError(
            "AGGREGATOR_CONFIG_ENCRYPTION_KEY is unset; cannot read an account"
        )
    return await account_store.list_worker(db)


def _branch_map_out(row: AggregatorBranchMap, branch_name: str | None):
    return AggregatorBranchMapOut(
        id=row.id,
        channel=row.channel,
        branch_id=row.branch_id,
        branch_name=branch_name,
        external_outlet_id=row.external_outlet_id,
        external_brand_id=row.external_brand_id,
        external_company_id=row.external_company_id,
        channel_ref=row.channel_ref,
        is_active=row.is_active,
    )


@router.get(
    "/branch-map",
    response_model=list[AggregatorBranchMapOut],
    dependencies=[Depends(require("catalogue.manage"))],
)
async def list_branch_map(
    channel: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[AggregatorBranchMapOut]:
    """Every outlet↔branch mapping, for the admin to view and edit."""
    stmt = select(AggregatorBranchMap, Branch.name).outerjoin(
        Branch, Branch.id == AggregatorBranchMap.branch_id
    )
    if channel:
        stmt = stmt.where(AggregatorBranchMap.channel == channel)
    stmt = stmt.order_by(AggregatorBranchMap.channel)
    rows = (await db.execute(stmt)).all()
    return [_branch_map_out(m, name) for m, name in rows]


@router.post(
    "/branch-map",
    response_model=AggregatorBranchMapOut,
    dependencies=[Depends(require("catalogue.manage"))],
)
async def upsert_branch_map_row(
    body: AggregatorBranchMapIn,
    db: AsyncSession = Depends(get_db),
) -> AggregatorBranchMapOut:
    """Create or update one mapping — the DB is the source of truth, edited here."""
    if body.channel not in AGGREGATOR_CHANNELS:
        raise BadRequestError(f"unknown aggregator channel: {body.channel}")
    branch = await db.get(Branch, body.branch_id)
    if branch is None:
        raise NotFoundError("branch not found")
    await mapping.upsert_branch_map(
        db,
        channel=body.channel,
        branch_id=body.branch_id,
        external_outlet_id=body.external_outlet_id,
        external_brand_id=body.external_brand_id,
        external_company_id=body.external_company_id,
        channel_ref=body.channel_ref,
        is_active=body.is_active,
    )
    row = await db.scalar(
        select(AggregatorBranchMap).where(
            AggregatorBranchMap.channel == body.channel,
            AggregatorBranchMap.branch_id == body.branch_id,
        )
    )
    return _branch_map_out(row, branch.name)


@router.delete(
    "/branch-map/{map_id}",
    dependencies=[Depends(require("catalogue.manage"))],
)
async def delete_branch_map_row(
    map_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Remove a mapping — the branch stops being enumerated on that channel."""
    row = await db.get(AggregatorBranchMap, map_id)
    if row is None:
        raise NotFoundError("mapping not found")
    await db.delete(row)
    return {"deleted": str(map_id)}


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
    ingested = await ingest.ingest_keeta_payloads(db, body.payloads)
    return KeetaOrdersResult(ingested=ingested)


@router.post("/keeta/finance", response_model=KeetaFinanceResult)
async def push_keeta_finance(
    body: KeetaFinancePush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> KeetaFinanceResult:
    """Ingest a batch of in-page-fetched Keeta finance payloads.

    Keeta's finance data is fetched in-page by the bootstrap worker (where the
    portal's JS signs the request) and pushed here. Each payload is parsed by
    `keeta_provider.parse_finance` into statements and payouts and upserted.
    When the payload is only download-task metadata (figures live in PDF invoices),
    the parse returns empty lists with a truncation note — the response still
    returns 200 with zero counts and includes the note so the worker can log it.
    """
    statements, payouts = await ingest.ingest_keeta_finance_payloads(db, body.payloads)
    return KeetaFinanceResult(statements=statements, payouts=payouts)


@router.post("/deliveroo/finance", response_model=DeliverooFinanceResult)
async def push_deliveroo_finance(
    body: DeliverooFinancePush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> DeliverooFinanceResult:
    """Ingest a batch of in-page-fetched Deliveroo invoice payloads.

    Deliveroo's invoice list replays over httpx, but the invoice download 403s
    behind Cloudflare, so the bootstrap worker downloads each statement CSV and
    PDF in-page (where the browser's `cf_clearance` applies) and pushes the raw
    payloads here. Each is parsed by `deliveroo_provider.parse_pushed_finance`
    into a statement (with per-order lines and an archived VAT PDF) and upserted
    exactly as the httpx finance sweep does. Returns the statement and line
    counts written.
    """
    statements, lines = await ingest.ingest_deliveroo_finance_payloads(
        db, body.payloads
    )
    return DeliverooFinanceResult(statements=statements, lines=lines)


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


def _run_out(run: AggregatorSyncRun) -> AggregatorSyncRunOut:
    """Lift the stats blob's headline figures onto flat fields for the table."""
    stats = run.stats or {}
    return AggregatorSyncRunOut(
        id=run.id,
        channel=run.channel,
        mode=run.mode,
        status=run.status,
        from_date=run.from_date,
        to_date=run.to_date,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        stats=stats or None,
        orders_retrieved=stats.get("orders_retrieved"),
        orders_promoted=stats.get("orders_promoted"),
        orders_promoted_new=stats.get("orders_promoted_new"),
        orders_promoted_existing=stats.get("orders_promoted_existing"),
        orders_not_promoted=stats.get("orders_not_promoted"),
        pct_promoted=stats.get("pct_promoted"),
        statements_total=stats.get("statements_total"),
        payouts_total=stats.get("payouts_total"),
        invoices_total=stats.get("invoices_total"),
    )


@router.get(
    "/runs",
    response_model=AggregatorSyncRunList,
    dependencies=[Depends(require("reports.sales"))],
)
async def list_sync_runs(
    channel: str | None = Query(None),
    mode: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AggregatorSyncRunList:
    """The ingest run trail for the admin Runs table — newest first, with the
    total for the filter. Each row is one channel×trigger: when it ran, whether it
    succeeded (and why not), and what it retrieved/promoted."""
    stmt = select(AggregatorSyncRun)
    if channel:
        stmt = stmt.where(AggregatorSyncRun.channel == channel)
    if mode:
        stmt = stmt.where(AggregatorSyncRun.mode == mode)
    if status:
        stmt = stmt.where(AggregatorSyncRun.status == status)

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    stmt = (
        stmt.order_by(
            AggregatorSyncRun.started_at.desc().nullslast(),
            AggregatorSyncRun.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    runs = (await db.execute(stmt)).scalars().all()
    return AggregatorSyncRunList(items=[_run_out(r) for r in runs], total=total)


@router.get(
    "/reconciliation/settlement",
    response_model=SettlementReconOut,
    dependencies=[Depends(require("reports.sales"))],
)
async def settlement_reconciliation_report(
    channel: str = Query(..., description="Aggregator channel to reconcile"),
    from_date: str | None = Query(
        None, alias="from", description="ISO date; include statements from this period"
    ),
    to_date: str | None = Query(
        None, alias="to", description="ISO date; include statements up to this period"
    ),
    db: AsyncSession = Depends(get_db),
) -> SettlementReconOut:
    """Layer A: per-statement sales↔settlement↔payout rollup for one channel.

    Read-only. For each statement in the window it reports the sales total (the
    orders that settled on it), the settlement total (its order-grain lines and
    its own declared net payable), the linked payout, and the two variances;
    plus a per-payout rollup that checks a batch transfer against the summed net
    payable of the statements it cleared.
    """
    if channel not in AGGREGATOR_CHANNELS:
        raise BadRequestError(f"unknown aggregator channel: {channel}")
    result = await settlement_reconcile.settlement_reconciliation(
        db, channel, from_date=from_date, to_date=to_date
    )
    return SettlementReconOut.model_validate(result)
