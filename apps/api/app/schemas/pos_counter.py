"""
Wire contracts for local-first counter checkout (`/pos/counter/*`).

Read this file to learn the shape; the register (`mm-pos`) codes against it.

**Conventions.** Every money figure and every rate is a **decimal string**:
money with exactly two decimals (`"12.50"`), rates/fractions with four
(`"0.0500"`). The register must parse them as `Decimal`, never `Double`. In
*requests* the server accepts either a JSON string or a JSON number for a
decimal field, but the register should send strings. Timestamps are ISO-8601
with an offset (`2026-09-23T10:15:00Z`). Ids are UUID strings.

Three endpoints, all on both the register host and the main API:

* `GET  /pos/counter/bundle` — the config bundle the register prices from
  (device token). `CounterBundleResponse`.
* `POST /pos/counter/sales` — one finished local sale (device token).
  `CounterSaleRequest` → `CounterSaleResponse`.
* `POST /pos/counter/promote` — move an untendered local check to the server
  as an ordinary open check (staff token). `CounterPromoteRequest` →
  `PosOrderResponse`.
* `POST /pos/counter/shadow` — shadow mode: the register's local figures for a
  server check, compared and recorded (device token). `CounterShadowReport` →
  `CounterShadowResult`.

See `app/api/v1/pos_counter.py` for status codes and headers.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from app.schemas.menu_group import MenuGroupNode
from app.schemas.pos.payment_methods import PaymentMethodResponse
from app.schemas.pos.reasons import ReasonResponse

#: The branch rollout flag (`branches.counter_local_first`).
CounterRollout = Literal["off", "shadow", "on"]
#: What a terminal reports it is running the counter in.
CounterDeviceMode = Literal["online", "shadow", "local"]
PricingStatus = Literal["verified", "mismatch", "unverified"]

Translations = dict[str, dict[str, str]]

_HEX64 = r"^[0-9a-f]{64}$"
_BUSINESS_DATE = r"^\d{4}-\d{2}-\d{2}$"


# ═══ The config bundle ═══════════════════════════════════════════════════════
#
# `bundle` is HASHED: its sha256 over canonical JSON (keys sorted, no
# whitespace, UTF-8) is `hash`, which the register cites on every sale so the
# server re-prices against exactly these inputs. `envelope` is NOT hashed — it
# changes without the pricing inputs changing.


class BundleBranch(BaseModel):
    id: UUID
    name: str
    reference: str
    #: The trading-day cut-off, `HH:MM` local. A sale before it belongs to the
    #: previous business date. Business dates are computed in the bundle's
    #: `timezone` (NOT the device's): `(local_time − cut_off).date()`.
    business_day_start: str
    cash_enabled: bool
    receipt_header: str | None = None
    receipt_footer: str | None = None


class BundleEntity(BaseModel):
    """The legal entity the counter trades under at this branch
    (`tax_identity_service.resolve(branch, 'cashier')`). `vat_registered`
    false ⇒ every line's tax rate is priced as zero."""

    id: UUID | None = None
    reference: str | None = None
    legal_name: str | None = None
    brand_name: str | None = None
    vat_registered: bool = True
    tax_number: str | None = None
    invoice_title: str | None = None
    logo_url: str | None = None


class BundleTax(BaseModel):
    id: UUID
    name: str
    #: Fraction, 4 dp: `"0.0500"` is 5%.
    rate: str
    type: Literal["inclusive", "exclusive"]
    is_active: bool


class BundleResolvedTax(BaseModel):
    """A group reduced to what a line is taxed at (`counter_pricing.tax_tuple`):
    active taxes only, rates summed, the first active tax names it. `NO_TAX`
    (rate 0, name "No tax", tax_id null, inclusive) when none is active. The
    unregistered-entity zeroing is NOT applied here — it is applied per line
    against `entity.vat_registered`."""

    rate: str
    name: str
    tax_id: str | None = None
    inclusive: bool = True


