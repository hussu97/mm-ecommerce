"""What the bootstrap/warmer worker sends when it hands a session to the API,
and what the reconciliation dashboard reads back."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AggregatorSessionPush(BaseModel):
    """A freshly captured marketplace session, pushed in for the ingest to replay.

    The worker that runs the browser sends the bundle here; the API seals it
    and stores it. The cookies carry the load-bearing anti-bot cookie,
    `header_profile` the exact request fingerprint, `storage_state` the
    Playwright blob a restarted worker hydrates.
    """

    channel: str
    account_ref: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    tokens: dict = Field(default_factory=dict)
    header_profile: dict[str, str] = Field(default_factory=dict)
    #: Playwright storage_state + origin-scoped sessionStorage. Optional so an
    #: older worker that only captured cookies still pushes; the API then keeps
    #: whatever blob it already has rather than wiping it.
    storage_state: dict | None = None
    token_expires_at: datetime | None = None
    cookie_expires_at: datetime | None = None


class AggregatorWorkerSession(BaseModel):
    """The decrypted session a worker hydrates from after a deploy/restart.

    Secrets on the wire, authenticated with the same push bearer as POST
    `/session`. The admin health read (`GET /sessions`) never returns this.
    """

    channel: str
    account_ref: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    tokens: dict = Field(default_factory=dict)
    header_profile: dict[str, str] = Field(default_factory=dict)
    storage_state: dict | None = None
    token_expires_at: datetime | None = None
    cookie_expires_at: datetime | None = None
    status: str
    last_warmed_at: datetime | None = None


class AggregatorSessionResponse(BaseModel):
    """The stored session's health, echoed back to the worker."""

    model_config = ConfigDict(from_attributes=True)

    channel: str
    account_ref: str
    status: str
    token_expires_at: datetime | None = None
    cookie_expires_at: datetime | None = None
    last_bootstrap_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


class AggregatorMailboxWrite(BaseModel):
    """OTP mailbox for one aggregator, written sealed at rest.

    Two providers share this object so each channel can have its own recipe:

    - `imap` — host/user/password (legacy).
    - `graph` — that channel's own Microsoft app (`client_id` + `client_secret`)
      plus, after `mailbox-auth`, a `refresh_token`. Secrets may be omitted on
      a later save to keep the stored value.

    `sender_filter` / `subject_filter` narrow which mail is treated as the
    code (Talabat `no reply` / Noon `verify`).
    """

    provider: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    folder: str | None = None
    sender_filter: str | None = None
    subject_filter: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    tenant: str | None = None
    redirect_uri: str | None = None
    refresh_token: str | None = None


class AggregatorMailboxPublic(BaseModel):
    """Admin view of a linked OTP mailbox — never secrets."""

    provider: str = "imap"
    host: str = ""
    port: int = 993
    username: str = ""
    folder: str = "INBOX"
    sender_filter: str = ""
    subject_filter: str = ""
    client_id: str = ""
    tenant: str = "consumers"
    redirect_uri: str = ""
    has_password: bool = False
    has_client_secret: bool = False
    has_refresh_token: bool = False


class AggregatorAccountPush(BaseModel):
    """A durable login recipe for one channel, stored sealed at rest.

    Distinct from the session cookie jar: this is the email/password, the
    login method (including OTP vs no OTP), and the OTP mailbox the worker
    reads a code from (per-channel Microsoft Graph app, or IMAP). `password` /
    `mailbox.password` / `mailbox.client_secret` may be omitted on a later
    save to keep the stored secret. `extras` is non-secret portal config
    (Deliveroo `org_id`).
    """

    channel: str
    account_ref: str = ""
    login_method: str | None = None
    email: str | None = None
    password: str | None = None
    mailbox: AggregatorMailboxWrite | None = None
    clear_mailbox: bool = False
    extras: dict | None = None


class AggregatorAccountPublic(BaseModel):
    """Admin health for one login recipe — never a password."""

    channel: str
    account_ref: str = ""
    login_method: str
    otp_required: bool = False
    email: str = ""
    has_password: bool
    has_mailbox: bool = False
    mailbox: AggregatorMailboxPublic | None = None
    extras: dict = Field(default_factory=dict)
    updated_at: datetime | None = None


class AggregatorWorkerAccount(BaseModel):
    """Decrypted login recipe for the worker. Secrets on the wire, on purpose.

    Authenticated with the same push bearer as POST `/session`. The admin
    health read never returns portal or mailbox secrets.
    """

    channel: str
    account_ref: str = ""
    login_method: str
    otp_required: bool = False
    email: str = ""
    password: str = ""
    mailbox: dict = Field(default_factory=dict)
    extras: dict = Field(default_factory=dict)


