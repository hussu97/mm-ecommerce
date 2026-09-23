"""
Book a local-first counter sale the register already rang up, paid and printed.

`POST /pos/counter/sales` lands here. The register (device token) sends ONE
self-contained, idempotent sale record; it is booked in the request's single
transaction as an ordinary counter order, through the same service functions
the server-authoritative register uses — so reports, the settled-order sweeper,
VAT, inventory depletion and the till all treat it like any other sale.

The money has already moved and a receipt has printed, so **a paid sale is
never simply refused**:

1. **Replay.** An order (or quarantine row) with the sale's id and the same
   payload fingerprint is a retry → `replayed`. The same id with a different
   fingerprint → 409 (the register parks it).
2. **Structural failure** — another branch's device, a till that is not this
   device's, a ticket prefix that is not this till's, an unknown payment method,
   cashier or product, tenders that do not add up to the receipt — is written to
   `counter_sale_quarantine` and answered 202, so the register can clear it and a
   manager resolves it in the console.
3. Everything else is booked, and whatever is off is recorded as a **flag** on
   the order (`ingest_flags`), never a refusal: an inactive product, a modifier
   rule the pick broke, an invalid staff attestation, a business date that
   disagrees with the close time, a late till or day.

The booking itself:

* the trading day the register named (`get_or_open_for_date`);
* `open_order(order_id=…, business_date=…, display_number=…, opened_at=…)` —
  the shared per-branch check number is still drawn under its advisory lock;
  the order number is `POS-{ref≤10}-{date}-{display_number}`;
* lines via `_build_item(price_snapshot=<bundle row>)`;
* `recalculate(ctx=<the bundle the sale cites>, at=priced_at)`, `priced_at`
  clamped to `[opened_at, closed_at]`;
* compared with the receipt: equal → `verified`; different → the receipt's
  figures are adopted (it is the tax invoice), `mismatch`, the server's own
  figures kept in `pricing_audit`, and an alert email; an unknown bundle →
  priced from current data, `unverified`;
* the dockets the register printed (`origin='device'`);
* each tender via `record_payment` (its own idempotency key, `recorded_at`,
  closed till allowed, inactive method allowed);
* `close_order(closed_at=…)` — lifecycle, fees and inventory depletion — or the
  open-check void for `state='void'`;
* late: a closed till is restated (`restamp_closed_till`), a closed day too
  (`restamp_closed_day`), and a date older than the VAT ledger's trailing window
  is recomputed (`vat_ledger.compute_window`).

The settled-order sweeper cannot see a half-written sale: nothing is committed
until the whole of it is.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from jose import JWTError, jwt
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.config import settings
from app.core.deps import _issued_before_password_change
from app.core.money import money
from app.core.security import ALGORITHM
from app.models.base import utcnow
from app.models.branch import Branch, BranchBusinessDay
from app.models.device import Device
from app.models.kitchen_flow import KitchenFlow
from app.models.marketing import Promotion
from app.models.modifier import Modifier, ProductModifier
from app.models.order import Order, OrderItem
from app.models.payment_method import PaymentMethod
from app.models.pos_counter import CounterSaleQuarantine
from app.models.pos_order import (
    DiscountSourceEnum,
    OrderDiscount,
    OrderItemStatusEnum,
    OrderPayment,
    OrderSourceEnum,
    OrderTax,
    OrderTypeEnum,
)
from app.models.product import Product
from app.models.reason import Reason
from app.models.role import UserBranch
from app.models.till import Till, TillStatusEnum
from app.models.user import User
from app.schemas.pos_counter import CounterSaleRequest, CounterSaleResponse
from app.services import email_service
from app.services.catalog import modifier_rules
from app.services.pos import (
    business_day_service,
    counter_bundle_service,
    counter_pricing,
    pos_order_service,
    till_service,
)
from app.services.pos.counter_pricing import fmt_money

logger = logging.getLogger(__name__)

#: Serialises two concurrent syncs of the same sale (a retry racing the
#: original), so the second sees the first's committed order and replays.
_SALE_LOCK_NS = 0x4D4D_5341

#: How far after the PIN sign-in a check may be opened on its attestation. The
#: PIN token lives 12 h; an hour of slack covers a till left signed in.
ATTESTATION_WINDOW = timedelta(hours=13)
#: Clock skew tolerated before `iat`.
_ATTESTATION_SKEW = timedelta(minutes=5)


# ─── Flags ────────────────────────────────────────────────────────────────────
# The vocabulary of `orders.ingest_flags`. Stable strings: the console filters
# on them.
FLAG_ATTESTATION_INVALID = "attestation_invalid"
FLAG_BUSINESS_DATE_DISAGREES = "business_date_disagrees"
FLAG_TIMESTAMPS_REORDERED = "timestamps_reordered"
FLAG_UNKNOWN_BUNDLE = "unknown_bundle"
FLAG_UNSUPPORTED_ENGINE = "unsupported_engine"
FLAG_INACTIVE_PRODUCT = "inactive_product"
FLAG_PRODUCT_NOT_IN_BUNDLE = "product_not_in_bundle"
FLAG_MODIFIER_RULE = "modifier_rule"
FLAG_OPEN_PRICE = "open_price"
FLAG_WEIGHT = "weight"
FLAG_UNKNOWN_KITCHEN_FLOW = "unknown_kitchen_flow"
FLAG_UNKNOWN_COUPON = "unknown_coupon"
FLAG_UNKNOWN_REASON = "unknown_reason"
FLAG_INACTIVE_PAYMENT_METHOD = "inactive_payment_method"
FLAG_CASHIER_NOT_TILL_OWNER = "cashier_not_till_owner"
FLAG_TILL_CLOSED = "till_closed"
FLAG_DAY_CLOSED = "day_closed"
FLAG_PRICING_MISMATCH = "pricing_mismatch"


class Quarantine(Exception):
    """A structural failure: the sale cannot be booked as an order."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


class SaleConflict(Exception):
    """The sale id is already taken by different content."""