class BundleTaxGroup(BaseModel):
    id: UUID
    name: str
    taxes: list[BundleTax]
    resolved: BundleResolvedTax


class BundleModifierOption(BaseModel):
    id: UUID
    name: str
    translations: Translations = {}
    sku: str
    #: Price of one unit of the option, 2 dp.
    price: str
    is_active: bool
    display_order: int


class BundleModifier(BaseModel):
    id: UUID
    reference: str
    name: str
    translations: Translations = {}
    is_active: bool
    options: list[BundleModifierOption]


class BundleProductModifier(BaseModel):
    """A product↔modifier link and its choice rules (`modifier_rules`): the
    total quantity chosen in the group must be within [minimum, maximum]; the
    first `free_options` units (options in `display_order`, then name) are free;
    `unique_options` (or `maximum_options <= 1`) forbids repeating an option."""

    id: UUID
    modifier_id: UUID
    minimum_options: int
    maximum_options: int
    free_options: int
    unique_options: bool
    display_order: int


class BundleProduct(BaseModel):
    id: UUID
    name: str
    name_localized: str | None = None
    translations: Translations = {}
    sku: str | None = None
    category_id: UUID | None = None
    #: Catalogue price per unit (per kilo when `is_sold_by_weight`), 2 dp. The
    #: register never reads `BranchProduct.price` — neither does the server.
    base_price: str
    #: `fixed` or `open` (the cashier types the price).
    pricing_method: str
    is_sold_by_weight: bool
    #: Staff meals / comps: no tax, excluded from the order discount spread.
    is_non_revenue: bool
    tax_group_id: UUID | None = None
    #: The kitchen station the line routes to for a pickup order (the branch's
    #: first active flow whose categories include the product's category, else
    #: the default flow), pre-computed.
    kitchen_flow_id: UUID | None = None
    image_urls: list[str] = []
    display_order: int = 0
    modifiers: list[BundleProductModifier] = []


class BundlePromotion(BaseModel):
    """A counter promotion that runs at this branch. `promotion_rules` in full:

    * applies only if `is_active`, the reward is `percentage_off_order` or
      `fixed_off_order`, `trigger` is `spend`, `sources` is non-empty and
      contains `cashier`, `branch_ids` is empty or contains the branch,
      `order_types` is empty or contains `pickup`;
    * inside its window at the sale's local clock (bundle `timezone`):
      `from_date <= date <= to_date` (null = open), `weekdays[date.weekday()]`
      (index 0 = Monday), and minutes-of-day `m = hour*60 + minute` within
      `[from_time, to_time]` — or, when `from_time > to_time`, `m >= from_time
      or m <= to_time` (crosses midnight; the weekday is the calendar day of the
      sale, not the day the window opened);
    * spend (Σ (base_price + options_price) × billable qty over non-void lines)
      `>= trigger_value`.

    One promotion per order: a selected coupon (`mode == coupon`) that is
    eligible wins; otherwise the eligible `auto` promotion with the lowest
    `rank`. An ineligible selected coupon falls back to auto.

    `reward_value` is a percent for `percentage_off_order` (`"15.0000"` =
    15%; the fraction used is `reward_value/100` rounded half-up to 4 dp) and
    AED for `fixed_off_order`. With `category_ids`, the discount is applied to
    each billable line whose product's category is listed (a fixed reward takes
    the full fixed amount off *each* such line); without, it is one order-level
    discount spread pro rata across taxable lines.
    """

    id: UUID
    name: str
    reward: str
    reward_value: str
    trigger: str
    trigger_value: str
    category_ids: list[UUID] = []
    branch_ids: list[UUID] = []
    order_types: list[str] = []
    sources: list[str] = []
    mode: Literal["auto", "coupon"]
    #: 0 = best. The bundle lists promotions best-first.
    rank: int
    is_active: bool = True
    from_date: date | None = None
    to_date: date | None = None
    from_time: int = 0
    to_time: int = 1439
    #: Monday … Sunday.
    weekdays: list[bool] = Field(default_factory=lambda: [True] * 7)


