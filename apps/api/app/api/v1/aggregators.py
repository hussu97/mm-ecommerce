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

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerting
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
    CHANNEL_KEETA,
    MATCH_MATCHED,
    MATCH_NO_MAKER_SIDE,
    MATCH_UNMATCHED_AGG,
    AggregatorBranchMap,
    AggregatorOrder,
    AggregatorReconciliation,
    AggregatorSession,
    AggregatorStatement,
    AggregatorStatementLine,
    AggregatorSyncRun,
    BranchHoursSyncRun,
)
from app.models.base import utcnow
from app.models.branch import Branch
from app.schemas.aggregator import (
    AggregatorAccountPublic,
    AggregatorAccountPush,
    AggregatorBranchMapIn,
    AggregatorBranchMapOut,
    AggregatorFeesRow,
    AggregatorFeesSummaryOut,
    AggregatorInvoiceUrl,
    AggregatorReauthBackoffPush,
    AggregatorReconciliationList,
    AggregatorReconciliationOut,
    AggregatorRunTriggerIn,
    AggregatorRunTriggerOut,
    AggregatorSessionHealthOut,
    AggregatorSessionPush,
    AggregatorSessionResponse,
    AggregatorStatementList,
    AggregatorStatementOut,
    AggregatorSyncRunList,
    AggregatorSyncRunOut,
    AggregatorWorkerAccount,
    AggregatorWorkerHealChannel,
    AggregatorWorkerSession,
    BranchHoursSyncRunList,
    BranchHoursSyncRunOut,
    DeliverooFinancePush,
    DeliverooFinanceResult,
    DeliverooMenuPush,
    DeliverooMenuResult,
    KeetaFinancePush,
    KeetaFinanceResult,
    KeetaHoursResult,
    KeetaHoursResultPush,
    KeetaHoursScheduleOut,
    KeetaHoursShop,
    KeetaMenuPush,
    KeetaMenuResult,
    KeetaOrdersPush,
    KeetaOrdersResult,
    ReconSummaryOut,
    ReconSummaryRow,
    SettlementReconOut,
)
from app.services import branch_hours_service
from app.services.aggregators import (
    account_store,
    crypto,
    ingest,
    mapping,
    session_store,
    settlement_reconcile,
    statement_docs,
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


@router.get(
    "/worker/needs-heal",
    response_model=list[AggregatorWorkerHealChannel],
)
async def worker_needs_heal(
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Status-only session list for the VM heal cron.

    Same push-token auth as GET `/worker/sessions`, but never decrypts a blob —
    the cron only needs to know whether any channel is not live before it
    starts a worker.
    """
    return await session_store.list_heal_channels(db)


@router.post("/worker/reauth-backoff", status_code=204)
async def worker_report_reauth_backoff(
    body: AggregatorReauthBackoffPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The worker publishing when it will next re-drive a dead channel's login.

    The heal daemon backs a failing login off (up to an hour) on its own volume,
    which the API cannot see; it reports the next-attempt time here so the ingest's
    reauth wait can bail out early instead of burning the full wait on a reauth the
    worker will not perform in time. `backoff_until=null` clears it. Same push-token
    auth as the session push; a no-op if the channel has never been bootstrapped.
    """
    if body.channel not in AGGREGATOR_CHANNELS:
        raise BadRequestError(f"unknown aggregator channel: {body.channel}")
    await session_store.set_reauth_backoff(
        db,
        body.channel,
        backoff_until=body.backoff_until,
        account_ref=body.account_ref,
    )
    return Response(status_code=204)


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
    # Keeta is push-only and is meant to be the freshest channel, but promotion ran
    # only on the hourly sweep — so a pushed order sat unlinked in aggregator_order
    # for up to an hour (and after a redeploy, until the next tick). Kick a tracked
    # promote+reconcile now so it lands in the MM tables within seconds. Idempotent
    # and backstopped by the hourly sweep; fires only when something was ingested.
    if ingested:
        ingest.trigger_promote_reconcile_in_background()
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


@router.post("/keeta/menu", response_model=KeetaMenuResult)
async def push_keeta_menu(
    body: KeetaMenuPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> KeetaMenuResult:
    """Store the in-page-fetched Keeta menu for the catalog sync.

    Keeta's menu API is mtgsig-signed in-page, so the worker reads it in the browser
    and pushes it here; it is stored as the keeta menu snapshot that
    `menu_readers._read_keeta_menu` parses for drift + mapping. Push transport only —
    like the orders/finance pushes."""
    from app.services.aggregators import catalog_sync

    await catalog_sync.store_worker_menu(db, target="keeta", payloads=body.payloads)
    return KeetaMenuResult(stored=True, shops=len(body.payloads))


@router.post("/deliveroo/menu", response_model=DeliverooMenuResult)
async def push_deliveroo_menu(
    body: DeliverooMenuPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> DeliverooMenuResult:
    """Store the in-page-captured Deliveroo menu + opening hours for the catalog sync.

    Deliveroo's webrom menu is behind Cloudflare + a webrom token, so the worker
    captures it (and the hours) in the browser and pushes them here; stored as the
    deliveroo menu + hours snapshots that `menu_readers._read_deliveroo_menu` /
    `_read_deliveroo_hours` parse. Push transport only — like the keeta menu push."""
    from app.services.aggregators import catalog_sync

    await catalog_sync.store_worker_menu_and_hours(
        db, target="deliveroo", payloads=body.payloads
    )
    return DeliverooMenuResult(stored=True, restaurants=len(body.payloads))


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


_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


@router.get(
    "/statements",
    response_model=AggregatorStatementList,
    dependencies=[Depends(require("reports.sales"))],
)
async def list_statements(
    channel: str | None = Query(None),
    date_from: str | None = Query(None, pattern=_DATE_RE),
    date_to: str | None = Query(None, pattern=_DATE_RE),
    has_invoice: bool | None = Query(
        None, description="Only statements that carry (or lack) an archived document"
    ),
    limit: int = Query(50, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AggregatorStatementList:
    """The settlement statements for the Invoices screen, newest period first.

    Filtered by channel and by the statement's PERIOD END falling in the range
    (a statement is dated by when its period closes). `has_invoice` narrows to the
    ones with an archived document to download — today only Careem, Deliveroo and
    Noon publish one; Keeta and Talabat statements have figures but no file.
    """
    s = AggregatorStatement
    stmt = select(s)
    if channel:
        stmt = stmt.where(s.channel == channel)
    # period_end is String(10) ISO; lexicographic order == chronological.
    if date_from:
        stmt = stmt.where(s.period_end >= date_from)
    if date_to:
        stmt = stmt.where(s.period_end <= date_to)
    if has_invoice is True:
        stmt = stmt.where(s.invoice_object_key.isnot(None))
    elif has_invoice is False:
        stmt = stmt.where(s.invoice_object_key.is_(None))

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(s.period_end.desc().nullslast(), s.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars()

    items = [
        AggregatorStatementOut(
            id=row.id,
            channel=row.channel,
            statement_id=row.statement_id,
            period_start=row.period_start,
            period_end=row.period_end,
            payment_due_date=row.payment_due_date,
            currency=row.currency,
            gross_sales=row.gross_sales,
            total_fees=row.total_fees,
            total_vat=row.total_vat,
            net_payable=row.net_payable,
            external_outlet_id=row.external_outlet_id,
            has_invoice=row.invoice_object_key is not None,
            invoice_original_filename=row.invoice_original_filename,
            invoice_fetched_at=row.invoice_fetched_at,
            attachment_count=len(row.invoice_attachments or []),
            created_at=row.created_at,
        )
        for row in rows
    ]
    return AggregatorStatementList(items=items, total=total)


@router.get(
    "/statements/{statement_uuid}/invoice",
    response_model=AggregatorInvoiceUrl,
    dependencies=[Depends(require("reports.sales"))],
)
async def statement_invoice_url(
    statement_uuid: UUID,
    db: AsyncSession = Depends(get_db),
) -> AggregatorInvoiceUrl:
    """A short-lived signed URL to download one statement's archived invoice.

    The document lives in a private GCS bucket; the admin never gets the object
    key, only a URL that expires in an hour. 404 when the statement carries no
    document, 503 when object storage is not configured (so the caller can tell a
    missing file from a missing integration).
    """
    row = await db.get(AggregatorStatement, statement_uuid)
    if row is None:
        raise NotFoundError("statement not found")
    if not row.invoice_object_key:
        raise NotFoundError("this statement has no archived invoice")
    expires = 3600
    url = statement_docs.presigned_get_url(
        row.invoice_object_key, expires_seconds=expires
    )
    if url is None:
        raise ServiceUnavailableError("invoice storage is not configured")
    return AggregatorInvoiceUrl(
        url=url,
        filename=row.invoice_original_filename,
        content_type=row.invoice_content_type,
        expires_seconds=expires,
    )


@router.get(
    "/fees/summary",
    response_model=AggregatorFeesSummaryOut,
    dependencies=[Depends(require("reports.sales"))],
)
async def fees_summary(
    channel: str | None = Query(None),
    date_from: str | None = Query(None, pattern=_DATE_RE),
    date_to: str | None = Query(None, pattern=_DATE_RE),
    db: AsyncSession = Depends(get_db),
) -> AggregatorFeesSummaryOut:
    """Per-channel commission / VAT / net roll-up over a date range.

    The commission figure is genuinely split by marketplace, and this merges the
    two sources so the picture is complete rather than half-dark:

      • Channels that settle through detailed statement lines (Deliveroo, Keeta,
        Noon) are read from `aggregator_statement_line` over `line_date` — the
        authoritative settled fees, and the ONLY place VAT appears.
      • Channels that do not (Talabat carries commission on the order feed; Careem
        exposes no per-order fee at all) are read from `aggregator_order` over
        `business_date` — commission and gross where the feed has them, no VAT.

    One source is chosen PER CHANNEL (statement lines when a channel has any in
    range, else the order feed), so Keeta is never double-counted. Providers
    disagree on a fee's SIGN, so every bucket is a positive magnitude —
    "what they charged". `effective_rate` is commission ÷ gross.
    """
    statement_rows = await _fees_from_statement_lines(db, channel, date_from, date_to)
    order_rows = await _fees_from_orders(db, channel, date_from, date_to)

    # Statement lines win where present (settled + carry VAT); the order feed fills
    # the channels that never reach a statement line (Talabat, Careem). Keeta's
    # merchant-funded promotion is folded into `commission_amount` at ingest, so it
    # is already inside the commission bucket here — no separate add-on.
    chosen: dict[str, AggregatorFeesRow] = dict(order_rows)
    chosen.update(statement_rows)

    by_channel = [chosen[ch] for ch in sorted(chosen)]
    totals = _fees_total(by_channel)
    return AggregatorFeesSummaryOut(
        from_date=date_from, to_date=date_to, by_channel=by_channel, totals=totals
    )


async def _fees_from_statement_lines(
    db: AsyncSession, channel: str | None, date_from: str | None, date_to: str | None
) -> dict[str, AggregatorFeesRow]:
    """Fee/VAT roll-up per channel from the settled statement lines (has VAT)."""
    ln = AggregatorStatementLine
    lt = func.lower(func.coalesce(ln.line_type, ""))
    fc = func.lower(func.coalesce(ln.fee_category, ""))
    # Provider-verbatim words → buckets. VAT is tested first so a "commission_vat"
    # line lands in VAT, not commission.
    is_vat = or_(lt == "vat", fc.like("%vat%"))
    is_commission = and_(fc == "commission", ~is_vat)
    is_gross = or_(fc == "gross_sales", lt.in_(["gross_sales", "sales", "sale"]))
    is_net = or_(fc == "net_payable", lt.in_(["net_payable", "payout", "settlement"]))
    amt = func.abs(ln.amount)

    def _sum(cond):
        return func.coalesce(func.sum(amt).filter(cond), 0)

    stmt = select(
        ln.channel.label("channel"),
        _sum(is_gross).label("gross_sales"),
        _sum(is_commission).label("commission"),
        _sum(is_vat).label("vat"),
        _sum(and_(~is_gross, ~is_net, ~is_commission, ~is_vat)).label("other_fees"),
        _sum(is_net).label("net_payable"),
        func.count(distinct(ln.external_order_id)).label("orders"),
    ).group_by(ln.channel)
    if channel:
        stmt = stmt.where(ln.channel == channel)
    if date_from:
        stmt = stmt.where(ln.line_date >= date_from)
    if date_to:
        stmt = stmt.where(ln.line_date <= date_to)
    return {r.channel: _fees_row(r.channel, r) for r in (await db.execute(stmt)).all()}


async def _fees_from_orders(
    db: AsyncSession, channel: str | None, date_from: str | None, date_to: str | None
) -> dict[str, AggregatorFeesRow]:
    """Fee roll-up per channel from the order feed — for channels with no statement
    lines (Talabat's commission, Careem's gross). No VAT here (the feed has none)."""
    o = AggregatorOrder
    stmt = select(
        o.channel.label("channel"),
        func.coalesce(func.sum(func.abs(o.gross_sales)), 0).label("gross_sales"),
        func.coalesce(func.sum(func.abs(o.commission_amount)), 0).label("commission"),
        func.coalesce(func.sum(func.abs(o.payment_fee)), 0).label("other_fees"),
        func.coalesce(func.sum(func.abs(o.net_payable)), 0).label("net_payable"),
        func.count().label("orders"),
    ).group_by(o.channel)
    if channel:
        stmt = stmt.where(o.channel == channel)
    if date_from:
        stmt = stmt.where(o.business_date >= date_from)
    if date_to:
        stmt = stmt.where(o.business_date <= date_to)
    return {
        r.channel: _fees_row(r.channel, r, vat=None)
        for r in (await db.execute(stmt)).all()
    }


def _fees_row(channel: str, r, *, vat: object = ...) -> AggregatorFeesRow:
    """One fees roll-up row, with the effective commission rate computed in Python.
    `vat` defaults to the row's own `vat` column; pass None for the order feed,
    which has no VAT column."""
    gross = Decimal(r.gross_sales or 0)
    commission = Decimal(r.commission or 0)
    rate = float(commission / gross) if gross else None
    return AggregatorFeesRow(
        channel=channel,
        gross_sales=r.gross_sales,
        commission=r.commission,
        vat=r.vat if vat is ... else vat,
        other_fees=r.other_fees,
        net_payable=r.net_payable,
        orders=r.orders or 0,
        effective_rate=rate,
    )


def _fees_total(rows: list[AggregatorFeesRow]) -> AggregatorFeesRow:
    """Combine the chosen per-channel rows into one total (no double-counting —
    one source was already picked per channel)."""

    def _s(attr):
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        return sum((Decimal(str(v)) for v in vals), Decimal(0)) if vals else None

    gross = _s("gross_sales")
    commission = _s("commission")
    rate = float(commission / gross) if gross and commission is not None else None
    return AggregatorFeesRow(
        channel="all",
        gross_sales=gross,
        commission=commission,
        vat=_s("vat"),
        other_fees=_s("other_fees"),
        net_payable=_s("net_payable"),
        orders=sum(r.orders for r in rows),
        effective_rate=rate,
    )


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


# ── Working-hours sync (MM → integrators) ────────────────────────────────────
# The run trail for `branch_hours_sync`'s fan-out, plus the two seams Keeta needs
# because the headed worker has no DB: it pulls MM's weekly schedule from here and
# reports each shop's in-page write back here to be recorded like the others.

_KEETA_DAY_KEYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _hhmm_to_seconds(clock: str) -> int:
    """`"08:15"` → 29700 (seconds from midnight). A 23:59 close becomes 86400 —
    the day-end value Keeta stores (matching the worker's `_keeta_hour_slot`)."""
    parts = (clock or "0:0").split(":")
    secs = int(parts[0]) * 3600 + (int(parts[1]) if len(parts) > 1 else 0) * 60
    return 86400 if secs == 86340 else secs


def _keeta_weekly_from_schedule(
    sched: dict[int, tuple[str, str]],
) -> dict[str, list[dict]]:
    """MM `{weekday: (opens,closes)}` → Keeta `businessHourOfTheWeek` day-map.

    Sunday-first keys (MM 0=Sunday aligns with `_KEETA_DAY_KEYS`), seconds from
    midnight; a closed weekday is `[{startTime:0,endTime:0,option:1}]`."""
    weekly: dict[str, list[dict]] = {}
    for wd in range(7):
        win = sched.get(wd)
        if win is None:
            weekly[_KEETA_DAY_KEYS[wd]] = [{"startTime": 0, "endTime": 0, "option": 1}]
        else:
            weekly[_KEETA_DAY_KEYS[wd]] = [
                {
                    "startTime": _hhmm_to_seconds(win[0]),
                    "endTime": _hhmm_to_seconds(win[1]),
                    "option": 1,
                }
            ]
    return weekly


def _hours_run_out(
    run: BranchHoursSyncRun, branch_name: str | None
) -> BranchHoursSyncRunOut:
    out = BranchHoursSyncRunOut.model_validate(run)
    out.branch_name = branch_name
    return out


@router.get(
    "/hours-runs",
    response_model=BranchHoursSyncRunList,
    dependencies=[Depends(require("reports.sales"))],
)
async def list_hours_runs(
    branch_id: UUID | None = Query(None),
    channel: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> BranchHoursSyncRunList:
    """The working-hours fan-out trail for the admin panel — one row per
    (branch, channel) push, newest first, with the total for the filter. Each
    row carries the dry-run plan or the live result and, on a failure, why."""
    filters = []
    if branch_id:
        filters.append(BranchHoursSyncRun.branch_id == branch_id)
    if channel:
        filters.append(BranchHoursSyncRun.channel == channel)
    if status:
        filters.append(BranchHoursSyncRun.status == status)

    count_stmt = select(func.count()).select_from(BranchHoursSyncRun)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = select(BranchHoursSyncRun, Branch.name).join(
        Branch, Branch.id == BranchHoursSyncRun.branch_id
    )
    if filters:
        stmt = stmt.where(*filters)
    stmt = (
        stmt.order_by(BranchHoursSyncRun.created_at.desc()).offset(offset).limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return BranchHoursSyncRunList(
        items=[_hours_run_out(run, name) for run, name in rows], total=total
    )


@router.get("/worker/keeta/hours", response_model=KeetaHoursScheduleOut)
async def worker_keeta_hours(
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> KeetaHoursScheduleOut:
    """MM's weekly schedule per Keeta shop, for the worker's in-page write.

    Keeta's hours save is mtgsig-signed in the page, so the DB-less worker pulls
    the schedule here (seconds-from-midnight, Sunday-first) and posts each shop's
    outcome back to `/keeta/hours-result`. An empty list until
    `CATALOG_SYNC_ENABLED`; `dry_run` echoes `BRANCH_HOURS_SYNC_LIVE` so the live
    decision stays on the API side. A shop with no MM schedule is omitted, so the
    worker keeps the portal's own map for it (never blanks a shop)."""
    if not settings.CATALOG_SYNC_ENABLED:
        return KeetaHoursScheduleOut(dry_run=True, shops=[])
    rows = (
        (
            await db.execute(
                select(AggregatorBranchMap).where(
                    AggregatorBranchMap.channel == CHANNEL_KEETA,
                    AggregatorBranchMap.is_active.is_(True),
                    AggregatorBranchMap.external_outlet_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    shops: list[KeetaHoursShop] = []
    for row in rows:
        sched = await branch_hours_service.schedule(db, row.branch_id)
        if sched is None:
            continue
        shops.append(
            KeetaHoursShop(
                shop_id=str(row.external_outlet_id),
                branch_id=row.branch_id,
                weekly=_keeta_weekly_from_schedule(sched),
            )
        )
    return KeetaHoursScheduleOut(
        dry_run=not settings.BRANCH_HOURS_SYNC_LIVE, shops=shops
    )


@router.post("/keeta/hours-result", response_model=KeetaHoursResult)
async def worker_keeta_hours_result(
    body: KeetaHoursResultPush,
    _: None = Depends(_require_push_token),
    db: AsyncSession = Depends(get_db),
) -> KeetaHoursResult:
    """Record the worker's per-shop Keeta hours outcomes as
    `branch_hours_sync_run` rows, alerting Sentry on a failed save with the same
    stable per-channel fingerprint the httpx fan-out uses."""
    map_rows = (
        await db.execute(
            select(
                AggregatorBranchMap.external_outlet_id,
                AggregatorBranchMap.branch_id,
            ).where(
                AggregatorBranchMap.channel == CHANNEL_KEETA,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).all()
    by_shop = {str(outlet): branch_id for outlet, branch_id in map_rows if outlet}
    recorded = 0
    for outcome in body.outcomes:
        branch_id = by_shop.get(str(outcome.shop_id))
        if branch_id is None:
            continue
        if not outcome.ok:
            alerting.capture_issue(
                "branch-hours weekly push failed: keeta",
                level="warning",
                fingerprint=["branch-hours-sync", "keeta", "weekly-push"],
                tags={
                    "channel": "keeta",
                    "shop_id": str(outcome.shop_id),
                    "branch_id": str(branch_id),
                    "op": "push_weekly_hours",
                    "dry_run": str(outcome.dry_run),
                },
            )
        db.add(
            BranchHoursSyncRun(
                branch_id=branch_id,
                channel="keeta",
                status="completed" if outcome.ok else "failed",
                dry_run=outcome.dry_run,
                planned=outcome.planned,
                error=outcome.error,
                finished_at=utcnow(),
            )
        )
        recorded += 1
    return KeetaHoursResult(recorded=recorded)


#: A single manual backfill spans at most a quarter — enough to re-pull a
#: settlement window, short of a click that would re-scrape the whole year.
_MAX_TRIGGER_RANGE_DAYS = 92


@router.post(
    "/runs/trigger",
    response_model=AggregatorRunTriggerOut,
    dependencies=[Depends(require("reports.sales"))],
)
async def trigger_sync_run(
    body: AggregatorRunTriggerIn | None = None,
    db: AsyncSession = Depends(get_db),
) -> AggregatorRunTriggerOut:
    """Kick off an aggregator pass now — the "Run now" button on the Runs table.

    With no dates it runs the same recent pass the nightly scheduler does (sales →
    finance → promote → reconcile, every channel). With `from_date`/`to_date` it
    backfills that explicit Dubai business-date range instead — the way to re-pull
    past days, e.g. to correct orders scraped before a fix landed: the re-pull
    re-derives each order's `created_at` from the corrected placed-at. Either way it
    fires in the background and answers at once — a pass takes minutes, and the
    caller watches it land as each channel's run row appears rather than holding the
    request open. Clicking while a pass is in flight is safe: the sweeps serialise on
    advisory locks and no-op if one is held. Gated on the same permission as the Runs
    table itself."""
    body = body or AggregatorRunTriggerIn()

    if (body.from_date is None) != (body.to_date is None):
        raise BadRequestError("Give both a start and an end date, or neither.")

    channels = body.channels or None
    if channels:
        unknown = [c for c in channels if c not in AGGREGATOR_CHANNELS]
        if unknown:
            raise BadRequestError(f"Unknown channel(s): {', '.join(unknown)}.")

    if body.from_date is not None and body.to_date is not None:
        if body.from_date > body.to_date:
            raise BadRequestError("The start date must not be after the end date.")
        if (body.to_date - body.from_date).days + 1 > _MAX_TRIGGER_RANGE_DAYS:
            raise BadRequestError(
                f"A single run spans at most {_MAX_TRIGGER_RANGE_DAYS} days."
            )
        started = ingest.trigger_range_in_background(
            body.from_date, body.to_date, channels
        )
        span = (
            body.from_date.isoformat()
            if body.from_date == body.to_date
            else f"{body.from_date.isoformat()} → {body.to_date.isoformat()}"
        )
        detail = f"Backfill started for {span} — the table fills in as each channel finishes."
    else:
        started = ingest.trigger_daily_in_background()
        detail = "Run started — the table will fill in as each channel finishes."

    if not started:
        raise ServiceUnavailableError(
            "The aggregator ingest is disabled or not configured on this deployment."
        )

    # Pre-run readiness, for information only: the run itself now self-heals a
    # dead session — it flags the channel and waits for the reauth daemon to drive
    # a headed re-login, then retries — so a dead channel is re-authenticated
    # automatically rather than skipped. We still surface the current state so the
    # operator knows a channel will take the extra re-login time on this pass.
    health_rows = await ingest.session_readiness(db, channels)
    not_ready = [r["channel"] for r in health_rows if not r["usable"]]
    if not_ready:
        detail += (
            f" {', '.join(sorted(not_ready))} "
            f"{'is' if len(not_ready) == 1 else 'are'} not authenticated — "
            "re-authenticating automatically now, so that channel takes a little "
            "longer on this pass."
        )
    return AggregatorRunTriggerOut(
        started=True,
        detail=detail,
        session_health=[AggregatorSessionHealthOut(**r) for r in health_rows],
    )


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
