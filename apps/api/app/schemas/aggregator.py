"""What the bootstrap/warmer worker sends when it hands a session to the API,
and what the reconciliation dashboard reads back."""

from __future__ import annotations

from datetime import datetime
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