class BundleKitchenFlow(BaseModel):
    id: UUID
    name: str
    is_default: bool


class CounterBundleBody(BaseModel):
    """The hashed pricing inputs. Lists are in a stable order."""

    #: `counter_pricing.ENGINE_VERSION` — the arithmetic these inputs are for.
    engine_version: int
    currency_code: str
    currency_symbol: str
    #: IANA zone the promotion clock and business dates run on.
    timezone: str
    #: Cash rounding step for the order total (`"0.25"`); `"0.000"`/`"0"` = none.
    rounding_step: str
    branch: BundleBranch
    entity: BundleEntity
    tax_groups: list[BundleTaxGroup]
    products: list[BundleProduct]
    modifiers: list[BundleModifier]
    #: The branch's menu tree, as `GET /menu-groups/tree?branch_id=` returns it.
    menu_tree: list[MenuGroupNode]
    #: Active payment methods, as `GET /payment-methods` returns them.
    payment_methods: list[PaymentMethodResponse]
    promotions: list[BundlePromotion]
    #: Active `void_return` reasons.
    void_reasons: list[ReasonResponse]
    kitchen_flows: list[BundleKitchenFlow]


class CounterAvailability(BaseModel):
    """The branch's 86 list right now (expiry applied). Selling an 86'd item is
    NOT refused — neither the server nor the register blocks it."""

    unavailable_product_ids: list[UUID] = []
    unavailable_option_ids: list[UUID] = []


class CounterBundleEnvelope(BaseModel):
    #: The mode THIS terminal must run: the branch flag, forced to `off` when
    #: the terminal's `X-App-Build` is below `min_build`.
    counter_local_first: CounterRollout
    #: The branch flag as an admin set it.
    branch_counter_local_first: CounterRollout
    #: The engine version the bundle's inputs are for (== `bundle.engine_version`).
    #: The app must run `.server` mode if it does not implement it.
    pricing_engine_version: int
    #: Versions the server can re-price a synced sale with.
    supported_engine_versions: list[int]
    #: `COUNTER_LOCAL_FIRST_MIN_BUILD`.
    min_build: int
    #: Whether this terminal's reported build is at least `min_build`.
    build_supported: bool
    server_time: datetime
    #: The branch's current business date on the server's clock.
    business_date: str
    #: This terminal's ticket prefix (`T1`), unique at the branch. Printed
    #: numbers are `{ticket_prefix}-{seq:04d}`, seq per device per business day.
    ticket_prefix: str
    #: The highest seq the server has ingested from this prefix for
    #: `business_date` (0 when none) — resumes numbering after a reinstall:
    #: `next = max(local, last_ingested_ticket_seq) + 1`.
    last_ingested_ticket_seq: int
    availability: CounterAvailability


class CounterBundleResponse(BaseModel):
    """`GET /pos/counter/bundle`.

    `hash` = sha256(canonical JSON of `bundle`) — cite it as `bundle_hash` on
    every sale. The HTTP `ETag` is NOT the bundle hash alone: it is
    `"{hash}.{envelope_tag}"`, so a change to the 86 list, the mode or the
    ticket prefix also refreshes a terminal holding an unchanged bundle.
    """

    hash: str
    bundle: CounterBundleBody
    envelope: CounterBundleEnvelope


# ═══ Sale ingest ═════════════════════════════════════════════════════════════


class CounterSaleOption(BaseModel):
    modifier_option_id: UUID
    quantity: int = Field(1, ge=1, le=1000)


class CounterSaleLineTotals(BaseModel):
    """What the register priced (and printed) for one line — the
    `counter_pricing.LinePricing` fields."""

    base_price: Decimal
    options_price: Decimal
    unit_price: Decimal
    gross: Decimal
    discount: Decimal
    total_price: Decimal
    tax_amount: Decimal
    tax_exclusive_total: Decimal
    tax_exclusive_unit: Decimal