class KeetaOrdersPush(BaseModel):
    """A batch of in-page-fetched Keeta order payloads, pushed in for ingest.

    Keeta cannot be swept over httpx (its `mtgsig` request signing lives in the
    page), so the bootstrap worker fetches each `getOrders` response in-page and
    hands the raw payloads here. Each payload is parsed by `keeta_provider` into
    channel-neutral orders and upserted, exactly as the httpx sweep does for the
    other four channels — this endpoint is simply Keeta's transport.
    """

    payloads: list[dict] = Field(default_factory=list)


class KeetaOrdersResult(BaseModel):
    """How many orders the pushed Keeta payloads upserted."""

    ingested: int


class KeetaFinancePush(BaseModel):
    """A batch of in-page-fetched Keeta finance payloads, pushed in for ingest.

    The bootstrap worker fetches the finance data in-page (where the portal's JS
    signs the request), then hands the raw payloads here. Each is parsed by
    `keeta_provider.parse_finance` into channel-neutral statements and payouts
    and upserted. When the payload contains only download-task metadata (the
    actual figures live in PDF invoices), the parse returns empty lists and
    records a truncation note — the response still returns 200 with zero counts
    so the worker can surface the note.
    """

    payloads: list[dict] = Field(default_factory=list)


class KeetaFinanceResult(BaseModel):
    """How many statements and payouts the pushed Keeta finance payloads upserted."""

    statements: int
    payouts: int
    truncation_note: str | None = None


class DeliverooFinancePush(BaseModel):
    """A batch of in-page-fetched Deliveroo invoice payloads, pushed in for ingest.

    Deliveroo's invoice list replays over httpx, but the invoice download 403s
    behind Cloudflare, so the bootstrap worker downloads each statement CSV and
    PDF in-page (carrying the browser's `cf_clearance`) and hands the raw
    payloads here. Each payload is one invoice:
    `{"invoice": <raw dict>, "statement_csv": <text|None>,
    "statement_pdf_b64": <b64|None>}`. `deliveroo_provider.parse_pushed_finance`
    turns it into a statement with per-order lines and an archived VAT PDF.
    """

    payloads: list[dict] = Field(default_factory=list)


class DeliverooFinanceResult(BaseModel):
    """How many statements and lines the pushed Deliveroo invoice payloads upserted."""

    statements: int
    lines: int
    truncation_note: str | None = None


class AggregatorBranchMapIn(BaseModel):
    """An outlet↔branch mapping to create or update from the admin.

    The mapping lives in the DB (seeded by migration 152, edited here) rather
    than in code, so a re-onboarded outlet or a new branch is a row change, not
    a deploy. Keyed on `(channel, branch_id)`; posting the same pair updates it.
    """

    channel: str
    branch_id: UUID
    external_outlet_id: str | None = None
    external_brand_id: str | None = None
    external_company_id: str | None = None
    channel_ref: str | None = None
    is_active: bool = True


class AggregatorBranchMapOut(BaseModel):
    """One outlet↔branch mapping row, with the branch name for display."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: str
    branch_id: UUID
    branch_name: str | None = None
    external_outlet_id: str | None = None
    external_brand_id: str | None = None
    external_company_id: str | None = None
    channel_ref: str | None = None
    is_active: bool


class AggregatorReconciliationOut(BaseModel):
    """One reconciliation row for the dashboard — the maker-checker's output.

    `branch_name` is joined from `branches`; `flags` is the list of issue codes
    raised (never null on the wire — an unflagged order carries `[]`).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: str
    external_order_id: str
    branch_id: UUID | None = None
    branch_name: str | None = None
    mm_order_id: UUID | None = None
    match_status: str
    item_flag: bool
    refund_flag: bool
    refund_agg: Decimal | None = None
    refund_mm: Decimal | None = None
    commission_expected: Decimal | None = None
    commission_actual: Decimal | None = None
    commission_variance: Decimal | None = None
    commission_rate_effective: Decimal | None = None
    total_agg: Decimal | None = None
    total_mm: Decimal | None = None
    amount_variance: Decimal | None = None
    flags: list[str] = Field(default_factory=list)
    reconciled_at: datetime | None = None


class AggregatorReconciliationList(BaseModel):
    """A page of reconciliation rows, plus the unpaginated total for the filter."""

    items: list[AggregatorReconciliationOut]
    total: int


