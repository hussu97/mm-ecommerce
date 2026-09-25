"""
The local-first counter's config bundle: every pricing input, published and hashed.

A register running local-first (`branches.counter_local_first`) prices a counter
sale by itself — so the server has to hand it the inputs, and has to be able to
re-price the sale later with *exactly* those inputs whatever has been edited
since. This module builds that bundle for one branch:

* the **hashed body** (`CounterBundleBody`): engine version, currency, time
  zone, rounding step, the branch and its business-day cut-off, the counter's
  legal entity (VAT registration and receipt identity), tax groups, the POS
  products with their price / pricing method / weight / non-revenue flag / tax
  group / category / pre-routed kitchen station, modifier links and options,
  the menu tree, payment methods, the branch's counter promotions with their
  mode, void reasons and kitchen stations;
* the **unhashed envelope** (`CounterBundleEnvelope`): the 86 list, server
  time, the device's ticket prefix, the last ticket sequence ingested today,
  and the mode this particular terminal must run.

The body's sha256 over canonical JSON is its identity. It is persisted to
`pos_config_bundles` the first time it is served, and `context_from_payload`
turns a stored body back into a `counter_pricing.PricingContext` for ingest.

Nothing here writes money; it only reads the catalogue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.pos_builds import COUNTER_LOCAL_FIRST_MIN_BUILD, build_at_least
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.business_settings import BusinessSettings
from app.models.device import Device
from app.models.kitchen_flow import KitchenFlow
from app.models.marketing import Promotion
from app.models.modifier import Modifier, ProductModifier
from app.models.order import Order
from app.models.payment_method import PaymentMethod
from app.models.pos_counter import PosConfigBundle
from app.models.product import Product
from app.models.reason import Reason, ReasonTypeEnum
from app.models.tax import TaxGroup
from app.schemas.menu_group import MenuGroupNode
from app.schemas.pos.payment_methods import PaymentMethodResponse
from app.schemas.pos.reasons import ReasonResponse
from app.schemas.pos_counter import (
    BundleBranch,
    BundleEntity,
    BundleKitchenFlow,
    BundleModifier,
    BundleModifierOption,
    BundleProduct,
    BundleProductModifier,
    BundlePromotion,
    BundleResolvedTax,
    BundleTax,
    BundleTaxGroup,
    CounterAvailability,
    CounterBundleBody,
    CounterBundleEnvelope,
)
from app.services.catalog import availability_service, menu_group_service
from app.services.orders import tax_identity_service
from app.services.pos import (
    auto_promotion_service,
    business_day_service,
    counter_pricing,
    promotion_rules,
)
from app.services.pos.counter_pricing import fmt_money, fmt_rate

logger = logging.getLogger(__name__)

#: A bundle nobody has been served for this long is pruned. A sale citing a
#: pruned bundle is re-priced from current data and booked `unverified`.
BUNDLE_RETENTION = timedelta(days=120)

#: `last_served_at` is refreshed at most this often per bundle, so a terminal
#: polling every minute does not write a row a minute.
_SERVED_REFRESH = timedelta(hours=1)

#: Serialises ticket-prefix assignment per branch. Arbitrary, fixed; the
#: two-int form so it cannot collide with the 64-bit keys elsewhere.
_PREFIX_LOCK_NS = 0x4D4D_5446

# ─── Canonical hashing ────────────────────────────────────────────────────────


def canonical_json(data: Any) -> str:
    """Keys sorted, no whitespace, UTF-8 — the form every hash is taken over."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


# ─── Building the body ────────────────────────────────────────────────────────