class CounterSaleLine(BaseModel):
    #: The register's line id; becomes `order_items.id`.
    id: UUID
    product_id: UUID
    quantity: int = Field(ge=1, le=10000)
    #: Only for an open-price product: the typed price per unit. Otherwise
    #: omit — the price is the bundle's.
    unit_price: Decimal | None = Field(None, ge=0)
    #: Kilos for a product sold by weight (3 dp); otherwise omit.
    weight: Decimal | None = Field(None, gt=0)
    options: list[CounterSaleOption] = []
    kitchen_notes: str | None = Field(None, max_length=2000)
    kitchen_flow_id: UUID | None = None
    added_at: AwareDatetime | None = None
    #: When this line went to the kitchen (a docket printed), or null.
    sent_to_kitchen_at: AwareDatetime | None = None
    #: A line voided on the check (e.g. after its docket printed). Not billed.
    voided: bool = False
    voided_at: AwareDatetime | None = None
    voided_by_id: UUID | None = None
    void_reason_id: UUID | None = None
    #: Required for a non-voided line.
    totals: CounterSaleLineTotals | None = None


class CounterSaleTender(BaseModel):
    #: The register's tender id; becomes `order_payments.id`.
    id: UUID
    #: Minted ONCE when the tender is created and persisted with it; a retry
    #: resends the same key. ≤ 64 chars.
    idempotency_key: str = Field(min_length=1, max_length=64)
    payment_method_id: UUID
    amount: Decimal = Field(gt=0)
    #: Cash handed over (≥ amount). Omit for exact / non-cash.
    tendered: Decimal | None = Field(None, ge=0)
    #: Informational — the server derives change as `tendered − amount`.
    change: Decimal | None = Field(None, ge=0)
    taken_at: AwareDatetime
    #: Who took it; default the sale's cashier.
    user_id: UUID | None = None
    reference: str | None = Field(None, max_length=120)


class CounterSaleKitchenTicket(BaseModel):
    """A docket the register printed."""

    #: 1, 2, … per sale.
    sequence: int = Field(ge=1, le=1000)
    kitchen_flow_id: UUID | None = None
    line_ids: list[UUID] = Field(min_length=1)
    sent_at: AwareDatetime
    printed_at: AwareDatetime | None = None


class CounterSaleTaxLine(BaseModel):
    tax_id: str | None = None
    name: str
    rate: Decimal
    taxable_amount: Decimal
    amount: Decimal


class CounterSaleTotals(BaseModel):
    """The printed receipt's figures — `counter_pricing.CheckPricing`."""

    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total_excl_tax: Decimal
    rounding: Decimal
    total: Decimal
    #: The promotion the register applied (null for none) and what it took off.
    promotion_id: UUID | None = None
    promotion_amount: Decimal = Decimal("0")
    taxes: list[CounterSaleTaxLine] = []