# ── sync runs (the ingest trail the admin Runs table shows) ───────────────────
class AggregatorSyncRunOut(BaseModel):
    """One recorded ingest run — a channel×trigger, with its outcome and stats.

    `stats` is the run's JSON blob verbatim (what was retrieved and the promotion
    split); the flat fields lift the figures the table columns show so the UI does
    not have to know the blob's shape. `error` is the failure reason (or the
    per-mode partial reasons), null on a clean run."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel: str
    mode: str
    status: str
    from_date: str | None = None
    to_date: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    stats: dict | None = None
    # Lifted from stats for the table columns (null when a run recorded none).
    orders_retrieved: int | None = None
    orders_promoted: int | None = None
    orders_promoted_new: int | None = None
    orders_promoted_existing: int | None = None
    orders_not_promoted: int | None = None
    pct_promoted: float | None = None
    statements_total: int | None = None
    payouts_total: int | None = None
    invoices_total: int | None = None


class AggregatorSyncRunList(BaseModel):
    """A page of sync-run rows, newest first, plus the unpaginated total."""

    items: list[AggregatorSyncRunOut]
    total: int


class AggregatorRunTriggerIn(BaseModel):
    """What "Run now" asks for. Omit the dates for the standard recent pass; give
    both to backfill an explicit Dubai business-date range (inclusive) — e.g. to
    re-pull days scraped before a fix landed. `channels` narrows the range run to a
    subset (default: every channel); it is ignored for a dateless recent pass, which
    always covers all of them."""

    from_date: date | None = None
    to_date: date | None = None
    channels: list[str] | None = None


class AggregatorRunTriggerOut(BaseModel):
    """The answer to a manual "Run now" — did the pass start, and a line to show.

    The pass runs in the background (it takes minutes), so this only reports that
    it was accepted; the Runs table shows the outcome as each channel's row lands.
    `started` is False when the ingest is disabled or unconfigured."""

    started: bool
    detail: str


class ReconSummaryRow(BaseModel):
    """The reconciliation tallies for one channel (or the `all` total row).

    Counts are over the filtered set; `commission_actual_sum` and
    `avg_rate_effective` are the money aggregates the couriers grammar is
    validated against.
    """

    channel: str
    total: int
    matched: int
    unmatched_agg: int
    no_maker_side: int
    item_flags: int
    refund_flags: int
    commission_variance_count: int
    commission_actual_sum: Decimal | None = None
    avg_rate_effective: Decimal | None = None


class ReconSummaryOut(BaseModel):
    """The summary read: per-channel rows and one combined total."""

    by_channel: list[ReconSummaryRow]
    totals: ReconSummaryRow


# ── Layer A: settlement reconciliation (sales↔statement↔payout) ───────────────
class SettlementPayoutInfo(BaseModel):
    """The transfer that settled a statement, as far as the payout feed knows."""

    model_config = ConfigDict(from_attributes=True)

    transfer_id: str
    transfer_amount: Decimal | None = None
    transfer_date: str | None = None
    transfer_status: str | None = None


class SettlementStatementRecon(BaseModel):
    """One statement reconciled across its sales, settlement and payout sides.

    `sales_total` sums the orders that settled on this statement;
    `settled_total` sums its order-grain lines; `statement_net_payable` is the
    statement's own declared figure (null for talabat file-rows, which then
    carry a `no_statement_total` flag instead of a false variance). Each
    variance is null when a side is unknown, never a misleading 0.
    """

    model_config = ConfigDict(from_attributes=True)

    channel: str
    statement_id: str
    period_start: str | None = None
    period_end: str | None = None
    payment_due_date: str | None = None
    currency: str | None = None
    sales_total: Decimal | None = None
    settled_total: Decimal | None = None
    statement_net_payable: Decimal | None = None
    orders_count: int
    lines_count: int
    orders_promoted: int
    payout_transfer_id: str | None = None
    payout: SettlementPayoutInfo | None = None
    sales_vs_settled: Decimal | None = None
    sales_vs_settled_flag: bool
    settled_vs_statement: Decimal | None = None
    settled_vs_statement_flag: bool
    flags: list[str] = Field(default_factory=list)


class SettlementPayoutRollup(BaseModel):
    """One transfer against the statements it settled — the batch-payout check.

    `statements_net_total` is the summed declared net payable of the member
    statements; `variance` is `transfer_amount − statements_net_total`, ~0 when
    one payout exactly clears its batch of statements.
    """

    model_config = ConfigDict(from_attributes=True)

    channel: str
    transfer_id: str
    transfer_amount: Decimal | None = None
    transfer_date: str | None = None
    transfer_status: str | None = None
    statement_ids: list[str] = Field(default_factory=list)
    statements_count: int
    statements_net_total: Decimal | None = None
    variance: Decimal | None = None
    variance_flag: bool
    flags: list[str] = Field(default_factory=list)


class SettlementReconOut(BaseModel):
    """The Layer A read: per-statement rows plus the per-payout rollup."""

    model_config = ConfigDict(from_attributes=True)

    channel: str
    from_date: str | None = None
    to_date: str | None = None
    statements: list[SettlementStatementRecon]
    payouts: list[SettlementPayoutRollup]