async def _settings(db: AsyncSession) -> BusinessSettings:
    existing = (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
    return existing or BusinessSettings()


def _route(flows: list[KitchenFlow], category_id: uuid.UUID | None) -> uuid.UUID | None:
    """`pos_order_service._route_to_kitchen_flow` for a pickup order, on
    pre-loaded flows."""
    default_flow: KitchenFlow | None = None
    for flow in flows:
        if flow.is_default:
            default_flow = flow
        if flow.order_types and counter_pricing.COUNTER_ORDER_TYPE not in (
            flow.order_types
        ):
            continue
        category_ids = {link.category_id for link in flow.categories}
        if category_id and category_id in category_ids:
            return flow.id
    return default_flow.id if default_flow else None


def _promotion(promo: Promotion, mode: str, rank: int) -> BundlePromotion:
    rule = promotion_rules.rule_from(promo)
    return BundlePromotion(
        id=promo.id,
        name=promo.name,
        reward=promo.reward,
        reward_value=f"{Decimal(str(promo.reward_value or 0)):.4f}",
        trigger=promo.trigger or "spend",
        trigger_value=fmt_money(Decimal(str(promo.trigger_value or 0))),
        category_ids=sorted(rule.category_ids, key=str),
        branch_ids=sorted(rule.branch_ids, key=str),
        order_types=sorted(rule.order_types),
        sources=sorted(rule.sources),
        mode=mode,  # type: ignore[arg-type]
        rank=rank,
        is_active=rule.is_active,
        from_date=rule.from_date,
        to_date=rule.to_date,
        from_time=rule.from_time,
        to_time=rule.to_time,
        weekdays=list(rule.weekdays),
    )


async def _counter_promotions(
    db: AsyncSession, branch_id: uuid.UUID
) -> list[BundlePromotion]:
    """Every counter promotion that runs at the branch in some mode, best
    first — the rows `auto_promotion_service.available_at` lists, without its
    live-now reading (the register evaluates the schedule at sale time).

    A promotion whose usage limit is used up is left out. The bundle is rebuilt
    and re-hashed on every fetch, so the tills drop it within one refresh (60s)
    of the sale that used it up, with no change to the pricing engine."""
    rows = list(
        (
            await db.execute(
                select(Promotion).where(
                    Promotion.is_active.is_(True),
                    Promotion.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    rows = await auto_promotion_service.without_exhausted(db, rows)
    ranked: list[tuple[tuple, Promotion, str]] = []
    for promo in rows:
        rule = promotion_rules.rule_from(promo)
        mode = promotion_rules.mode_at(rule, branch_id)
        if mode is None or not promotion_rules.is_counter_shape(rule):
            continue
        if "cashier" not in rule.sources:
            continue
        if rule.branch_ids and branch_id not in rule.branch_ids:
            continue
        ranked.append(((*promotion_rules.rank(rule), str(promo.id)), promo, mode))
    ranked.sort(key=lambda row: row[0])
    return [_promotion(promo, mode, i) for i, (_, promo, mode) in enumerate(ranked)]


def _sorted_tree(nodes: list[dict]) -> list[dict]:
    """The tree with siblings in a total order, so a tie on display order
    cannot flip the hash between two reads."""
    out = []
    for node in sorted(
        nodes, key=lambda n: (n["display_order"], n["name"], str(n["id"]))
    ):
        node = dict(node)
        node["children"] = _sorted_tree(node.get("children") or [])
        out.append(node)
    return out


async def build_body(db: AsyncSession, branch: Branch) -> CounterBundleBody:
    """The hashed half of a branch's bundle, from current data."""
    settings = await _settings(db)
    tz = await business_day_service.resolve_timezone(db)

    entity = await tax_identity_service.resolve(
        db, branch_id=branch.id, source=counter_pricing.COUNTER_SOURCE
    )

    flows = list(
        (
            await db.execute(
                select(KitchenFlow)
                .where(
                    KitchenFlow.branch_id == branch.id,
                    KitchenFlow.is_active.is_(True),
                    KitchenFlow.deleted_at.is_(None),
                )
                .options(selectinload(KitchenFlow.categories))
                .order_by(KitchenFlow.display_order, KitchenFlow.id)
            )
        )
        .scalars()
        .unique()
        .all()
    )

    products = list(
        (
            await db.execute(
                select(Product)
                .where(
                    Product.is_active.is_(True),
                    menu_group_service.pos_visibility_clause(branch.id),
                )
                .order_by(Product.display_order, Product.name, Product.id)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    product_ids = [p.id for p in products]

    links: list[ProductModifier] = []
    if product_ids:
        links = list(
            (
                await db.execute(
                    select(ProductModifier)
                    .where(ProductModifier.product_id.in_(product_ids))
                    .order_by(
                        ProductModifier.product_id,
                        ProductModifier.display_order,
                        ProductModifier.id,
                    )
                )
            )
            .scalars()
            .all()
        )
    modifier_ids = sorted({link.modifier_id for link in links}, key=str)
    modifiers: list[Modifier] = []
    if modifier_ids:
        modifiers = list(
            (
                await db.execute(
                    select(Modifier)
                    .where(Modifier.id.in_(modifier_ids))
                    .options(selectinload(Modifier.options))
                )
            )
            .scalars()
            .unique()
            .all()
        )
    modifiers.sort(key=lambda m: (m.reference or "", str(m.id)))

    links_by_product: dict[uuid.UUID, list[ProductModifier]] = {}
    for link in links:
        links_by_product.setdefault(link.product_id, []).append(link)

    group_ids = sorted(
        {p.tax_group_id for p in products if p.tax_group_id is not None}, key=str
    )
    groups: list[TaxGroup] = []
    if group_ids:
        groups = list(
            (
                await db.execute(
                    select(TaxGroup)
                    .where(TaxGroup.id.in_(group_ids))
                    .options(selectinload(TaxGroup.taxes))
                )
            )
            .scalars()
            .unique()
            .all()
        )
    groups.sort(key=lambda g: str(g.id))

    def _tax_group(group: TaxGroup) -> BundleTaxGroup:
        # Same member order `_resolve_tax` reads, so "the first live tax names
        # the line" names the same tax on both sides.
        rows = [
            counter_pricing.TaxRow(
                id=str(link.tax.id),
                name=link.tax.name,
                rate=Decimal(str(link.tax.rate)),
                type=link.tax.type,
                is_active=bool(link.tax.is_active),
            )
            for link in group.taxes
        ]
        resolved = counter_pricing.tax_tuple(rows)
        return BundleTaxGroup(
            id=group.id,
            name=group.name,
            taxes=[
                BundleTax(
                    id=uuid.UUID(r.id),
                    name=r.name,
                    rate=fmt_rate(r.rate),
                    type="exclusive" if r.type == "exclusive" else "inclusive",
                    is_active=r.is_active,
                )
                for r in rows
            ],
            resolved=BundleResolvedTax(
                rate=fmt_rate(resolved.rate),
                name=resolved.name,
                tax_id=resolved.tax_id,
                inclusive=resolved.inclusive,
            ),
        )

    methods = list(
        (
            await db.execute(
                select(PaymentMethod)
                .where(
                    PaymentMethod.is_active.is_(True),
                    PaymentMethod.deleted_at.is_(None),
                )
                .order_by(
                    PaymentMethod.display_order, PaymentMethod.name, PaymentMethod.id
                )
            )
        )
        .scalars()
        .all()
    )
    reasons = list(
        (
            await db.execute(
                select(Reason)
                .where(
                    Reason.type == ReasonTypeEnum.VOID_RETURN.value,
                    Reason.is_active.is_(True),
                    Reason.deleted_at.is_(None),
                )
                .order_by(Reason.name, Reason.id)
            )
        )
        .scalars()
        .all()
    )
    tree = await menu_group_service.list_tree(db, branch_id=branch.id)

    return CounterBundleBody(
        engine_version=counter_pricing.ENGINE_VERSION,
        currency_code=settings.currency_code or "AED",
        currency_symbol=settings.currency_symbol or "AED",
        timezone=tz.key,
        rounding_step=f"{Decimal(str(settings.cash_rounding_step or 0)):.3f}",
        branch=BundleBranch(
            id=branch.id,
            name=branch.name,
            reference=branch.reference,
            business_day_start=branch.business_day_start,
            cash_enabled=bool(branch.cash_enabled),
            receipt_header=branch.receipt_header,
            receipt_footer=branch.receipt_footer,
        ),
        entity=BundleEntity(
            id=entity.id if entity is not None else None,
            reference=entity.reference if entity is not None else None,
            legal_name=entity.legal_name if entity is not None else None,
            brand_name=entity.brand_name if entity is not None else None,
            vat_registered=tax_identity_service.is_vat_registered(entity),
            tax_number=entity.tax_number if entity is not None else None,
            invoice_title=entity.invoice_title if entity is not None else None,
            logo_url=entity.logo_url if entity is not None else None,
        ),
        tax_groups=[_tax_group(g) for g in groups],
        products=[
            BundleProduct(
                id=p.id,
                name=p.name,
                name_localized=p.name_localized,
                translations=p.translations or {},
                sku=p.sku,
                category_id=p.category_id,
                base_price=fmt_money(Decimal(str(p.base_price or 0))),
                pricing_method=p.pricing_method or "fixed",
                is_sold_by_weight=bool(p.is_sold_by_weight),
                is_non_revenue=bool(p.is_non_revenue),
                tax_group_id=p.tax_group_id,
                kitchen_flow_id=_route(flows, p.category_id),
                image_urls=list(p.image_urls or []),
                display_order=p.display_order or 0,
                modifiers=[
                    BundleProductModifier(
                        id=link.id,
                        modifier_id=link.modifier_id,
                        minimum_options=link.minimum_options,
                        maximum_options=link.maximum_options,
                        free_options=link.free_options,
                        unique_options=bool(link.unique_options),
                        display_order=link.display_order,
                    )
                    for link in links_by_product.get(p.id, [])
                ],
            )
            for p in products
        ],
        modifiers=[
            BundleModifier(
                id=m.id,
                reference=m.reference,
                name=m.name,
                translations=m.translations or {},
                is_active=bool(m.is_active),
                options=[
                    BundleModifierOption(
                        id=o.id,
                        name=o.name,
                        translations=o.translations or {},
                        sku=o.sku,
                        price=fmt_money(Decimal(str(o.price or 0))),
                        is_active=bool(o.is_active),
                        display_order=o.display_order,
                    )
                    for o in sorted(
                        m.options, key=lambda o: (o.display_order, o.name, str(o.id))
                    )
                ],
            )
            for m in modifiers
        ],
        menu_tree=[MenuGroupNode.model_validate(n) for n in _sorted_tree(tree)],
        payment_methods=[PaymentMethodResponse.model_validate(m) for m in methods],
        promotions=await _counter_promotions(db, branch.id),
        void_reasons=[ReasonResponse.model_validate(r) for r in reasons],
        kitchen_flows=[
            BundleKitchenFlow(id=f.id, name=f.name, is_default=bool(f.is_default))
            for f in flows
        ],
    )


def body_payload(body: CounterBundleBody) -> dict[str, Any]:
    """The body as it is hashed, served and stored (JSON mode)."""
    return body.model_dump(mode="json")


# ─── Persisting ───────────────────────────────────────────────────────────────


async def persist(
    db: AsyncSession, *, branch_id: uuid.UUID, bundle_hash: str, payload: dict
) -> None:
    """Record a served bundle so ingest can re-price against it.

    Idempotent (`ON CONFLICT DO NOTHING`); `last_served_at` is refreshed at
    most hourly. A brand-new bundle also prunes the long-unserved ones.
    """
    inserted = (
        await db.execute(
            pg_insert(PosConfigBundle)
            .values(
                hash=bundle_hash,
                branch_id=branch_id,
                engine_version=int(payload.get("engine_version") or 0),
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["hash"])
            .returning(PosConfigBundle.hash)
        )
    ).scalar_one_or_none()
    now = utcnow()
    if inserted is None:
        await db.execute(
            update(PosConfigBundle)
            .where(
                PosConfigBundle.hash == bundle_hash,
                PosConfigBundle.last_served_at < now - _SERVED_REFRESH,
            )
            .values(last_served_at=now)
        )
        return
    await prune(db, now=now)


async def prune(db: AsyncSession, *, now=None) -> int:
    """Drop bundles unserved for `BUNDLE_RETENTION`. Returns how many."""
    cutoff = (now or utcnow()) - BUNDLE_RETENTION
    result = await db.execute(
        PosConfigBundle.__table__.delete().where(
            PosConfigBundle.last_served_at < cutoff
        )
    )
    return int(result.rowcount or 0)


async def stored(db: AsyncSession, bundle_hash: str) -> PosConfigBundle | None:
    return await db.get(PosConfigBundle, bundle_hash)


# ─── The pricing context ──────────────────────────────────────────────────────


def context_from_payload(payload: dict[str, Any]) -> counter_pricing.PricingContext:
    """A stored (or freshly built) bundle body as the engine's inputs."""
    branch_id = uuid.UUID(str(payload["branch"]["id"]))
    tax_groups = {
        uuid.UUID(str(g["id"])): counter_pricing.TaxTuple(
            rate=Decimal(str(g["resolved"]["rate"])),
            name=str(g["resolved"]["name"]),
            tax_id=g["resolved"].get("tax_id"),
            inclusive=bool(g["resolved"].get("inclusive", True)),
        )
        for g in payload.get("tax_groups", [])
    }
    products = {
        uuid.UUID(str(p["id"])): counter_pricing.ProductFacts(
            category_id=uuid.UUID(str(p["category_id"]))
            if p.get("category_id")
            else None,
            tax_group_id=uuid.UUID(str(p["tax_group_id"]))
            if p.get("tax_group_id")
            else None,
            is_non_revenue=bool(p.get("is_non_revenue")),
        )
        for p in payload.get("products", [])
    }
    promotions = tuple(
        counter_pricing.promo_rule_from_wire(p, branch_id)
        for p in payload.get("promotions", [])
    )
    entity = payload.get("entity") or {}
    return counter_pricing.PricingContext(
        branch_id=branch_id,
        timezone=str(payload["timezone"]),
        rounding_step=Decimal(str(payload.get("rounding_step") or "0")),
        vat_registered=bool(entity.get("vat_registered", True)),
        tax_groups=tax_groups,
        products=products,
        promotions=promotions,
        engine_version=int(payload.get("engine_version") or 0),
        legal_entity_id=uuid.UUID(str(entity["id"])) if entity.get("id") else None,
    )


# ─── Ticket prefix and sequence ───────────────────────────────────────────────


async def ensure_ticket_prefix(db: AsyncSession, device: Device) -> str:
    """This device's ticket prefix, assigning the next free `T{n}` at its
    branch the first time. Serialised per branch on an advisory lock, and the
    partial unique index is the backstop — a loser re-reads and retries."""
    if device.ticket_prefix:
        return device.ticket_prefix
    for attempt in range(3):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :branch)"),
            {"ns": _PREFIX_LOCK_NS, "branch": device.branch_id.int & 0x7FFF_FFFF},
        )
        taken = set(
            (
                await db.execute(
                    select(Device.ticket_prefix).where(
                        Device.branch_id == device.branch_id,
                        Device.ticket_prefix.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        n = 1
        while f"T{n}" in taken:
            n += 1
        try:
            async with db.begin_nested():
                device.ticket_prefix = f"T{n}"
                await db.flush()
            return device.ticket_prefix
        except IntegrityError:
            device.ticket_prefix = None
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover


def parse_display_number(display_number: str | None) -> tuple[str, int] | None:
    """`T1-0042` → `("T1", 42)`; None when it is not that shape."""
    if not display_number or "-" not in display_number:
        return None
    prefix, _, seq = display_number.rpartition("-")
    if not prefix or not seq.isdigit():
        return None
    return prefix, int(seq)


async def last_ingested_seq(
    db: AsyncSession, *, branch_id: uuid.UUID, business_date: str, prefix: str
) -> int:
    """The highest ticket sequence ingested for `prefix` on `business_date`."""
    rows = (
        (
            await db.execute(
                select(Order.display_number).where(
                    Order.branch_id == branch_id,
                    Order.business_date == business_date,
                    Order.display_number.like(f"{prefix}-%"),
                )
            )
        )
        .scalars()
        .all()
    )
    best = 0
    for value in rows:
        parsed = parse_display_number(value)
        if parsed and parsed[0] == prefix:
            best = max(best, parsed[1])
    return best


# ─── The whole response ───────────────────────────────────────────────────────


def effective_mode(branch_flag: str, build_number: str | None) -> str:
    """The mode a terminal must run: the branch flag, or `off` below the
    minimum build (and for a build that reports none)."""
    if not build_at_least(build_number, COUNTER_LOCAL_FIRST_MIN_BUILD):
        return "off"
    return branch_flag if branch_flag in ("off", "shadow", "on") else "off"


@dataclass
class ServedBundle:
    hash: str
    body: dict[str, Any]
    envelope: CounterBundleEnvelope
    etag: str
    extra_headers: dict[str, str] = field(default_factory=dict)


async def availability(db: AsyncSession, branch_id: uuid.UUID) -> CounterAvailability:
    products = (
        (await db.execute(availability_service.unavailable_product_ids_at(branch_id)))
        .scalars()
        .all()
    )
    options = (
        (await db.execute(availability_service.unavailable_option_ids_at(branch_id)))
        .scalars()
        .all()
    )
    return CounterAvailability(
        unavailable_product_ids=sorted(set(products), key=str),
        unavailable_option_ids=sorted(set(options), key=str),
    )


async def serve(
    db: AsyncSession, *, device: Device, build_number: str | None
) -> ServedBundle:
    """Build, hash, persist and wrap this device's branch bundle."""
    branch = await db.get(Branch, device.branch_id)
    if branch is None:  # pragma: no cover — FK
        raise LookupError("device has no branch")
    body = await build_body(db, branch)
    payload = body_payload(body)
    bundle_hash = sha256_hex(payload)
    await persist(db, branch_id=branch.id, bundle_hash=bundle_hash, payload=payload)

    prefix = await ensure_ticket_prefix(db, device)
    business_date = await business_day_service.current_business_date(db, branch)
    branch_flag = getattr(branch, "counter_local_first", None) or "off"
    envelope = CounterBundleEnvelope(
        counter_local_first=effective_mode(branch_flag, build_number),  # type: ignore[arg-type]
        branch_counter_local_first=branch_flag,  # type: ignore[arg-type]
        pricing_engine_version=counter_pricing.ENGINE_VERSION,
        supported_engine_versions=sorted(counter_pricing.SUPPORTED_ENGINE_VERSIONS),
        min_build=COUNTER_LOCAL_FIRST_MIN_BUILD,
        build_supported=build_at_least(build_number, COUNTER_LOCAL_FIRST_MIN_BUILD),
        server_time=utcnow(),
        business_date=business_date,
        ticket_prefix=prefix,
        last_ingested_ticket_seq=await last_ingested_seq(
            db, branch_id=branch.id, business_date=business_date, prefix=prefix
        ),
        availability=await availability(db, branch.id),
    )
    # What the terminal must re-fetch for, beyond the bundle itself: the 86
    # list, its mode and its prefix. `server_time` and the sequence are left
    # out on purpose — the clock comes from the `Date` header and the device's
    # own numbering is authoritative between reinstalls.
    envelope_tag = sha256_hex(
        {
            "mode": envelope.counter_local_first,
            "branch_mode": envelope.branch_counter_local_first,
            "prefix": envelope.ticket_prefix,
            "min_build": envelope.min_build,
            "business_date": envelope.business_date,
            "availability": envelope.availability.model_dump(mode="json"),
        }
    )[:16]
    return ServedBundle(
        hash=bundle_hash,
        body=payload,
        envelope=envelope,
        etag=f'"{bundle_hash}.{envelope_tag}"',
    )


async def current_context(
    db: AsyncSession, branch: Branch
) -> tuple[str, counter_pricing.PricingContext]:
    """The branch's bundle from current data — for a sale whose bundle is
    unknown — as `(hash, context)`."""
    payload = body_payload(await build_body(db, branch))
    return sha256_hex(payload), context_from_payload(payload)


__all__ = [
    "BUNDLE_RETENTION",
    "ServedBundle",
    "availability",
    "body_payload",
    "build_body",
    "canonical_json",
    "context_from_payload",
    "current_context",
    "effective_mode",
    "ensure_ticket_prefix",
    "last_ingested_seq",
    "parse_display_number",
    "persist",
    "prune",
    "serve",
    "sha256_hex",
    "stored",
]