@dataclass
class IngestResult:
    status_code: int
    response: CounterSaleResponse


@dataclass
class _Checked:
    """What the structural pass resolved, for the booking pass."""

    branch: Branch
    till: Till
    cashier: User
    products: dict[uuid.UUID, Product]
    methods: dict[uuid.UUID, PaymentMethod]
    tender_users: dict[uuid.UUID, User]
    flags: list[str] = field(default_factory=list)


# ─── Fingerprint ──────────────────────────────────────────────────────────────


def fingerprint(sale: CounterSaleRequest) -> str:
    """The replay fingerprint: the sale minus what may legitimately be recorded
    after the first attempt (print results, clock offset, attestation, the app
    build it was retried from). See `CounterSaleRequest`."""
    data = sale.model_dump(
        mode="json",
        exclude={
            "receipt_printed_at",
            "clock_offset_ms",
            "staff_attestation",
            "app_version",
            "app_build",
        },
    )
    for ticket in data.get("kitchen_tickets", []):
        ticket.pop("printed_at", None)
    return counter_bundle_service.sha256_hex(data)


# ─── Attestation ──────────────────────────────────────────────────────────────


async def attestation_problem(
    db: AsyncSession,
    *,
    token: str | None,
    cashier_id: uuid.UUID,
    branch_id: uuid.UUID,
    opened_at: datetime,
) -> str | None:
    """Why the staff attestation does not prove the cashier, or None if it does.

    The attestation is the PIN sign-in access token that was live when the check
    was opened. Its signature is verified but its expiry is not — the outbox may
    drain long after the 12 h token lapsed — and in its place the check must have
    been opened within `ATTESTATION_WINDOW` of the token's `iat`. It must name
    the cashier, who must be active staff assigned to the branch (or an admin),
    and must not predate a password change.
    """
    if not token:
        return "missing"
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_exp": False},
        )
    except JWTError:
        return "bad_signature"
    if payload.get("type") != "access":
        return "wrong_type"
    if str(payload.get("sub")) != str(cashier_id):
        return "wrong_cashier"
    iat_raw = payload.get("iat")
    if iat_raw is None:
        return "no_iat"
    try:
        iat = datetime.fromtimestamp(float(iat_raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return "no_iat"
    if not (iat - _ATTESTATION_SKEW <= opened_at <= iat + ATTESTATION_WINDOW):
        return "outside_window"
    user = await db.get(User, cashier_id)
    if user is None or not user.is_active:
        return "inactive_user"
    if _issued_before_password_change(payload, user):
        return "session_revoked"
    if not user.is_admin:
        if not user.is_staff:
            return "not_staff"
        assigned = (
            await db.execute(
                select(UserBranch.id).where(
                    UserBranch.user_id == user.id, UserBranch.branch_id == branch_id
                )
            )
        ).first()
        if assigned is None:
            return "not_branch_staff"
    return None


# ─── Replay and quarantine ────────────────────────────────────────────────────


def _response_for(order: Order, status: str) -> CounterSaleResponse:
    return CounterSaleResponse(
        status=status,  # type: ignore[arg-type]
        order_id=order.id,
        order_number=order.order_number,
        display_number=order.display_number,
        check_number=order.check_number,
        pricing_status=order.pricing_status,  # type: ignore[arg-type]
        ingested_late=bool(order.ingested_late),
        flags=list(order.ingest_flags or []),
    )


async def _replay(
    db: AsyncSession, sale: CounterSaleRequest, sha: str
) -> IngestResult | None:
    existing = await db.get(Order, sale.id)
    if existing is not None:
        if existing.ingest_payload_sha == sha:
            return IngestResult(200, _response_for(existing, "replayed"))
        raise SaleConflict(
            "A different sale is already booked under this id"
            if existing.ingest_payload_sha
            else "This id belongs to a check already on the server"
        )
    parked = await db.get(CounterSaleQuarantine, sale.id)
    if parked is not None:
        if parked.payload_sha == sha:
            return IngestResult(
                202,
                CounterSaleResponse(
                    status="quarantined",
                    order_id=sale.id,
                    display_number=sale.display_number,
                    error=parked.error,
                ),
            )
        raise SaleConflict("A different sale is already quarantined under this id")
    return None


async def _quarantine(
    db: AsyncSession,
    *,
    device: Device,
    sale: CounterSaleRequest,
    sha: str,
    error: str,
) -> IngestResult:
    await db.execute(
        pg_insert(CounterSaleQuarantine)
        .values(
            id=sale.id,
            device_id=device.id,
            branch_id=device.branch_id,
            payload=sale.model_dump(mode="json"),
            payload_sha=sha,
            error=error,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    logger.warning(
        "Counter sale %s (%s) from device %s quarantined: %s",
        sale.id,
        sale.display_number,
        device.reference,
        error,
    )
    return IngestResult(
        202,
        CounterSaleResponse(
            status="quarantined",
            order_id=sale.id,
            display_number=sale.display_number,
            error=error,
        ),
    )


async def _structural_check(
    db: AsyncSession, *, device: Device, sale: CounterSaleRequest
) -> _Checked:
    """Everything that makes a sale unbookable, raised as `Quarantine`."""
    if sale.branch_id != device.branch_id:
        raise Quarantine("wrong_branch: the sale names another branch")
    if sale.device_id != device.id:
        raise Quarantine("wrong_device: the sale names another device")
    if not device.ticket_prefix or sale.ticket_prefix != device.ticket_prefix:
        raise Quarantine(
            f"wrong_ticket_prefix: {sale.ticket_prefix!r} is not this till's "
            f"prefix ({device.ticket_prefix!r})"
        )
    if sale.display_number != f"{sale.ticket_prefix}-{sale.ticket_seq:04d}":
        raise Quarantine("display_number_malformed")
    try:
        date.fromisoformat(sale.business_date)
    except ValueError as exc:
        raise Quarantine("business_date_malformed") from exc

    branch = await db.get(Branch, device.branch_id)
    if branch is None:  # pragma: no cover — FK
        raise Quarantine("unknown_branch")

    till = await db.get(Till, sale.till_id)
    if till is None or till.branch_id != branch.id or till.device_id != device.id:
        raise Quarantine("unknown_till: not a till opened on this device")

    cashier = await db.get(User, sale.cashier_id)
    if cashier is None:
        raise Quarantine("unknown_cashier")

    line_ids = [line.id for line in sale.lines]
    if len(set(line_ids)) != len(line_ids):
        raise Quarantine("duplicate_line_id")
    if (
        await db.execute(select(OrderItem.id).where(OrderItem.id.in_(line_ids)))
    ).first() is not None:
        raise Quarantine("line_id_reused: a line id already belongs to an order")

    product_ids = {line.product_id for line in sale.lines}
    products = {
        p.id: p
        for p in (await db.execute(select(Product).where(Product.id.in_(product_ids))))
        .scalars()
        .all()
    }
    missing = product_ids - set(products)
    if missing:
        raise Quarantine(f"unknown_product: {sorted(str(m) for m in missing)}")

    for line in sale.lines:
        if not line.voided and line.totals is None:
            raise Quarantine(f"line_totals_missing: {line.id}")

    known_lines = set(line_ids)
    sequences = [t.sequence for t in sale.kitchen_tickets]
    if len(set(sequences)) != len(sequences):
        raise Quarantine("duplicate_kitchen_ticket_sequence")
    for ticket in sale.kitchen_tickets:
        if not set(ticket.line_ids) <= known_lines:
            raise Quarantine("kitchen_ticket_unknown_line")

    methods: dict[uuid.UUID, PaymentMethod] = {}
    tender_users: dict[uuid.UUID, User] = {cashier.id: cashier}
    for tender in sale.tenders:
        method = await db.get(PaymentMethod, tender.payment_method_id)
        if method is None:
            raise Quarantine(f"unknown_payment_method: {tender.payment_method_id}")
        methods[method.id] = method
        if tender.user_id and tender.user_id not in tender_users:
            user = await db.get(User, tender.user_id)
            if user is None:
                raise Quarantine(f"unknown_tender_user: {tender.user_id}")
            tender_users[user.id] = user

    keys = [t.idempotency_key for t in sale.tenders]
    if len(set(keys)) != len(keys):
        raise Quarantine("duplicate_tender_key")
    if keys:
        reused = (
            await db.execute(
                select(OrderPayment.id).where(
                    OrderPayment.idempotency_key.in_(keys),
                    OrderPayment.order_id != sale.id,
                )
            )
        ).first()
        if reused is not None:
            raise Quarantine("tender_key_reused: a tender key belongs to another order")

    tendered = money(sum((t.amount for t in sale.tenders), Decimal("0")))
    if sale.state == "void":
        if sale.tenders:
            raise Quarantine("void_with_tenders: refunds are online-only")
    elif tendered != money(sale.totals.total):
        raise Quarantine(
            f"tenders_do_not_match_total: tenders {tendered} vs receipt "
            f"{money(sale.totals.total)}"
        )

    taken = (
        await db.execute(
            select(Order.id).where(
                Order.branch_id == branch.id,
                Order.business_date == sale.business_date,
                Order.display_number == sale.display_number,
                Order.id != sale.id,
            )
        )
    ).first()
    if taken is not None:
        raise Quarantine("display_number_taken: another sale already has this number")

    return _Checked(
        branch=branch,
        till=till,
        cashier=cashier,
        products=products,
        methods=methods,
        tender_users=tender_users,
    )


# ─── Lines ────────────────────────────────────────────────────────────────────


def _bundle_links(product_row: dict, modifiers_by_id: dict[str, dict]) -> list:
    """A bundle product's modifier links, shaped like `ProductModifier` rows
    for `modifier_rules.resolve_links`."""
    links = []
    for link in sorted(
        product_row.get("modifiers", []),
        key=lambda item: (item["display_order"], item["id"]),
    ):
        modifier = modifiers_by_id.get(str(link["modifier_id"]))
        if modifier is None:
            continue
        links.append(
            SimpleNamespace(
                id=uuid.UUID(str(link["id"])),
                modifier_id=uuid.UUID(str(link["modifier_id"])),
                minimum_options=int(link["minimum_options"]),
                maximum_options=int(link["maximum_options"]),
                free_options=int(link["free_options"]),
                unique_options=bool(link["unique_options"]),
                display_order=int(link["display_order"]),
                modifier=SimpleNamespace(
                    name=modifier["name"],
                    translations=modifier.get("translations") or {},
                    is_active=bool(modifier["is_active"]),
                    options=[
                        SimpleNamespace(
                            id=uuid.UUID(str(o["id"])),
                            name=o["name"],
                            translations=o.get("translations") or {},
                            sku=o["sku"],
                            price=Decimal(str(o["price"])),
                            is_active=bool(o["is_active"]),
                            display_order=int(o["display_order"]),
                        )
                        for o in modifier.get("options", [])
                    ],
                ),
            )
        )
    return links


async def _db_links(db: AsyncSession, product_id: uuid.UUID) -> list:
    return list(
        (
            await db.execute(
                select(ProductModifier)
                .where(ProductModifier.product_id == product_id)
                .options(
                    joinedload(ProductModifier.modifier).selectinload(Modifier.options)
                )
                .order_by(ProductModifier.display_order)
            )
        )
        .scalars()
        .unique()
    )


async def _valid_ids(db: AsyncSession, model, ids: set) -> set:
    ids = {i for i in ids if i is not None}
    if not ids:
        return set()
    return set(
        (await db.execute(select(model.id).where(model.id.in_(ids)))).scalars().all()
    )


async def _build_lines(
    db: AsyncSession,
    *,
    order: Order,
    sale: CounterSaleRequest,
    checked: _Checked,
    bundle_payload: dict,
) -> dict[uuid.UUID, OrderItem]:
    flags = checked.flags
    products_by_id = {str(p["id"]): p for p in bundle_payload.get("products", [])}
    modifiers_by_id = {str(m["id"]): m for m in bundle_payload.get("modifiers", [])}

    # One lookup for every flow the sale can land on: the lines' own, the
    # tickets', and the bundle's pre-routed one per product.
    bundle_flows: set[uuid.UUID] = set()
    for line in sale.lines:
        routed = (products_by_id.get(str(line.product_id)) or {}).get("kitchen_flow_id")
        if routed:
            try:
                bundle_flows.add(uuid.UUID(str(routed)))
            except ValueError:
                pass
    flow_ids = await _valid_ids(
        db,
        KitchenFlow,
        {line.kitchen_flow_id for line in sale.lines}
        | {t.kitchen_flow_id for t in sale.kitchen_tickets}
        | bundle_flows,
    )
    reason_ids = await _valid_ids(
        db, Reason, {line.void_reason_id for line in sale.lines}
    )
    voider_ids = await _valid_ids(db, User, {line.voided_by_id for line in sale.lines})

    items: dict[uuid.UUID, OrderItem] = {}
    for line in sale.lines:
        product = checked.products[line.product_id]
        row = products_by_id.get(str(line.product_id))
        if not product.is_active:
            flags.append(FLAG_INACTIVE_PRODUCT)

        if row is not None:
            links = _bundle_links(row, modifiers_by_id)
            snapshot = pos_order_service.PriceSnapshot(
                base_price=Decimal(str(row["base_price"])),
                name=row.get("name"),
                sku=row.get("sku"),
            )
            pricing_method = row.get("pricing_method") or "fixed"
            by_weight = bool(row.get("is_sold_by_weight"))
            bundle_flow = row.get("kitchen_flow_id")
        else:
            flags.append(FLAG_PRODUCT_NOT_IN_BUNDLE)
            links = await _db_links(db, product.id)
            snapshot = pos_order_service.PriceSnapshot(
                base_price=Decimal(str(product.base_price or 0))
            )
            pricing_method = product.pricing_method or "fixed"
            by_weight = bool(product.is_sold_by_weight)
            bundle_flow = None

        resolved, violations = modifier_rules.resolve_links(
            links,
            [
                modifier_rules.Selection(
                    option_id=o.modifier_option_id, quantity=o.quantity
                )
                for o in line.options
            ],
            product_name=product.name,
            strict=False,
        )
        if violations:
            flags.append(FLAG_MODIFIER_RULE)

        unit_price_override = None
        if pricing_method == "open":
            if line.unit_price is None:
                flags.append(FLAG_OPEN_PRICE)
                unit_price_override = Decimal("0")
            else:
                unit_price_override = line.unit_price
        elif line.unit_price is not None:
            flags.append(FLAG_OPEN_PRICE)

        weight = line.weight
        if by_weight and weight is None:
            flags.append(FLAG_WEIGHT)
        elif not by_weight and weight is not None:
            flags.append(FLAG_WEIGHT)
            weight = None

        flow_id = line.kitchen_flow_id if line.kitchen_flow_id in flow_ids else None
        if line.kitchen_flow_id is not None and flow_id is None:
            flags.append(FLAG_UNKNOWN_KITCHEN_FLOW)
        if flow_id is None and bundle_flow:
            candidate = uuid.UUID(str(bundle_flow))
            if candidate in flow_ids:
                flow_id = candidate

        item = await pos_order_service._build_item(
            db,
            order=order,
            user=checked.cashier,
            product=product,
            quantity=line.quantity,
            resolved=resolved,
            unit_price_override=unit_price_override,
            weight=weight,
            kitchen_notes=line.kitchen_notes,
            price_snapshot=snapshot,
            kitchen_flow_id=flow_id,
            route_to_kitchen=flow_id is None,
            added_at=line.added_at or sale.opened_at,
            item_id=line.id,
        )
        if line.sent_to_kitchen_at is not None:
            item.sent_to_kitchen_at = line.sent_to_kitchen_at
        if line.voided:
            item.status = OrderItemStatusEnum.VOID.value
            item.voided_at = line.voided_at or sale.closed_at
            item.voided_by_id = (
                line.voided_by_id
                if line.voided_by_id in voider_ids
                else checked.cashier.id
            )
            if line.void_reason_id is not None:
                if line.void_reason_id in reason_ids:
                    item.void_reason_id = line.void_reason_id
                else:
                    flags.append(FLAG_UNKNOWN_REASON)
        items[line.id] = item
    await db.flush()
    return items


# ─── Comparing with the receipt ───────────────────────────────────────────────


def _billable(item: OrderItem) -> int:
    return max(item.quantity - (item.returned_quantity or 0), 0)


def _live(order: Order) -> list[OrderItem]:
    return [
        i
        for i in order.items
        if (i.status or OrderItemStatusEnum.ACTIVE.value)
        != OrderItemStatusEnum.VOID.value
    ]


def _server_promotion(order: Order) -> tuple[uuid.UUID | None, Decimal]:
    rows = [
        d
        for d in order.order_discounts
        if d.source == DiscountSourceEnum.PROMOTION.value and d.reference_id
    ]
    if not rows:
        return None, Decimal("0")
    return rows[0].reference_id, money(
        sum((Decimal(str(d.amount)) for d in rows), Decimal("0"))
    )


def compare(order: Order, sale: CounterSaleRequest) -> list[dict[str, str]]:
    """Every figure the receipt and the server's re-price disagree on."""
    diffs: list[dict[str, str]] = []

    def check(fieldname: str, server: Any, client: Any) -> None:
        if money(server) != money(client):
            diffs.append(
                {
                    "field": fieldname,
                    "server": fmt_money(server),
                    "client": fmt_money(client),
                }
            )

    t = sale.totals
    check("subtotal", order.subtotal, t.subtotal)
    check("discount_total", order.discount_amount, t.discount_total)
    check("tax_total", order.vat_amount, t.tax_total)
    check("total_excl_tax", order.total_excl_vat, t.total_excl_tax)
    check("rounding", order.rounding_amount, t.rounding)
    check("total", order.total, t.total)

    promo_id, promo_amount = _server_promotion(order)
    if promo_id != t.promotion_id:
        diffs.append(
            {
                "field": "promotion_id",
                "server": str(promo_id) if promo_id else "—",
                "client": str(t.promotion_id) if t.promotion_id else "—",
            }
        )
    check("promotion_amount", promo_amount, t.promotion_amount)

    lines = {line.id: line for line in sale.lines}
    for item in _live(order):
        line = lines.get(item.id)
        if line is None or line.totals is None:
            continue
        lt = line.totals
        prefix = f"lines[{item.id}]"
        check(f"{prefix}.base_price", item.base_price, lt.base_price)
        check(f"{prefix}.options_price", item.options_price, lt.options_price)
        check(f"{prefix}.unit_price", item.unit_price, lt.unit_price)
        check(
            f"{prefix}.gross",
            money(Decimal(str(item.unit_price)) * _billable(item)),
            lt.gross,
        )
        check(f"{prefix}.discount", item.discount_amount, lt.discount)
        check(f"{prefix}.total_price", item.total_price, lt.total_price)
        check(f"{prefix}.tax_amount", item.tax_amount, lt.tax_amount)
        check(
            f"{prefix}.tax_exclusive_total",
            item.tax_exclusive_total_price,
            lt.tax_exclusive_total,
        )
        check(
            f"{prefix}.tax_exclusive_unit",
            item.tax_exclusive_unit_price,
            lt.tax_exclusive_unit,
        )

    # A bucket that taxed nothing (a zero-priced line at 5%) is noise on
    # either side; compare what was actually charged.
    server_taxes = sorted(
        (
            (str(tx.tax_id) if tx.tax_id else None, money(tx.amount))
            for tx in order.order_taxes
            if money(tx.amount) != 0
        ),
        key=lambda pair: (pair[0] or "", pair[1]),
    )
    client_taxes = sorted(
        ((tx.tax_id, money(tx.amount)) for tx in t.taxes if money(tx.amount) != 0),
        key=lambda pair: (pair[0] or "", pair[1]),
    )
    if server_taxes != client_taxes:
        diffs.append(
            {
                "field": "taxes",
                "server": "; ".join(f"{k or '—'}={v}" for k, v in server_taxes) or "—",
                "client": "; ".join(f"{k or '—'}={v}" for k, v in client_taxes) or "—",
            }
        )
    return diffs


def _server_figures(order: Order) -> dict[str, Any]:
    promo_id, promo_amount = _server_promotion(order)
    return {
        "subtotal": fmt_money(order.subtotal),
        "discount_total": fmt_money(order.discount_amount),
        "tax_total": fmt_money(order.vat_amount),
        "total_excl_tax": fmt_money(order.total_excl_vat),
        "rounding": fmt_money(order.rounding_amount),
        "total": fmt_money(order.total),
        "promotion_id": str(promo_id) if promo_id else None,
        "promotion_amount": fmt_money(promo_amount),
        "taxes": [
            {
                "tax_id": str(tx.tax_id) if tx.tax_id else None,
                "name": tx.name,
                "rate": counter_pricing.fmt_rate(tx.rate),
                "taxable_amount": fmt_money(tx.taxable_amount),
                "amount": fmt_money(tx.amount),
            }
            for tx in order.order_taxes
        ],
        "lines": {
            str(item.id): {
                "base_price": fmt_money(item.base_price),
                "options_price": fmt_money(item.options_price),
                "unit_price": fmt_money(item.unit_price),
                "discount": fmt_money(item.discount_amount),
                "total_price": fmt_money(item.total_price),
                "tax_amount": fmt_money(item.tax_amount),
                "tax_exclusive_total": fmt_money(item.tax_exclusive_total_price),
                "tax_exclusive_unit": fmt_money(item.tax_exclusive_unit_price),
            }
            for item in _live(order)
        },
    }


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def _adopt_invoiced_figures(
    db: AsyncSession,
    *,
    order: Order,
    sale: CounterSaleRequest,
    bundle_payload: dict,
) -> None:
    """Book the receipt's figures. The printed receipt is a tax invoice, so
    the books must match the paper; the server's own figures are already in
    `pricing_audit`. Writes the order, its lines, its tax breakdown and its
    promotion rows — never the tenders, which are what the customer paid."""
    t = sale.totals
    order.subtotal = money(t.subtotal)
    order.discount_amount = money(t.discount_total)
    order.vat_amount = money(t.tax_total)
    order.total_excl_vat = money(t.total_excl_tax)
    order.rounding_amount = money(t.rounding)
    order.total = money(t.total)

    lines = {line.id: line for line in sale.lines}
    for item in _live(order):
        line = lines.get(item.id)
        if line is None or line.totals is None:
            continue
        lt = line.totals
        item.base_price = money(lt.base_price)
        item.options_price = money(lt.options_price)
        item.unit_price = money(lt.unit_price)
        item.discount_amount = money(lt.discount)
        item.total_price = money(lt.total_price)
        item.tax_amount = money(lt.tax_amount)
        item.tax_exclusive_total_price = money(lt.tax_exclusive_total)
        item.tax_exclusive_unit_price = money(lt.tax_exclusive_unit)

    for existing in list(order.order_taxes):
        await db.delete(existing)
    await db.flush()
    taxes = [tx for tx in t.taxes if money(tx.amount) != 0 or tx.rate > 0]
    for tx in taxes:
        db.add(
            OrderTax(
                order_id=order.id,
                tax_id=_uuid_or_none(tx.tax_id),
                name=tx.name,
                rate=tx.rate,
                taxable_amount=money(tx.taxable_amount),
                amount=money(tx.amount),
            )
        )
    if not taxes:
        order.vat_rate = Decimal("0")
    elif len(taxes) == 1:
        order.vat_rate = taxes[0].rate
    elif order.total_excl_vat and Decimal(str(order.total_excl_vat)) > 0:
        order.vat_rate = (
            Decimal(str(order.vat_amount)) / Decimal(str(order.total_excl_vat))
        ).quantize(Decimal("0.0001"))

    # The promotion rows: the receipt's promotion, on the lines the receipt
    # discounted (or one order-level row), at the amounts it printed.
    for row in [
        d
        for d in order.order_discounts
        if d.source == DiscountSourceEnum.PROMOTION.value and d.reference_id
    ]:
        order.order_discounts.remove(row)
    if t.promotion_id is not None:
        promo = next(
            (
                p
                for p in bundle_payload.get("promotions", [])
                if str(p.get("id")) == str(t.promotion_id)
            ),
            None,
        )
        name = (promo or {}).get("name") or "Promotion"
        is_percentage = (promo or {}).get("reward") == "percentage_off_order"
        raw = Decimal(str((promo or {}).get("reward_value") or "0"))
        value = (
            (raw / Decimal("100")).quantize(Decimal("0.0001")) if is_percentage else raw
        )
        discounted = [
            (item, lines[item.id].totals.discount)  # type: ignore[union-attr]
            for item in _live(order)
            if item.id in lines
            and lines[item.id].totals is not None
            and money(lines[item.id].totals.discount) > 0  # type: ignore[union-attr]
        ]
        if discounted:
            for item, amount in discounted:
                order.order_discounts.append(
                    OrderDiscount(
                        order_item_id=item.id,
                        source=DiscountSourceEnum.PROMOTION.value,
                        name=name,
                        reference_id=t.promotion_id,
                        is_percentage=is_percentage,
                        value=value,
                        amount=money(amount),
                    )
                )
        elif money(t.promotion_amount) > 0:
            order.order_discounts.append(
                OrderDiscount(
                    order_item_id=None,
                    source=DiscountSourceEnum.PROMOTION.value,
                    name=name,
                    reference_id=t.promotion_id,
                    is_percentage=is_percentage,
                    value=value,
                    amount=money(t.promotion_amount),
                )
            )
    await db.flush()


# ─── Booking ──────────────────────────────────────────────────────────────────


def _clamp(value: datetime, low: datetime, high: datetime) -> datetime:
    return max(low, min(value, high))


async def _pricing_context(
    db: AsyncSession, *, sale: CounterSaleRequest, branch: Branch, flags: list[str]
) -> tuple[counter_pricing.PricingContext, dict, bool]:
    """The inputs to re-price with: the bundle the sale cites when the server
    has it (and can run its engine version), else current data."""
    if sale.engine_version not in counter_pricing.SUPPORTED_ENGINE_VERSIONS:
        flags.append(FLAG_UNSUPPORTED_ENGINE)
    else:
        stored = await counter_bundle_service.stored(db, sale.bundle_hash)
        if (
            stored is not None
            and stored.branch_id == branch.id
            and stored.engine_version == sale.engine_version
        ):
            return (
                counter_bundle_service.context_from_payload(stored.payload),
                stored.payload,
                True,
            )
        flags.append(FLAG_UNKNOWN_BUNDLE)
    payload = counter_bundle_service.body_payload(
        await counter_bundle_service.build_body(db, branch)
    )
    return counter_bundle_service.context_from_payload(payload), payload, False


async def ingest(
    db: AsyncSession, *, device: Device, sale: CounterSaleRequest
) -> IngestResult:
    """Book one synced counter sale. See the module docstring.

    Raises `SaleConflict` for the 409 case; everything else is an
    `IngestResult` (201 ingested / 200 replayed / 202 quarantined).
    """
    sha = fingerprint(sale)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": _SALE_LOCK_NS, "key": sale.id.int & 0x7FFF_FFFF},
    )
    replay = await _replay(db, sale, sha)
    if replay is not None:
        return replay

    try:
        checked = await _structural_check(db, device=device, sale=sale)
    except Quarantine as exc:
        return await _quarantine(db, device=device, sale=sale, sha=sha, error=exc.error)

    flags = checked.flags
    branch, till, cashier = checked.branch, checked.till, checked.cashier

    problem = await attestation_problem(
        db,
        token=sale.staff_attestation,
        cashier_id=cashier.id,
        branch_id=branch.id,
        opened_at=sale.opened_at,
    )
    if problem is not None:
        flags.append(FLAG_ATTESTATION_INVALID)
    if till.user_id != cashier.id:
        flags.append(FLAG_CASHIER_NOT_TILL_OWNER)

    opened_at, closed_at = sale.opened_at, sale.closed_at
    if opened_at > closed_at:
        flags.append(FLAG_TIMESTAMPS_REORDERED)
        opened_at = closed_at
    priced_at = _clamp(sale.priced_at, opened_at, closed_at)

    tz = await business_day_service.resolve_timezone(db)
    if (
        business_day_service.business_date_for(branch, closed_at, tz)
        != sale.business_date
    ):
        flags.append(FLAG_BUSINESS_DATE_DISAGREES)

    ctx, bundle_payload, known_bundle = await _pricing_context(
        db, sale=sale, branch=branch, flags=flags
    )

    order = await pos_order_service.open_order(
        db,
        branch=branch,
        user=cashier,
        order_type=OrderTypeEnum.PICKUP.value,
        till=till,
        device_id=device.id,
        customer_name=sale.customer_name,
        customer_phone=sale.customer_phone,
        notes=sale.notes,
        source=OrderSourceEnum.CASHIER.value,
        business_date=sale.business_date,
        order_id=sale.id,
        client_request_id=sale.id,
        display_number=sale.display_number,
        opened_at=opened_at,
    )
    if sale.coupon_promotion_id is not None:
        if await db.get(Promotion, sale.coupon_promotion_id) is not None:
            order.applied_coupon_promotion_id = sale.coupon_promotion_id
        else:
            flags.append(FLAG_UNKNOWN_COUPON)
    order.config_bundle_hash = sale.bundle_hash
    order.priced_at = priced_at
    order.ingested_at = utcnow()
    order.ingest_payload_sha = sha
    await db.flush()

    items = await _build_lines(
        db, order=order, sale=sale, checked=checked, bundle_payload=bundle_payload
    )

    order = await pos_order_service.recalculate(db, order, at=priced_at, ctx=ctx)

    differences: list[dict[str, str]] = []
    if sale.state == "void":
        pricing_status = "verified" if known_bundle else "unverified"
    else:
        differences = compare(order, sale)
        if not differences:
            pricing_status = "verified" if known_bundle else "unverified"
        else:
            pricing_status = "mismatch" if known_bundle else "unverified"
            flags.append(FLAG_PRICING_MISMATCH)
            order.pricing_audit = {
                "engine_version": sale.engine_version,
                "bundle_hash": sale.bundle_hash,
                "bundle_known": known_bundle,
                "priced_at": priced_at.isoformat(),
                "server": _server_figures(order),
                "client": sale.totals.model_dump(mode="json")
                | {
                    "lines": {
                        str(line.id): line.totals.model_dump(mode="json")
                        for line in sale.lines
                        if line.totals is not None
                    }
                },
                "differences": differences,
            }
            server_total = fmt_money(order.total)
            await _adopt_invoiced_figures(
                db, order=order, sale=sale, bundle_payload=bundle_payload
            )
    order.pricing_status = pricing_status

    # The dockets the register printed, exactly as it printed them.
    ticket_flow_ids = await _valid_ids(
        db, KitchenFlow, {t.kitchen_flow_id for t in sale.kitchen_tickets}
    )
    for ticket in sorted(sale.kitchen_tickets, key=lambda t: t.sequence):
        ticket_items = [items[i] for i in ticket.line_ids if i in items]
        flow_id = ticket.kitchen_flow_id
        if flow_id is not None and flow_id not in ticket_flow_ids:
            flags.append(FLAG_UNKNOWN_KITCHEN_FLOW)
            flow_id = ticket_items[0].kitchen_flow_id if ticket_items else None
        await pos_order_service._record_kitchen_ticket(
            db,
            order=order,
            flow_id=flow_id,
            items=ticket_items,
            sequence=ticket.sequence,
            sent_at=ticket.sent_at,
            printed_at=ticket.printed_at,
            origin="device",
        )
    await db.flush()

    till_was_closed = till.status != TillStatusEnum.OPEN.value
    for tender in sale.tenders:
        method = checked.methods[tender.payment_method_id]
        if method.deleted_at is not None or not method.is_active:
            flags.append(FLAG_INACTIVE_PAYMENT_METHOD)
        await pos_order_service.record_payment(
            db,
            order=order,
            user=checked.tender_users.get(tender.user_id or cashier.id, cashier),
            payment_method_id=method.id,
            amount=tender.amount,
            tendered=tender.tendered,
            till=till,
            reference=tender.reference,
            idempotency_key=tender.idempotency_key,
            recorded_at=tender.taken_at,
            allow_closed_till=True,
            allow_inactive_method=True,
        )

    # Late: the till or the day closed before the sale reached us. Decided and
    # stamped before the close, so the close is the order's last write.
    day = (
        await db.execute(
            select(BranchBusinessDay).where(
                BranchBusinessDay.branch_id == branch.id,
                BranchBusinessDay.business_date == sale.business_date,
            )
        )
    ).scalar_one_or_none()
    day_was_closed = day is not None and day.closed_at is not None
    if till_was_closed:
        flags.append(FLAG_TILL_CLOSED)
    if day_was_closed:
        flags.append(FLAG_DAY_CLOSED)
    order = await pos_order_service.get_order(db, order.id)
    order.ingested_late = till_was_closed or day_was_closed
    order.ingest_flags = sorted(set(flags))
    await db.flush()

    if sale.state == "void":
        reason_id = None
        if sale.void_reason_id is not None:
            if await db.get(Reason, sale.void_reason_id) is not None:
                reason_id = sale.void_reason_id
            else:
                flags.append(FLAG_UNKNOWN_REASON)
        order = await pos_order_service._void_open_check(
            db, order=order, user=cashier, reason_id=reason_id
        )
        order.voided_at = closed_at
    else:
        order = await pos_order_service.close_order(
            db, order=order, user=cashier, closed_at=closed_at
        )

    if till_was_closed:
        await till_service.restamp_closed_till(db, till)
    if day_was_closed:
        await business_day_service.restamp_closed_day(db, branch, sale.business_date)
    await _recompute_vat_if_outside_window(db, sale.business_date)
    await db.flush()

    if differences:
        await email_service.send_counter_pricing_mismatch(
            order_number=order.order_number,
            display_number=order.display_number,
            branch_name=branch.name,
            business_date=sale.business_date,
            pricing_status=pricing_status,
            server_total=server_total,
            client_total=fmt_money(sale.totals.total),
            bundle_hash=sale.bundle_hash,
            differences=differences,
        )

    return IngestResult(201, _response_for(order, "ingested"))


async def _recompute_vat_if_outside_window(
    db: AsyncSession, business_date: str
) -> None:
    """The VAT ledger re-derives a trailing window on its own sweep; a sale
    older than that window would otherwise never reach it."""
    try:
        sold = date.fromisoformat(business_date)
    except ValueError:  # pragma: no cover — validated upstream
        return
    horizon = business_day_service.shop_today() - timedelta(
        days=settings.VAT_LEDGER_WINDOW_DAYS
    )
    if sold < horizon:
        from app.services import vat_ledger

        await vat_ledger.compute_window(db, business_date, business_date)


# ─── Shadow mode ──────────────────────────────────────────────────────────────

FLAG_SHADOW_MISMATCH = "shadow_mismatch"


async def shadow_compare(db: AsyncSession, *, device: Device, report) -> list[dict]:
    """Compare a shadow-mode register's local figures with the server check.

    In `shadow` the server path is the real one; the register prices the same
    check with its local engine alongside and reports here. A difference is
    the signal the rollout is waiting for: it is logged, kept on the order
    (`pricing_audit.shadow`, flag `shadow_mismatch` — the Counter sync console
    lists it) and emailed. The sale itself is never touched. Returns the
    differences (empty when the engines agree).
    """
    order = await pos_order_service.get_order(db, report.order_id)
    if order.branch_id != device.branch_id:
        raise LookupError("order is not at this device's branch")
    differences = compare(order, report)
    if not differences:
        return []
    logger.warning(
        "Counter shadow mismatch on %s (%s field(s)): %s",
        order.order_number,
        len(differences),
        differences[:5],
    )
    audit = dict(order.pricing_audit or {})
    audit["shadow"] = {
        "engine_version": report.engine_version,
        "bundle_hash": report.bundle_hash,
        "reported_at": utcnow().isoformat(),
        "server": _server_figures(order),
        "differences": differences,
    }
    order.pricing_audit = audit
    order.ingest_flags = sorted(set(order.ingest_flags or []) | {FLAG_SHADOW_MISMATCH})
    await db.flush()
    branch = await db.get(Branch, order.branch_id)
    await email_service.send_counter_pricing_mismatch(
        order_number=order.order_number,
        display_number=order.display_number,
        branch_name=branch.name if branch else "",
        business_date=order.business_date or "",
        pricing_status="shadow mismatch",
        server_total=fmt_money(order.total),
        client_total=fmt_money(report.totals.total),
        bundle_hash=report.bundle_hash,
        differences=differences,
    )
    return differences


# ─── Promote ──────────────────────────────────────────────────────────────────


async def _promoted_ticket(
    db: AsyncSession, *, branch: Branch, device_id: uuid.UUID | None, request
) -> tuple[str | None, str | None]:
    """The printed ticket a promoted check keeps, as `(display_number,
    business_date)` — or `(None, None)` for ordinary server numbering.

    A check fired to the kitchen before "Move to server" already has
    `T1-0042` on its docket; the receipt must say the same. Kept only when it
    is well-formed, this device's own prefix, and not already used that day —
    anything else falls back rather than refusing a live, untendered check.
    """
    prefix, seq = request.ticket_prefix, request.ticket_seq
    number, day = request.display_number, request.business_date
    if not (prefix and seq and number and day) or number != f"{prefix}-{seq:04d}":
        return None, None
    device = await db.get(Device, device_id) if device_id else None
    if (
        device is None
        or device.branch_id != branch.id
        or device.ticket_prefix != prefix
    ):
        return None, None
    taken = (
        await db.execute(
            select(Order.id).where(
                Order.branch_id == branch.id,
                Order.business_date == day,
                Order.display_number == number,
            )
        )
    ).first()
    if taken is not None:
        return None, None
    return number, day


async def promote(
    db: AsyncSession,
    *,
    user: User,
    branch: Branch,
    till: Till | None,
    device_id: uuid.UUID | None,
    request,
) -> Order:
    """Turn an untendered local check into a server open check with the same id.

    The explicit "Move to server" for what a local check cannot do (park,
    split, table, a manual discount). Interactive and online, so the ordinary
    strict rules apply: each line goes through `add_item`. Idempotent: an open
    check already on the server under this id is returned as it is.
    """
    existing = await db.get(Order, request.id)
    if existing is not None:
        if existing.branch_id != branch.id or not existing.is_pos:
            raise SaleConflict("This id belongs to another order")
        return await pos_order_service.get_order(db, existing.id)

    display_number, business_date = await _promoted_ticket(
        db, branch=branch, device_id=device_id, request=request
    )
    order = await pos_order_service.open_order(
        db,
        branch=branch,
        user=user,
        order_type=OrderTypeEnum.PICKUP.value,
        till=till,
        device_id=device_id,
        customer_name=request.customer_name,
        customer_phone=request.customer_phone,
        notes=request.notes,
        source=OrderSourceEnum.CASHIER.value,
        order_id=request.id,
        client_request_id=request.id,
        opened_at=request.opened_at,
        business_date=business_date,
        display_number=display_number,
    )
    by_line: dict[uuid.UUID, OrderItem] = {}
    for line in request.lines:
        item = await pos_order_service.add_item(
            db,
            order=order,
            user=user,
            product_id=line.product_id,
            quantity=line.quantity,
            unit_price_override=line.unit_price,
            selected_options=[
                {
                    "modifier_option_id": str(o.modifier_option_id),
                    "quantity": o.quantity,
                }
                for o in line.options
            ],
            kitchen_notes=line.kitchen_notes,
            weight=line.weight,
        )
        by_line[line.id] = item
        if line.sent_to_kitchen_at is not None:
            item.sent_to_kitchen_at = line.sent_to_kitchen_at
    for ticket in sorted(request.kitchen_tickets, key=lambda t: t.sequence):
        ticket_items = [by_line[i] for i in ticket.line_ids if i in by_line]
        if not ticket_items:
            continue
        await pos_order_service._record_kitchen_ticket(
            db,
            order=order,
            flow_id=ticket_items[0].kitchen_flow_id,
            items=ticket_items,
            sequence=ticket.sequence,
            sent_at=ticket.sent_at,
            printed_at=ticket.printed_at,
            origin="device",
        )
    await db.flush()
    order = await pos_order_service.get_order(db, order.id)
    if request.coupon_promotion_id is not None:
        order = await pos_order_service.set_coupon(
            db, order=order, promotion_id=request.coupon_promotion_id
        )
    else:
        order = await pos_order_service.recalculate(db, order)
    return order


__all__ = [
    "ATTESTATION_WINDOW",
    "IngestResult",
    "Quarantine",
    "SaleConflict",
    "attestation_problem",
    "compare",
    "fingerprint",
    "ingest",
    "promote",
    "shadow_compare",
]
