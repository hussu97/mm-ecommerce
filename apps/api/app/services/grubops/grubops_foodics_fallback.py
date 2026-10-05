"""Build a GrubOps order from Foodics when GrubOps lists it but won't serve it.

GrubOps sometimes lists an order in `getOrderSummaryList` while `getOrderInfo`
answers 404 for minutes, or for ever (Talabat 3937792428 at Barsha, 2026-10-05:
listed at 19:25, still 404 three hours later, never reached the register). The
order is not lost: GrubTech publishes every aggregator order to **Foodics**, the
POS behind it, within a second of listing it. So when GrubOps keeps the detail
back, this reads the same order from Foodics and hands the ingest a payload in
GrubOps' own shape. Everything downstream is then the unchanged GrubOps path:
the order is created (or adopts a promotion gap-fill), rung on the register,
takes its stock, and caches the Foodics id that Packed / Cancel write back to.

What the two systems are known to agree on, measured on 3,218 production orders
before this was written:

* A GrubOps line's `externalId` IS the Foodics product / modifier-option id, one
  to one with no conflicts. Foodics ids are translated back to GrubOps recipe and
  modifier ids through the orders GrubOps did serve, so the approved
  `external_item_map` (system `grubops`) resolves them exactly as it resolves a
  served order's lines. A Foodics item GrubOps has never served falls back to a
  unique exact-name match on that approved map, and otherwise stays an unmapped
  line, the way an unmapped GrubOps line does.
* The sum of the Foodics product totals less the Foodics discount equals GrubOps'
  `totalPrice`, discounted Noon orders included. Foodics' own `total_price` adds
  the marketplace delivery charge, which MM never books, so it is not used.
* Foodics' `customer` is a GrubTech placeholder (the same name on unrelated
  orders, "Grubtech Test" on others). The customer, payment method and status
  come from the GrubOps summary instead, which carries all three.

The fallback refuses, leaving the order to wait exactly as it did before, rather
than file something it cannot stand behind: no single Foodics order on the
mapped branch with the same channel and external id within half an hour of the
listing; a Foodics paid amount that disagrees with the lines; or no live lines.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, select, text

from app.core.money import money
from app.models.aggregator import FoodicsBranchMap
from app.models.external_item_map import KIND_OPTION, KIND_PRODUCT, ExternalItemMap
from app.models.grubops import GrubOpsLocationMap
from app.models.grubops_order import GrubOpsOrderMap
from app.services.aggregators import reconcile
from app.services.foodics import foodics_orders_service
from app.services.grubops import grubops_orders_service
from app.services.providers.foodics_provider import PRODUCT_VOID, FoodicsError
from app.services.providers.foodics_provider import provider as foodics

logger = logging.getLogger(__name__)

__all__ = [
    "FALLBACK_KEY",
    "build_from_foodics",
    "build_info",
    "find_match",
    "is_fallback_raw",
    "parse_external_number",
    "with_status",
]

#: How long GrubOps gets to serve the detail itself before Foodics is asked. It
#: normally answers on the first tick; the wait keeps the ordinary path the only
#: path in the ordinary case, and still puts a stuck order on the register about
#: two minutes after it was placed.
FALLBACK_GRACE = timedelta(seconds=90)

#: Past this the order is promotion's to recover (the same 12 h adopt grace,
#: `AGGREGATOR_GRUBOPS_ADOPT_GRACE_HOURS`), and Foodics' newest pages no longer
#: reach back to it.
FALLBACK_MAX_AGE = timedelta(hours=12)

#: A Foodics order further than this from the GrubOps listing is not the same
#: order. Noon's short external ids repeat per branch per day, so the window is
#: what keeps one evening's "4158" from matching another's.
MATCH_WINDOW = timedelta(minutes=30)

#: Foodics lists 30 orders a page, newest first. Three pages is several busy
#: hours at one branch; the order sought is minutes old.
_MAX_PAGES = 3

#: The key on a built payload (and so on `grubops_order_map.raw`) that says it
#: came from Foodics, not from `getOrderInfo`.
FALLBACK_KEY = "_fallback"

#: "Melting Moments - Talabat: 3938122835, #2665" → ("3938122835", "2665"). The
#: `#` number is Talabat's short code, the one on the rider's screen.
_EXTERNAL_NUMBER_RE = re.compile(r":\s*([^\s,#]+)\s*(?:,\s*#\s*(\d{2,6}))?\s*$")

#: Problems already logged, so a refusal retried every tick is said once.
_reported: set[tuple[str, str]] = set()


def is_fallback_raw(raw: Any) -> bool:
    """Whether a stored `grubops_order_map.raw` was built here from Foodics."""
    return isinstance(raw, dict) and isinstance(raw.get(FALLBACK_KEY), dict)


def with_status(raw: dict, status: str | None) -> dict:
    """A stored fallback payload carrying the summary's newer status, so the
    ordinary ingest moves the order while GrubOps still withholds the detail."""
    info = dict(raw)
    info["orderHeader"] = {**(raw.get("orderHeader") or {}), "orderStatus": status}
    return info


def parse_external_number(meta: Any) -> tuple[str | None, str | None]:
    """The marketplace order id and (Talabat) short code from a Foodics `meta`."""
    if not isinstance(meta, dict):
        return None, None
    match = _EXTERNAL_NUMBER_RE.search(str(meta.get("external_number") or ""))
    if match is None:
        return None, None
    return match.group(1), match.group(2)


def _foodics_ts(value: Any) -> datetime | None:
    """A Foodics timestamp (`YYYY-MM-DD HH:MM:SS`, UTC, no offset)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _summary_channel(summary: dict) -> str | None:
    return (summary.get("source") or {}).get("channel")