class CounterSaleRequest(BaseModel):
    """`POST /pos/counter/sales` — one finished local counter sale.

    Idempotent on `id`: a retry of the same sale is answered `200 replayed`.
    The replay fingerprint is sha256 over the canonical JSON of this body
    EXCLUDING `receipt_printed_at`, `kitchen_tickets[].printed_at`,
    `clock_offset_ms`, `staff_attestation`, `app_version` and `app_build` — so
    recording a print result after the first attempt does not turn a retry into
    a conflict. Anything else changed under the same id is a `409`.
    """

    #: The register's order id (UUIDv4); becomes `orders.id` and
    #: `orders.client_request_id`.
    id: UUID
    branch_id: UUID
    device_id: UUID
    #: The till the sale was rung on (open on this device at sale time).
    till_id: UUID
    cashier_id: UUID
    #: The PIN sign-in access token (`POST /staff/pin-login`) that was live when
    #: the check was opened. Verified with its signature but NOT its expiry:
    #: `iat <= opened_at <= iat + 13h`, and it must name `cashier_id`, active
    #: staff at the branch. Missing/invalid adds the `attestation_invalid` flag;
    #: the sale is still booked.
    staff_attestation: str | None = Field(None, max_length=4096)
    ticket_prefix: str = Field(min_length=1, max_length=6)
    ticket_seq: int = Field(ge=1, le=999999)
    #: `f"{ticket_prefix}-{ticket_seq:04d}"`.
    display_number: str = Field(min_length=3, max_length=20)
    business_date: str = Field(pattern=_BUSINESS_DATE)
    opened_at: AwareDatetime
    #: The instant the check was priced (promotion clock). Clamped by the
    #: server to [opened_at, closed_at].
    priced_at: AwareDatetime
    closed_at: AwareDatetime
    #: server_time − device_time, ms, when known. Informational.
    clock_offset_ms: int | None = None
    bundle_hash: str = Field(pattern=_HEX64)
    engine_version: int = Field(ge=1)
    app_version: str | None = Field(None, max_length=30)
    app_build: str | None = Field(None, max_length=20)
    #: `closed` = paid and done; `void` = abandoned after a docket printed (no
    #: tenders allowed).
    state: Literal["closed", "void"] = "closed"
    void_reason_id: UUID | None = None
    #: The coupon chip the cashier selected, if any (selected ≠ applied).
    coupon_promotion_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=150)
    customer_phone: str | None = Field(None, max_length=30)
    notes: str | None = Field(None, max_length=2000)
    lines: list[CounterSaleLine] = Field(min_length=1)
    tenders: list[CounterSaleTender] = []
    totals: CounterSaleTotals
    kitchen_tickets: list[CounterSaleKitchenTicket] = []
    receipt_printed_at: AwareDatetime | None = None

    @field_validator("ticket_prefix")
    @classmethod
    def _prefix_shape(cls, v: str) -> str:
        if not v.isalnum():
            raise ValueError("ticket_prefix must be alphanumeric")
        return v


class CounterSaleResponse(BaseModel):
    """`201` ingested · `200` replayed · `202` quarantined."""

    status: Literal["ingested", "replayed", "quarantined"]
    order_id: UUID
    order_number: str | None = None
    display_number: str | None = None
    check_number: int | None = None
    pricing_status: PricingStatus | None = None
    ingested_late: bool = False
    #: Non-fatal facts recorded on the order (see `orders.ingest_flags`).
    flags: list[str] = []
    #: Why a quarantined sale could not be booked.
    error: str | None = None


# ═══ Promote ═════════════════════════════════════════════════════════════════


class CounterPromoteLine(BaseModel):
    id: UUID
    product_id: UUID
    quantity: int = Field(ge=1, le=10000)
    unit_price: Decimal | None = Field(None, ge=0)
    weight: Decimal | None = Field(None, gt=0)
    options: list[CounterSaleOption] = []
    kitchen_notes: str | None = Field(None, max_length=2000)
    added_at: AwareDatetime | None = None
    sent_to_kitchen_at: AwareDatetime | None = None


class CounterPromoteRequest(BaseModel):
    """`POST /pos/counter/promote` — "Move to server" for an UNTENDERED local
    check (park, split, table, manual discount need the server). Idempotent on
    `id`: promoting a check already on the server returns it."""

    id: UUID
    branch_id: UUID
    till_id: UUID | None = None
    device_id: UUID | None = None
    opened_at: AwareDatetime | None = None
    coupon_promotion_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=150)
    customer_phone: str | None = Field(None, max_length=30)
    notes: str | None = Field(None, max_length=2000)
    lines: list[CounterPromoteLine] = []
    kitchen_tickets: list[CounterSaleKitchenTicket] = []
    #: The ticket number the register already printed on a docket (all four
    #: together, or none): the promoted check keeps it, so the docket and the
    #: receipt name the same check. Ignored if this device does not own the
    #: prefix or the number is already taken that day.
    ticket_prefix: str | None = Field(None, min_length=1, max_length=6)
    ticket_seq: int | None = Field(None, ge=1, le=999999)
    display_number: str | None = Field(None, min_length=3, max_length=20)
    business_date: str | None = Field(None, pattern=_BUSINESS_DATE)


