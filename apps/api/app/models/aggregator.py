"""What the delivery aggregators call the money this shop already counts.

Five marketplaces sell this shop's food — Careem, Deliveroo, Talabat, Noon and
Keeta — and each keeps its own ledger of every order, the fees it took and the
payout it eventually sends. This module is where that ledger is mirrored into
`mm_ecommerce`, so the shop can answer three questions a marketplace's own
dashboard is bad at: were items missing, was anything refunded after delivery,
and what commission was *actually* charged versus what we modelled.

The tables split along the grain of the source data, because conflating them is
how a reconciliation starts lying:

**Who this branch is over there.** `aggregator_branch_map` — one row per
`(channel, branch)`, holding the marketplace's own outlet/brand/company ids. A
branch on three aggregators is three rows; onboarding a fourth is an insert, not
a migration. This is the same "capability is a mapping row" idiom
`grubops_location_map` uses, generalised: a branch is *on* Careem iff it has an
active row here, the way it is *on* GrubOps iff it has a `grubops_location_map`
row. `foodics_branch_map` completes the set so "has Foodics" is a row too, and a
branch can carry any mix of the three integrations — or none.

**How we sign in.** `aggregator_account` — the durable login recipe for one
channel: which flow to run (`login_method`) plus Fernet-sealed credentials.
The session cookies are a *product* of that login; they expire. The email and
password (or OTP mailbox) do not, so the worker can re-auth on the VM instead
of shipping a laptop-minted cookie jar.

**How we talk to them.** `aggregator_session` — the derived, encrypted session
(cookies + tokens + the captured header fingerprint) a bootstrap logged in to
obtain, replayed by the httpx providers so the hourly job never opens a browser.
Two independent expiries because a marketplace's access token (refreshable over
the API) and its anti-bot cookie (refreshable only by re-running the page
sensor) die on different clocks.

**What they say happened.** `aggregator_order` / `aggregator_order_item` (sales
truth), `aggregator_statement` / `aggregator_statement_line` (the settlement),
`aggregator_payout` (the transfer). Everything keyed on the channel plus the
marketplace's own id so a re-run upserts rather than duplicates.

**Where we disagree.** `aggregator_reconciliation` — one row per matched order,
carrying the item/refund/commission deltas against the MM order it became. Only
GrubOps branches produce an MM order to check against; an aggregator-only branch
(DSO, Karama) gets `no_maker_side`, which is a fact, not a discrepancy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin

# ── Channels ────────────────────────────────────────────────────────────────
# Spelled out here and in each migration CHECK, per the string-plus-constraint
# convention — a native enum would need a migration of its own to grow, and a
# sixth marketplace is a `("careem", ..., "the_new_one")` edit, not a type.
CHANNEL_CAREEM = "careem"
CHANNEL_DELIVEROO = "deliveroo"
CHANNEL_TALABAT = "talabat"
CHANNEL_NOON = "noon"
CHANNEL_KEETA = "keeta"
AGGREGATOR_CHANNELS: tuple[str, ...] = (
    CHANNEL_CAREEM,
    CHANNEL_DELIVEROO,
    CHANNEL_TALABAT,
    CHANNEL_NOON,
    CHANNEL_KEETA,
)
_CHANNELS_SQL = ", ".join(f"'{c}'" for c in AGGREGATOR_CHANNELS)

# ── Session health ──────────────────────────────────────────────────────────
SESSION_LIVE = "live"
SESSION_NEEDS_BOOTSTRAP = "needs_bootstrap"
SESSION_DEAD = "dead"
SESSION_STATUSES: tuple[str, ...] = (
    SESSION_LIVE,
    SESSION_NEEDS_BOOTSTRAP,
    SESSION_DEAD,
)

# ── Login recipes ───────────────────────────────────────────────────────────
# How the worker signs in. Selectors and URLs stay in code (`login.py`); this
# is the *kind* of flow, so a new channel is a CHECK widen plus a login
# function, not a JSON blob of CSS selectors that nobody can test.
LOGIN_EMAIL_PASSWORD = "email_password"
LOGIN_EMAIL_OTP = "email_otp"
LOGIN_EMAIL_PASSWORD_OTP = "email_password_otp"
LOGIN_SSO = "sso"
LOGIN_MANUAL = "manual"
LOGIN_METHODS: tuple[str, ...] = (
    LOGIN_EMAIL_PASSWORD,
    LOGIN_EMAIL_OTP,
    LOGIN_EMAIL_PASSWORD_OTP,
    LOGIN_SSO,
    LOGIN_MANUAL,
)
_LOGIN_METHODS_SQL = ", ".join(f"'{m}'" for m in LOGIN_METHODS)

#: Flows that type a portal password. Noon is email-then-OTP, no password.
METHODS_NEED_PASSWORD: frozenset[str] = frozenset(
    {LOGIN_EMAIL_PASSWORD, LOGIN_EMAIL_PASSWORD_OTP}
)
#: Flows that wait on a mailed one-time code. The IMAP mailbox on the
#: account row is how the worker reads that code unattended.
METHODS_NEED_OTP: frozenset[str] = frozenset(
    {LOGIN_EMAIL_OTP, LOGIN_EMAIL_PASSWORD_OTP}
)
#: Flows that fill a portal email field.
METHODS_NEED_EMAIL: frozenset[str] = frozenset(
    {LOGIN_EMAIL_PASSWORD, LOGIN_EMAIL_OTP, LOGIN_EMAIL_PASSWORD_OTP}
)


def method_needs_otp(login_method: str) -> bool:
    return login_method in METHODS_NEED_OTP


#: The flow each portal actually uses today. Stored on the account row so an
#: operator can see it; the worker still dispatches on this column, not this
#: map, in case one brand's Careem tenant differs from another's.
CHANNEL_LOGIN_METHODS: dict[str, str] = {
    CHANNEL_DELIVEROO: LOGIN_EMAIL_PASSWORD,
    CHANNEL_TALABAT: LOGIN_EMAIL_PASSWORD_OTP,
    CHANNEL_NOON: LOGIN_EMAIL_OTP,
    CHANNEL_KEETA: LOGIN_EMAIL_PASSWORD,
    CHANNEL_CAREEM: LOGIN_MANUAL,
}

# ── Sync-run lifecycle ──────────────────────────────────────────────────────
RUN_PLANNED = "planned"
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
RUN_PARTIAL = "partial"
RUN_STATUSES: tuple[str, ...] = (
    RUN_PLANNED,
    RUN_RUNNING,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_PARTIAL,
)

RUN_MODE_SALES = "sales"
RUN_MODE_FINANCE = "finance"
RUN_MODE_BACKFILL = "backfill"
RUN_MODES: tuple[str, ...] = (RUN_MODE_SALES, RUN_MODE_FINANCE, RUN_MODE_BACKFILL)

# ── Item grain ──────────────────────────────────────────────────────────────
# Careem/Talabat give a real per-line breakdown; Noon/Deliveroo/Keeta give a
# period-window aggregate. The grain says which, so a "missing items" check
# knows whether it is comparing lines or totals.
GRAIN_LINE = "line"
GRAIN_AGGREGATE = "aggregate"
ITEM_GRAINS: tuple[str, ...] = (GRAIN_LINE, GRAIN_AGGREGATE)

#: Statement-line grain: order-level fee rows vs period summary totals.
STATEMENT_GRAIN_ORDER = "order"
STATEMENT_GRAIN_SUMMARY = "summary"
STATEMENT_GRAINS: tuple[str, ...] = (STATEMENT_GRAIN_ORDER, STATEMENT_GRAIN_SUMMARY)

# ── Reconciliation outcome ──────────────────────────────────────────────────
MATCH_MATCHED = "matched"
MATCH_UNMATCHED_AGG = "unmatched_agg"  # aggregator has it, MM does not
MATCH_UNMATCHED_MM = "unmatched_mm"  # MM has it, aggregator does not
MATCH_NO_MAKER_SIDE = "no_maker_side"  # aggregator-only branch: nothing to check
MATCH_STATUSES: tuple[str, ...] = (
    MATCH_MATCHED,
    MATCH_UNMATCHED_AGG,
    MATCH_UNMATCHED_MM,
    MATCH_NO_MAKER_SIDE,
)


class AggregatorBranchMap(Base, UUIDMixin, TimestampMixin):
    """One of our branches, as one marketplace knows it.

    Capability-by-row: a branch trades on a channel iff it has an active row
    here. The external ids are path-scoped on most portals (Careem
    company/brand/outlet), so all three are carried; a channel that needs only
    one (Deliveroo's `branchId`, Keeta's `shopId`, Noon's `n-restaurantcode`)
    leaves the rest null and uses `channel_ref`.
    """

    __tablename__ = "aggregator_branch_map"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The marketplace's own outlet id — the leaf a sale is scoped to.
    external_outlet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_brand_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_company_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: A channel-specific catch-all when the portal keys on one opaque ref
    #: rather than the company/brand/outlet triple.
    channel_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The off switch for one branch on one channel. Careem's Sharjah outlet is
    #: permanently shut, so its row is inactive while every other channel keeps
    #: Sharjah — activeness is per `(channel, branch)`, never global.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (
        UniqueConstraint("channel", "branch_id", name="uq_aggregator_branch_map"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_branch_map_channel"
        ),
        Index("ix_aggregator_branch_map_branch", "branch_id"),
    )

    def __repr__(self) -> str:
        return f"<AggregatorBranchMap {self.channel} branch={self.branch_id}>"


class FoodicsBranchMap(Base, UUIDMixin, TimestampMixin):
    """One of our branches, as Foodics knows it.

    Mirrors `grubops_location_map` so "has Foodics" is a row, not a guess. Today
    Foodics is reached account-wide (`FOODICS_ACCOUNT_NUMBER`) and only the
    GrubOps branches use it; this table lets a future branch have Foodics
    without GrubOps, or the reverse, without a special case.
    """

    __tablename__ = "foodics_branch_map"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    foodics_branch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    def __repr__(self) -> str:
        return f"<FoodicsBranchMap branch={self.branch_id} foodics={self.foodics_branch_id}>"


class AggregatorAccount(Base, UUIDMixin, TimestampMixin):
    """How we sign in to one marketplace account, and with which secrets.

    Distinct from `aggregator_session`: that row is the *derived* cookie/token
    jar, which Cloudflare and token expiry kill on a short clock. This row is
    the durable recipe — `login_method` names the flow in `login.py` (including
    whether an email OTP is required), `credentials_encrypted` holds the
    Fernet blob `{email, password}` the worker fills, and
    `mailbox_encrypted` holds the IMAP login the worker uses to read that OTP.
    `extras` is non-secret portal config (Deliveroo `org_id`).

    Portal and mailbox passwords never leave these columns in the clear. The
    admin health read returns `has_password` / `has_mailbox` and the emails;
    only the worker bearer decrypts.
    """

    __tablename__ = "aggregator_account"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    account_ref: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
    login_method: Mapped[str] = mapped_column(String(32), nullable=False)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: IMAP host/user/password/folder + optional sender/subject filters, so an
    #: OTP channel can pull the code from the inbox that actually receives it.
    mailbox_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    extras: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("channel", "account_ref", name="uq_aggregator_account"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_account_channel"
        ),
        CheckConstraint(
            f"login_method IN ({_LOGIN_METHODS_SQL})",
            name="ck_aggregator_account_login_method",
        ),
    )

    def __repr__(self) -> str:
        return f"<AggregatorAccount {self.channel} method={self.login_method}>"


class AggregatorSession(Base, UUIDMixin, TimestampMixin):
    """The live, encrypted session a bootstrap captured for one channel.

    The httpx providers load this and replay it: `cookies` (incl. the load-
    bearing anti-bot cookie — `_px3`, `bm_sv`, `WEBDFPID`), `tokens` (bearer +
    refresh), and `header_profile` (the exact UA/client-hints/custom-header set
    the browser sent, so the request fingerprint matches the cookie that minted
    it). All three are Fernet blobs — never plaintext at rest.

    Two expiries because they refresh differently: `token_expires_at` is bumped
    by the httpx refresh-token grant; `cookie_expires_at` can only be renewed by
    a lightweight headless "touch" that re-runs the portal's sensor. The warmer
    acts on whichever is approaching; `status` flips to `needs_bootstrap` when
    neither can save it and only a headed browser login will. The Playwright
    `storage_state` blob is what lets a *new* worker (new image, empty volume)
    resume that login instead of asking for it again.
    """

    __tablename__ = "aggregator_session"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The account this session signs in as. One brand today, so it defaults to
    #: empty; carried so a second brand on the same channel is another row, not
    #: a collision.
    account_ref: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )

    cookies_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_profile_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Full Playwright `storage_state` (cookies with domain/path/expiry +
    #: localStorage) plus origin-scoped sessionStorage. This is what a new
    #: worker hydrates after a deploy: the name→value `cookies` column is
    #: enough for httpx replay, but not enough to reopen a logged-in browser.
    storage_state_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cookie_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=SESSION_NEEDS_BOOTSTRAP
    )
    last_bootstrap_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_warmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: When the worker's heal daemon will next attempt this channel's login. It
    #: backs a failing login off exponentially on its own volume (invisible to the
    #: API); publishing the next-attempt time here lets the ingest skip its reauth
    #: WAIT when the worker will not act within it, instead of burning the full
    #: AGGREGATOR_REAUTH_WAIT_SECONDS and recording RUN_FAILED. Cleared on every
    #: successful session push (a fresh login means healthy).
    reauth_backoff_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("channel", "account_ref", name="uq_aggregator_session"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_session_channel"
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in SESSION_STATUSES)})",
            name="ck_aggregator_session_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<AggregatorSession {self.channel} status={self.status}>"


class AggregatorSyncRun(Base, UUIDMixin, TimestampMixin):
    """One pull for one channel — the seam that makes a run reportable.

    A run wraps a sales sweep, a finance sweep or a backfill so a failure is
    recorded rather than silent, and so "when did Talabat sales last land" has
    an answer. `stats` holds counters (rows seen/upserted, pages, truncation).
    """

    __tablename__ = "aggregator_sync_run"

    channel: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    from_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    to_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=RUN_PLANNED
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_sync_run_channel"
        ),
        CheckConstraint(
            f"mode IN ({', '.join(repr(m) for m in RUN_MODES)})",
            name="ck_aggregator_sync_run_mode",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in RUN_STATUSES)})",
            name="ck_aggregator_sync_run_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<AggregatorSyncRun {self.channel} {self.mode} {self.status}>"


#: The integrators the hours fan-out writes to. The five aggregators plus
#: `foodics` — one more than `AGGREGATOR_CHANNELS`, because Foodics carries a
#: single daily branch window even though it is not a marketplace, and its
#: outcome belongs in the same run trail as the aggregators'.
HOURS_SYNC_CHANNELS: tuple[str, ...] = (*AGGREGATOR_CHANNELS, "foodics")
_HOURS_CHANNELS_SQL = ", ".join(f"'{c}'" for c in HOURS_SYNC_CHANNELS)


class BranchHoursSyncRun(Base, UUIDMixin, TimestampMixin):
    """The outcome of one hours push for one (branch, channel).

    `branch_hours_sync` mirrors MM's weekly schedule out to each integrator a
    branch is mapped to. This is the trail that makes each push reportable —
    "did Barsha's hours reach Noon, and if not why" has an answer — and the row
    the admin surface and Sentry read from. Its own table, not a `mode` on
    `aggregator_sync_run`: that one is per-channel with no `branch_id` and a
    channel CHECK that excludes Foodics, and the hours outcome is per
    `(branch, channel)`.

    `planned` holds the dry-run payload summary (endpoint + a compact
    day→window map) so a dry-run pass is auditable without touching a portal;
    `dry_run` records which mode wrote the row.
    """

    __tablename__ = "branch_hours_sync_run"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=RUN_PLANNED
    )
    dry_run: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    planned: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            f"channel IN ({_HOURS_CHANNELS_SQL})",
            name="ck_branch_hours_sync_run_channel",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in RUN_STATUSES)})",
            name="ck_branch_hours_sync_run_status",
        ),
        Index("ix_branch_hours_sync_run_branch", "branch_id"),
        Index("ix_branch_hours_sync_run_channel", "channel"),
        Index("ix_branch_hours_sync_run_created", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<BranchHoursSyncRun {self.channel} branch={self.branch_id} {self.status}>"
        )


class AggregatorOrder(Base, UUIDMixin, TimestampMixin):
    """One order as one marketplace's ledger holds it — the sales truth.

    Money is taken verbatim from the marketplace: it is the number we are
    reconciling *against*, so nothing here is recomputed. `commission_amount`
    is null until a statement attributes it (the order feed rarely carries the
    real cut); zero would mean "nothing charged", which is a different claim.
    `branch_id` is resolved through `aggregator_branch_map` and left null only
    for an order whose outlet we do not yet map.
    """

    __tablename__ = "aggregator_order"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The marketplace's own order reference — the number a human quotes and the
    #: key the reconciliation joins on, always as `(channel, external_order_id)`
    #: because a bare number is reused across channels.
    external_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The marketplace's short customer-facing code when it exposes one SEPARATELY
    #: from `external_order_id` (Noon's `orderRef` "2253" alongside the long
    #: `orderNr`). GrubTech surfaces this same short code as its `externalId`, so
    #: it is the shared key that lets promotion and the GrubOps ingest converge on
    #: one MM order. Null where the one id already IS the short code (most channels).
    display_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    business_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The marketplace's own status word, unconstrained by design — provider
    #: vocabulary, like the courier words on `order_delivery`.
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: The marketplace's delivery address for the order, as it exposes it —
    #: structured (`{line, area, city, building, ...}`) when the portal gives
    #: parts, else `{"text": "..."}`. JSONB to mirror `orders.shipping_address_
    #: snapshot`, which promotion copies this into. Null where the channel masks
    #: it (Keeta redacts the address once an order is delivered) or never gives one.
    customer_address: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    #: The marketplace's own rider for this order — a name and a mobile — captured
    #: at the provider edge, the same little the GrubOps ingest surfaces on
    #: `orders.aggregator_driver_*`. Promotion copies these onto the MM order. Null
    #: until the marketplace assigns a rider, and where the payload masks them.
    driver_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: The marketplace's own delivery-job status word, unconstrained by design
    #: (provider vocabulary, canon rule 6). Null on non-delivered channels.
    driver_status: Mapped[str | None] = mapped_column(String(40), nullable=True)

    gross_sales: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_sales: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    commission_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    payment_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: Merchant-funded promotion charged back to us (Keeta's "Promotion funded by
    #: merchant" / feeDtl activityFee) — a real fee, kept distinct from commission
    #: so the effective commission rate stays clean; the fees roll-up adds it in.
    marketing_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    cancellation_fee: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_payable: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    #: The statement this order settled on, once one is published; null until.
    statement_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    #: The MM order this aggregator order was promoted into (or the GrubOps order
    #: it converged onto for a Barsha/Sharjah sale), and when promotion last
    #: synced it. Null until promoted; `promoted_at` drives the incremental
    #: `promote_channel` pass the way `reconciled_at` drives reconciliation.
    mm_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("channel", "external_order_id", name="uq_aggregator_order"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_order_channel"
        ),
        Index("ix_aggregator_order_business_date", "channel", "business_date"),
        Index("ix_aggregator_order_promoted", "channel", "promoted_at"),
    )

    def __repr__(self) -> str:
        return f"<AggregatorOrder {self.channel} {self.external_order_id}>"


class AggregatorOrderItem(Base, UUIDMixin, TimestampMixin):
    """A sold line, or a period-window aggregate of one item — see `grain`.

    `amount_is_known` is the honest flag: some channels give a name and quantity
    but no per-line money, and a zero there would read as free. The
    missing-items check only trusts `grain='line'` rows.
    """

    __tablename__ = "aggregator_order_item"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The channel-scoped natural key for this line (order id + line index, or
    #: the aggregate's item id + window). Dedupe key for the upsert.
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    aggregator_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("aggregator_order.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    grain: Mapped[str] = mapped_column(String(16), nullable=False)
    item_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    gross_sales: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_sales: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_is_known: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: Structured options `[{name, quantity, unit_price, external_ref}, ...]`.
    modifiers: Mapped[list | dict | None] = mapped_column(JSONB, nullable=True)
    modifiers_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    period_start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(10), nullable=True)

    __table_args__ = (
        UniqueConstraint("channel", "source_key", name="uq_aggregator_order_item"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_order_item_channel"
        ),
        CheckConstraint(
            f"grain IN ({', '.join(repr(g) for g in ITEM_GRAINS)})",
            name="ck_aggregator_order_item_grain",
        ),
    )

    def __repr__(self) -> str:
        return f"<AggregatorOrderItem {self.channel} {self.item_name}>"


class AggregatorOrderStatusEvent(Base, UUIDMixin, TimestampMixin):
    """One order arriving at one marketplace status, at the marketplace's time.

    The channel-side twin of `order_status_events`: where that table records the
    MM order walking OUR lifecycle ladder, this records the marketplace's own,
    richer trace (Keeta's `merchantOrderTraces`, Careem/Deliveroo/Talabat
    timelines) — "order placed", "finding courier", "rider near pickup",
    "delivered" — words we do not map onto our enum and timestamps that are the
    marketplace's, not ours. Captured at the provider edge on the frequent sales
    cadence, so it exists before (and independently of) promotion; promotion can
    then surface it on the promoted order's admin timeline.

    Keyed on `(channel, external_order_id, status)` so a re-scrape upserts the
    same step rather than duplicating it. `status` is provider-verbatim and
    unconstrained by design (canon rule 6). `at` is tz-aware (Dubai wall-clock
    resolved via `ingest._aware_business`, never a naive value into timestamptz).
    """

    __tablename__ = "aggregator_order_status_event"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The marketplace's own status word for this step, verbatim.
    status: Mapped[str] = mapped_column(String(60), nullable=False)
    #: When the marketplace says the step happened (tz-aware).
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Ordinal within the trace when the portal exposes one, so equal/overlapping
    #: timestamps still render in the marketplace's order. Null when absent.
    sequence: Mapped[int | None] = mapped_column(nullable=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "channel",
            "external_order_id",
            "status",
            name="uq_aggregator_order_status_event",
        ),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})",
            name="ck_aggregator_order_status_event_channel",
        ),
        Index(
            "ix_aggregator_order_status_event_order",
            "channel",
            "external_order_id",
            "at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AggregatorOrderStatusEvent {self.channel} "
            f"{self.external_order_id} {self.status}>"
        )


class AggregatorStatement(Base, UUIDMixin, TimestampMixin):
    """A published settlement summary for one channel over one period."""

    __tablename__ = "aggregator_statement"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    statement_id: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(10), nullable=True)
    payment_due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gross_sales: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_payable: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_fees: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_vat: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    #: The payout that settled this statement, resolved by the rollup in
    #: `ingest.link_statements_to_payouts`. One payout pays several statements at
    #: once (a marketplace batches every statement due since the last transfer),
    #: so the FK lives here — many statements → one `aggregator_payout.transfer_id`
    #: — not on the payout, which could only name one. This is the payments leg of
    #: the reconciliation chain: payout ← statement ← line ← order ← mm_order.
    #: Not independently indexed: the composite `ix_aggregator_statement_payout
    #: (channel, payout_transfer_id)` created in migration 161 already serves the
    #: only lookup (`link_statements_to_payouts` filters channel + this column), so
    #: a single-column `index=True` here would just be a redundant index the
    #: migration never builds — i.e. model↔DB drift (CLAUDE.md §8).
    payout_transfer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Marketplace outlet when the statement is per-branch (Talabat detailed).
    external_outlet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Private GCS object key for the archived settlement invoice
    #: (`invoices/{channel}/{statement_id}/…` in GCS_INVOICE_BUCKET). Not a public
    #: URL — served via a short-lived signed URL.
    invoice_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    invoice_content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    invoice_original_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    invoice_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Extra archived files `[{object_key, content_type, original_filename, size_bytes}]`.
    invoice_attachments: Mapped[list | dict | None] = mapped_column(
        JSONB, nullable=True
    )
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("channel", "statement_id", name="uq_aggregator_statement"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_statement_channel"
        ),
    )

    def __repr__(self) -> str:
        return f"<AggregatorStatement {self.channel} {self.statement_id}>"


class AggregatorStatementLine(Base, UUIDMixin, TimestampMixin):
    """One line of a settlement — a sale credit, a fee, a refund, an adjustment.

    `line_type` and `fee_category` are provider words kept verbatim; the
    reconciliation reads them to attribute a real commission or a post-delivery
    refund back to `external_order_id`.
    """

    __tablename__ = "aggregator_statement_line"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    statement_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    transfer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    line_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    line_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fee_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    #: Filled when `(channel, external_order_id)` has a promoted aggregator order.
    mm_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    grain: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=STATEMENT_GRAIN_ORDER
    )

    __table_args__ = (
        UniqueConstraint("channel", "source_key", name="uq_aggregator_statement_line"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})",
            name="ck_aggregator_statement_line_channel",
        ),
        CheckConstraint(
            f"grain IN ({', '.join(repr(g) for g in STATEMENT_GRAINS)})",
            name="ck_aggregator_statement_line_grain",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AggregatorStatementLine {self.channel} {self.line_type} {self.amount}>"
        )


class AggregatorPayout(Base, UUIDMixin, TimestampMixin):
    """A transfer the marketplace made (or scheduled) against a statement."""

    __tablename__ = "aggregator_payout"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    transfer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    statement_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    transfer_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    payment_due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transfer_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    #: Provider status word, unconstrained — "scheduled", "paid", "on_hold".
    transfer_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    __table_args__ = (
        UniqueConstraint("channel", "transfer_id", name="uq_aggregator_payout"),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})", name="ck_aggregator_payout_channel"
        ),
    )

    def __repr__(self) -> str:
        return f"<AggregatorPayout {self.channel} {self.transfer_id}>"


class AggregatorReconciliation(Base, UUIDMixin, TimestampMixin):
    """Where the marketplace's ledger and MM's order disagree — Layer B output.

    One row per matched aggregator order. `match_status` says whether an MM
    order was found at all; `no_maker_side` is an aggregator-only branch (DSO,
    Karama) with nothing to check, deliberately not a discrepancy. The three
    substantive checks each carry their delta plus a boolean flag so the
    dashboard can filter without re-deriving: items, refunds, and commission
    (the marketplace's real `commission_actual` against MM's modelled
    `commission_expected` from `order_fees`). Recomputed idempotently, keyed on
    `(channel, external_order_id)`.
    """

    __tablename__ = "aggregator_reconciliation"

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    aggregator_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("aggregator_order.id", ondelete="SET NULL"),
        nullable=True,
    )
    mm_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_status: Mapped[str] = mapped_column(String(20), nullable=False)

    item_discrepancy: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    item_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    refund_agg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    refund_mm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    refund_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    commission_expected: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    commission_actual: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    commission_variance: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    #: The effective rate actually charged — `commission_actual / total`,
    #: VAT-inclusive — which is the number that validates `couriers`.
    commission_rate_effective: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )

    total_agg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_mm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_variance: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    #: The list of issue codes raised for this order, for a dashboard filter.
    flags: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("aggregator_sync_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "channel", "external_order_id", name="uq_aggregator_reconciliation"
        ),
        CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})",
            name="ck_aggregator_reconciliation_channel",
        ),
        CheckConstraint(
            f"match_status IN ({', '.join(repr(s) for s in MATCH_STATUSES)})",
            name="ck_aggregator_reconciliation_match_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AggregatorReconciliation {self.channel} "
            f"{self.external_order_id} {self.match_status}>"
        )