def find_match(
    summary: dict, orders: list[dict], *, foodics_branch_id: str
) -> tuple[dict | None, str]:
    """The one Foodics order that is this GrubOps listing, or None and why.

    All of: the Foodics branch mapped to the listing's location, the same
    channel (canonicalised, so "Keeta 2.0" is "Keeta"), the identical external
    id, and placed within `MATCH_WINDOW` of the listing. Two candidates is a
    refusal, never a guess.
    """
    external_id = str(summary.get("externalId") or "").strip()
    channel = reconcile.canonical_channel_code(_summary_channel(summary))
    listed = grubops_orders_service._parse_ts(summary.get("createdAt"))
    if not external_id or not channel or listed is None:
        return None, "the GrubOps listing lacks an external id, channel or time"

    candidates = []
    for order in orders:
        branch = order.get("branch")
        if not isinstance(branch, dict) or branch.get("id") != foodics_branch_id:
            continue
        meta = order.get("meta")
        source = meta.get("external_source") if isinstance(meta, dict) else None
        if reconcile.canonical_channel_code(source) != channel:
            continue
        if parse_external_number(meta)[0] != external_id:
            continue
        placed = _foodics_ts(order.get("created_at"))
        if placed is None or abs(placed - listed) > MATCH_WINDOW:
            continue
        candidates.append(order)

    if not candidates:
        return None, "no Foodics order matches"
    if len(candidates) > 1:
        return None, f"{len(candidates)} Foodics orders match"
    return candidates[0], ""


def _live_products(order: dict) -> list[dict]:
    return [
        p
        for p in order.get("products") or []
        if isinstance(p, dict) and p.get("status") != PRODUCT_VOID
    ]


def _dec(value: Any) -> Decimal:
    return grubops_orders_service._num(value)


def order_money(order: dict) -> tuple[Decimal, Decimal, Decimal]:
    """(gross, discount, total) as GrubOps would report them for this order."""
    gross = money(
        sum((_dec(p.get("total_price")) for p in _live_products(order)), Decimal("0"))
    )
    discount = money(_dec(order.get("discount_amount")))
    return gross, discount, money(gross - discount)