# ═══ Shadow mode ═════════════════════════════════════════════════════════════


class CounterShadowLine(BaseModel):
    #: The SERVER order item id (in shadow mode the check is a server check).
    id: UUID
    totals: CounterSaleLineTotals


class CounterShadowReport(BaseModel):
    """`POST /pos/counter/shadow` — in `shadow` mode the server check is the
    real one; after each re-price (or at settle) the register also prices the
    same lines with its local engine against its bundle and reports its figures
    here. The server compares them with the check's own and records any
    difference. Fire-and-forget: the answer never changes the sale."""

    order_id: UUID
    bundle_hash: str = Field(pattern=_HEX64)
    engine_version: int = Field(ge=1)
    totals: CounterSaleTotals
    lines: list[CounterShadowLine] = []


class CounterShadowDifference(BaseModel):
    field: str
    server: str
    client: str


class CounterShadowResult(BaseModel):
    order_id: UUID
    matches: bool
    differences: list[CounterShadowDifference] = []


# ═══ Heartbeat / console ═════════════════════════════════════════════════════


class DeviceHeartbeatRequest(BaseModel):
    """Optional body of `POST /devices/heartbeat`. Every field optional; an
    older build sends no body at all and nothing changes."""

    pending_sales: int | None = Field(None, ge=0, le=100000)
    parked_sales: int | None = Field(None, ge=0, le=100000)
    oldest_pending_at: AwareDatetime | None = None
    counter_mode: CounterDeviceMode | None = None


class CounterSyncOrderRow(BaseModel):
    id: UUID
    order_number: str
    display_number: str | None
    branch_id: UUID | None
    device_id: UUID | None
    business_date: str | None
    total: Decimal
    pricing_status: str | None
    ingested_late: bool
    ingest_flags: list[str]
    closed_at: datetime | None
    ingested_at: datetime | None
    pricing_audit: dict | None = None


class CounterQuarantineRow(BaseModel):
    id: UUID
    device_id: UUID | None
    branch_id: UUID | None
    error: str
    received_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None
    display_number: str | None = None
    total: str | None = None
    payload: dict


class CounterDeviceSyncRow(BaseModel):
    device_id: UUID
    name: str
    branch_id: UUID
    ticket_prefix: str | None
    build_number: str | None
    counter_mode: str | None
    pending_sales: int | None
    parked_sales: int | None
    oldest_pending_sale_at: datetime | None
    sync_reported_at: datetime | None
    last_seen_at: datetime | None


class CounterSyncOverview(BaseModel):
    orders: list[CounterSyncOrderRow]
    quarantine: list[CounterQuarantineRow]
    devices: list[CounterDeviceSyncRow]


class QuarantineResolveRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


__all__ = [
    "BundleBranch",
    "BundleEntity",
    "BundleKitchenFlow",
    "BundleModifier",
    "BundleModifierOption",
    "BundleProduct",
    "BundleProductModifier",
    "BundlePromotion",
    "BundleResolvedTax",
    "BundleTax",
    "BundleTaxGroup",
    "CounterAvailability",
    "CounterBundleBody",
    "CounterBundleEnvelope",
    "CounterBundleResponse",
    "CounterDeviceMode",
    "CounterDeviceSyncRow",
    "CounterPromoteLine",
    "CounterPromoteRequest",
    "CounterQuarantineRow",
    "CounterRollout",
    "CounterSaleKitchenTicket",
    "CounterSaleLine",
    "CounterSaleLineTotals",
    "CounterSaleOption",
    "CounterSaleRequest",
    "CounterSaleResponse",
    "CounterSaleTaxLine",
    "CounterSaleTender",
    "CounterSaleTotals",
    "CounterShadowDifference",
    "CounterShadowLine",
    "CounterShadowReport",
    "CounterShadowResult",
    "CounterSyncOrderRow",
    "CounterSyncOverview",
    "DeviceHeartbeatRequest",
    "PricingStatus",
    "QuarantineResolveRequest",
]