def money_disagreement(order: dict) -> str | None:
    """Why the Foodics figures cannot be trusted for this order, or None.

    `meta.customer_paid_amount`, when GrubTech sets it, is the marketplace's own
    item total and has always equalled lines − discount. If it ever does not,
    something about the order is not understood, and a wrong sale total is worse
    than a late one.
    """
    if not _live_products(order):
        return "the Foodics order has no live lines"
    _, _, total = order_money(order)
    meta = order.get("meta")
    paid = meta.get("customer_paid_amount") if isinstance(meta, dict) else None
    if paid not in (None, "") and money(_dec(paid)) != total:
        return f"Foodics lines total {total} but the customer paid {paid}"
    return None


def _name_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def build_info(
    summary: dict,
    order: dict,
    *,
    recipes: dict[str, str],
    modifiers: dict[tuple[str, str], str],
) -> dict:
    """A `getOrderInfo`-shaped payload for `grubops_orders_service.ingest`.

    `recipes` maps a Foodics product id to its GrubOps recipe id; `modifiers`
    maps (recipe id, Foodics option id) to its GrubOps modifier id. An id that
    is not there is written as an unmapped line, as GrubOps' own would be.
    """
    gross, discount, total = order_money(order)
    channel = _summary_channel(summary)
    external_id = str(summary.get("externalId") or "")
    _, short_code = parse_external_number(order.get("meta"))
    customer = summary.get("customer") or {}

    # Foodics prefixes the customer's note with its own external_number line.
    note_lines = str(order.get("kitchen_notes") or "").split("\n")
    note = "\n".join(
        line for line in note_lines if not line.strip().startswith("external_number")
    ).strip()
    instructions = note
    if short_code and reconcile.canonical_channel_code(channel) == "talabat":
        # Exactly how GrubOps spells it, so `_driver_code` reads the code into
        # the box and `_customer_note` keeps it out of the note.
        instructions = f"{note} | Talabat-short code: {short_code}".strip(" |")

    lines: list[dict] = []
    for product in _live_products(order):
        item = product.get("product") or {}
        foodics_id = str(item.get("id") or "")
        recipe_id = recipes.get(foodics_id)
        quantity = _dec(product.get("quantity") or 1)
        lines.append(
            {
                "type": "ITEM",
                "name": item.get("name") or "Item",
                "recipeId": recipe_id,
                "modifierId": None,
                "externalId": foodics_id or None,
                "quantity": float(quantity),
                "unitPrice": float(_dec(product.get("unit_price"))),
                "totalPrice": float(_dec(product.get("total_price"))),
                "taxAmount": 0.0,
                "instructions": product.get("kitchen_notes") or None,
            }
        )
        for option in product.get("options") or []:
            if not isinstance(option, dict):
                continue
            mod = option.get("modifier_option") or {}
            option_id = str(mod.get("id") or "")
            per_item = _dec(option.get("quantity") or 1)
            lines.append(
                {
                    "type": "MODIFIER",
                    "name": mod.get("name") or "Option",
                    "recipeId": recipe_id,
                    "modifierId": (
                        modifiers.get((recipe_id, option_id)) if recipe_id else None
                    ),
                    "externalId": option_id or None,
                    "quantity": float(per_item),
                    # Priced per item, as the ingest adds it to the item's unit
                    # price; GrubOps' own priced modifiers are always quantity 1.
                    "unitPrice": float(_dec(option.get("unit_price")) * per_item),
                    "taxAmount": 0.0,
                }
            )

    listed = summary.get("createdAt")
    foodics_id = str(order.get("id") or "")
    return {
        "orderHeader": {
            "orderId": summary.get("orderId"),
            "externalId": external_id,
            "orderStatus": summary.get("status"),
            "foodAggregatorName": channel,
            "locationId": summary.get("locationId"),
            "totalPrice": float(total),
            "grossPrice": float(gross),
            "unitPrice": float(gross),
            "subtotal": None,
            "discountAmount": float(discount),
            # Not itemised: the ingest derives the 5% from the gross, as it does
            # for a served order that reports none.
            "taxAmount": 0.0,
            "instructions": instructions or None,
            "paymentMethod": summary.get("paymentMethod"),
            "paymentStatus": None,
            "createdAt": listed,
        },
        "customer": {
            "customerName": customer.get("customerName"),
            "customerMobile": customer.get("phoneNumber"),
        },
        "orderLines": lines,
        "orderTaxes": [],
        "orderHistories": [
            {"status": "OrderCreated", "code": 1000, "timeStamp": listed},
            {
                # The publish event `_foodics_order_id` reads the id from, so the
                # ingest caches it exactly as for a served order.
                "status": "PUBLISHING_ORDER_CREATED_TO_POS_SUCCEEDED",
                "code": 20000,
                "timeStamp": listed,
                "description": (
                    f"Order External Id - {external_id} Foodics Order Id: {foodics_id}"
                ),
            },
        ],
        FALLBACK_KEY: {
            "source": "foodics",
            "foodics_order_id": foodics_id,
            "foodics_reference": order.get("reference"),
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# The orders GrubOps did serve, read for the Foodics id → GrubOps id pairs their
# lines carry. Most recent wins should a menu rebuild ever re-key an item.
_HISTORY_SQL = text(
    """
    SELECT DISTINCT ON (line->>'externalId')
           line->>'externalId' AS external_id,
           line->>'recipeId' AS recipe_id,
           line->>'modifierId' AS modifier_id
    FROM grubops_order_map AS m,
         jsonb_array_elements(m.raw->'orderLines') AS line
    WHERE m.raw IS NOT NULL
      AND m.raw->'_fallback' IS NULL
      AND line->>'externalId' IN :ids
    ORDER BY line->>'externalId', m.created_at DESC
    """
).bindparams(bindparam("ids", expanding=True))


async def _id_maps(
    db, order: dict
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Foodics product → GrubOps recipe, and (recipe, Foodics option) → modifier."""
    products = _live_products(order)
    product_ids = {str((p.get("product") or {}).get("id") or "") for p in products}
    option_ids = {
        str((o.get("modifier_option") or {}).get("id") or "")
        for p in products
        for o in p.get("options") or []
        if isinstance(o, dict)
    }
    wanted = sorted((product_ids | option_ids) - {""})
    history: dict[str, tuple[str | None, str | None]] = {}
    if wanted:
        rows = await db.execute(_HISTORY_SQL, {"ids": wanted})
        for external_id, recipe_id, modifier_id in rows:
            history[external_id] = (recipe_id, modifier_id)

    # A recipe or modifier GrubOps has never served comes off the approved map
    # by exact name — and only when the name is unique there.
    approved = (
        await db.execute(
            select(
                ExternalItemMap.mm_kind,
                ExternalItemMap.external_ref,
                ExternalItemMap.external_sub_ref,
                ExternalItemMap.external_name,
            ).where(
                ExternalItemMap.system == "grubops",
                ExternalItemMap.approved.is_(True),
                ExternalItemMap.mm_kind.in_([KIND_PRODUCT, KIND_OPTION]),
            )
        )
    ).all()
    recipe_by_name: dict[str, set[str]] = {}
    modifier_by_name: dict[tuple[str, str], set[str]] = {}
    for kind, ref, sub_ref, name in approved:
        if kind == KIND_PRODUCT:
            recipe_by_name.setdefault(_name_key(name), set()).add(ref)
        elif sub_ref:
            modifier_by_name.setdefault((ref, _name_key(name)), set()).add(sub_ref)

    recipes: dict[str, str] = {}
    modifiers: dict[tuple[str, str], str] = {}
    for product in products:
        item = product.get("product") or {}
        foodics_id = str(item.get("id") or "")
        recipe_id = (history.get(foodics_id) or (None, None))[0]
        if not recipe_id:
            named = recipe_by_name.get(_name_key(item.get("name")), set())
            recipe_id = next(iter(named)) if len(named) == 1 else None
        if not recipe_id:
            continue
        recipes[foodics_id] = recipe_id
        for option in product.get("options") or []:
            if not isinstance(option, dict):
                continue
            mod = option.get("modifier_option") or {}
            option_id = str(mod.get("id") or "")
            seen_recipe, modifier_id = history.get(option_id) or (None, None)
            # A modifier id only means something under its own recipe.
            if not modifier_id or seen_recipe != recipe_id:
                named = modifier_by_name.get(
                    (recipe_id, _name_key(mod.get("name"))), set()
                )
                modifier_id = next(iter(named)) if len(named) == 1 else None
            if modifier_id:
                modifiers[(recipe_id, option_id)] = modifier_id
    return recipes, modifiers


def _report(order_map: GrubOpsOrderMap, reason: str, *, level=logging.WARNING) -> None:
    key = (order_map.grubops_order_id, reason)
    if key in _reported:
        return
    _reported.add(key)
    logger.log(
        level,
        "GrubOps order %s (%s %s): Foodics fallback not used — %s",
        order_map.grubops_order_id,
        order_map.source_channel,
        order_map.external_id,
        reason,
    )


async def build_from_foodics(
    db, summary: dict, order_map: GrubOpsOrderMap, *, now: datetime | None = None
) -> dict | None:
    """The order as Foodics holds it, in GrubOps' shape — or None to keep waiting.

    None in every case it cannot be sure: the write-back is off (an order nobody
    can Pack is no better than a late one), the order is too young (GrubOps may
    still serve it) or too old (promotion's), its branch has no Foodics map, or
    Foodics is unreachable, has no single match, or disagrees with itself. Never
    raises for a Foodics problem; the caller retries on the next tick.
    """
    if not foodics_orders_service.is_enabled() or order_map.mm_order_id is not None:
        return None
    listed = grubops_orders_service._parse_ts(summary.get("createdAt"))
    if listed is None:
        return None
    age = (now or datetime.now(timezone.utc)) - listed
    if age < FALLBACK_GRACE or age > FALLBACK_MAX_AGE:
        return None

    foodics_branch_id = (
        await db.execute(
            select(FoodicsBranchMap.foodics_branch_id)
            .join(
                GrubOpsLocationMap,
                GrubOpsLocationMap.branch_id == FoodicsBranchMap.branch_id,
            )
            .where(
                GrubOpsLocationMap.grubops_location_id == order_map.location_id,
                FoodicsBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not foodics_branch_id:
        _report(order_map, "its branch has no active Foodics map")
        return None

    match: dict | None = None
    reason = "no Foodics order matches"
    try:
        for page in range(1, _MAX_PAGES + 1):
            orders = await foodics.list_recent_orders(
                branch_id=foodics_branch_id, page=page
            )
            match, reason = find_match(
                summary, orders, foodics_branch_id=foodics_branch_id
            )
            if match is not None or reason != "no Foodics order matches":
                break
            oldest = min(
                (t for t in (_foodics_ts(o.get("created_at")) for o in orders) if t),
                default=None,
            )
            # Newest first: once a page reaches back past the window, so has every
            # page after it.
            if not orders or oldest is None or oldest < listed - MATCH_WINDOW:
                break
    except FoodicsError as exc:
        logger.warning(
            "GrubOps order %s: Foodics fallback could not read Foodics: %s",
            order_map.grubops_order_id,
            exc,
        )
        return None

    if match is None:
        _report(order_map, reason)
        return None
    disagreement = money_disagreement(match)
    if disagreement:
        _report(order_map, disagreement)
        return None

    recipes, modifiers = await _id_maps(db, match)
    info = build_info(summary, match, recipes=recipes, modifiers=modifiers)
    logger.warning(
        "GrubOps order %s (%s %s) still has no detail after %ds; building it from "
        "Foodics order %s (#%s)",
        order_map.grubops_order_id,
        order_map.source_channel,
        order_map.external_id,
        int(age.total_seconds()),
        match.get("id"),
        match.get("reference"),
    )
    return info
