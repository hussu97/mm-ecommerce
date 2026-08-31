"""The cross-integrator diff — what MM would change on one outlet.

Pure functions, no DB, no I/O: given MM's desired menu (or schedule) and one
integrator's actual menu (or schedule) — both as the channel-neutral shapes from
`menu_normalized` — return a flat list of deltas an operator can read and approve.
This is the read-only heart of the feature; the writer (a later phase) consumes an
approved diff.

**Matching.** Names have already drifted between channels (the audit found "Boxes"
vs "Mix Boxes", "& Walnut" vs "and Walnut"), so items and categories match on a
*normalised* name — lower-cased, `&`→`and`, punctuation stripped, whitespace
collapsed — with a token-subset fallback that pairs "Boxes" with "Mix Boxes" and
reports it as a rename rather than a missing+extra pair. A future pass can match on
the seeded identity map instead; name matching is the safe default before it exists.

**Price parity.** With `enforce_price_parity` (the `CATALOG_SYNC_ENFORCE_PRICE_PARITY`
policy) any *known* price difference is a delta — no seasonal-uplift exceptions. A
`None` price (an integrator that hid the number behind a restricted role) is
"unknown", never a mismatch: we do not invent drift from a value we could not read.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.services.aggregators.menu_normalized import (
    NormalizedHours,
    NormalizedItem,
    NormalizedMenu,
)

# ── Delta vocabulary ──────────────────────────────────────────────────────────
# `action` is what the writer would do: add a thing missing on the channel, delete
# a thing not in MM (deletion is allowed), update a value, or just note it.
ACTION_ADD = "add"
ACTION_DELETE = "delete"
ACTION_UPDATE = "update"
ACTION_INFO = "info"

# `kind` is the specific delta. Category / item / option granularity.
K_CATEGORY_MISSING = "category_missing_on_channel"
K_CATEGORY_EXTRA = "category_extra_on_channel"
K_CATEGORY_RENAME = "category_name_drift"
K_ITEM_MISSING = "item_missing_on_channel"
K_ITEM_EXTRA = "item_extra_on_channel"
K_ITEM_RENAME = "item_name_drift"
K_ITEM_DESC = "item_description_drift"
K_ITEM_PRICE = "item_price_mismatch"
K_ITEM_UNAVAILABLE = "item_unavailable_on_channel"
K_OPTION_PRICE = "option_price_mismatch"
K_OPTION_MISSING = "option_missing_on_channel"
K_OPTION_EXTRA = "option_extra_on_channel"

# ── Hours delta kinds ─────────────────────────────────────────────────────────
K_HOURS_SHIFT = "hours_shift_mismatch"
K_HOURS_DAY_CLOSED_CHANNEL = "hours_day_closed_on_channel"  # MM open, channel shut
K_HOURS_DAY_OPEN_CHANNEL = "hours_day_open_on_channel"  # MM shut, channel open
K_HOURS_CLOSURE = "hours_closure_only_on_channel"

_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


@dataclass
class Delta:
    """One difference between MM (desired) and an integrator (actual)."""

    kind: str
    action: str
    #: The category / item / option this is about, by its MM (or channel) name.
    entity: str
    #: Human-facing values for the review screen. Strings so JSONB is clean.
    mm_value: str | None = None
    channel_value: str | None = None
    #: Extra context — the category an item sits in, an option group, a note.
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MenuDiff:
    """The full set of menu deltas for one outlet, plus a counts summary."""

    target: str
    deltas: list[Delta] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.deltas:
            out[d.kind] = out.get(d.kind, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "total": len(self.deltas),
            "summary": self.summary,
            "deltas": [d.to_dict() for d in self.deltas],
        }


# ── Name normalisation & matching ─────────────────────────────────────────────

_PUNCT = re.compile(r"[^a-z0-9\s]")
_WS = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """Lower-case, `&`→`and`, drop punctuation, collapse whitespace.

    So "Dark Chocolate & Walnut Brownies" and "Dark Chocolate and Walnut
    Brownies" normalise equal, and the diff reports the raw drift once rather
    than a spurious missing+extra pair.
    """
    if not name:
        return ""
    s = name.strip().lower().replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _tokens(name: str) -> set[str]:
    return set(normalize_name(name).split())


def _fuzzy_match(target: str, candidates: list[str]) -> str | None:
    """A candidate whose tokens are a subset of `target`'s (or vice versa).

    Pairs "Boxes" with "Mix Boxes" (one token set ⊆ the other) so a rename reads
    as a rename. Returns the best (largest-overlap) candidate, or None.
    """
    t = _tokens(target)
    if not t:
        return None
    best: tuple[int, str] | None = None
    for c in candidates:
        ct = _tokens(c)
        if not ct:
            continue
        if t <= ct or ct <= t:
            overlap = len(t & ct)
            if best is None or overlap > best[0]:
                best = (overlap, c)
    return best[1] if best else None


def _prices_disagree(a: Decimal | None, b: Decimal | None) -> bool:
    """True only when both prices are known and differ. Unknown is never drift."""
    if a is None or b is None:
        return False
    return Decimal(a) != Decimal(b)


def _item_effective_prices(item: NormalizedItem) -> list[tuple[str, Decimal | None]]:
    """The (label, price) set that defines an item's price.

    A flat-priced item is one entry (its own price). A variant-priced item (MM's
    brownies: base 0 + a size modifier) contributes its priced options, so the
    diff compares 3/6/9-piece prices, not a meaningless base 0.
    """
    priced_opts = [
        (o.name, o.price)
        for g in item.modifier_groups
        for o in g.options
        if o.price is not None and o.price != Decimal("0")
    ]
    if priced_opts:
        return priced_opts
    return [("", item.price)]


# ── Menu diff ─────────────────────────────────────────────────────────────────


def _index_items(menu: NormalizedMenu) -> dict[str, tuple[NormalizedItem, str]]:
    """normalised item name → (item, its category name), flattened across the menu.

    Item identity is the name, not the (category, name) pair: channels regroup
    categories constantly ("Boxes" vs "Mix Boxes", a "New In" shelf, Foodics'
    flat price tag), so an item that moved category has NOT gone missing. Match
    globally; report the category move as metadata.
    """
    out: dict[str, tuple[NormalizedItem, str]] = {}
    for cat in menu.categories:
        for item in cat.items:
            out.setdefault(normalize_name(item.name), (item, cat.name))
    return out


def diff_menu(
    desired: NormalizedMenu,
    actual: NormalizedMenu,
    *,
    target: str,
    enforce_price_parity: bool = True,
) -> MenuDiff:
    """Diff MM's desired menu against one integrator's actual menu.

    Items match **globally by name** (a moved category is not a missing item);
    category structure is reported as derived metadata (a rename, or a category
    with nothing matched).
    """
    out = MenuDiff(target=target)
    d_index = _index_items(desired)
    a_index = _index_items(actual)
    matched: set[str] = set()
    # For category-level reporting: which channel category each MM category's
    # items landed in, and whether a category had any match at all.
    cat_landing: dict[str, dict[str, int]] = {}

    for key, (ditem, dcat) in d_index.items():
        aitem_cat = a_index.get(key)
        if aitem_cat is None:
            fuzzy = _fuzzy_match(ditem.name, [k for k in a_index if k not in matched])
            if fuzzy is not None:
                fkey = normalize_name(fuzzy)
                aitem, acat = a_index[fkey]
                matched.add(fkey)
                out.deltas.append(
                    Delta(
                        kind=K_ITEM_RENAME,
                        action=ACTION_UPDATE,
                        entity=ditem.name,
                        mm_value=ditem.name,
                        channel_value=aitem.name,
                        detail=f"MM category {dcat}",
                    )
                )
                _diff_one_item(out, ditem, aitem, dcat, enforce_price_parity)
                cat_landing.setdefault(dcat, {})[acat] = (
                    cat_landing.setdefault(dcat, {}).get(acat, 0) + 1
                )
                continue
            out.deltas.append(
                Delta(
                    kind=K_ITEM_MISSING,
                    action=ACTION_ADD,
                    entity=ditem.name,
                    detail=f"category {dcat}",
                )
            )
            cat_landing.setdefault(dcat, {})
            continue
        aitem, acat = aitem_cat
        matched.add(key)
        _diff_one_item(out, ditem, aitem, dcat, enforce_price_parity)
        cat_landing.setdefault(dcat, {})[acat] = (
            cat_landing.setdefault(dcat, {}).get(acat, 0) + 1
        )

    # Leftover channel items — on the portal, not in MM. Deletion is allowed.
    for key, (aitem, acat) in a_index.items():
        if key in matched:
            continue
        out.deltas.append(
            Delta(
                kind=K_ITEM_EXTRA,
                action=ACTION_DELETE,
                entity=aitem.name,
                detail=f"on channel, category {acat}; review before delete",
            )
        )

    _category_deltas(out, desired, actual, cat_landing)
    return out


def _category_deltas(
    out: MenuDiff,
    desired: NormalizedMenu,
    actual: NormalizedMenu,
    cat_landing: dict[str, dict[str, int]],
) -> None:
    """Derive category-level deltas from where items actually landed."""
    actual_cat_names = {normalize_name(c.name): c.name for c in actual.categories}
    for dcat in desired.categories:
        landed = cat_landing.get(dcat.name, {})
        if not landed:
            out.deltas.append(
                Delta(
                    kind=K_CATEGORY_MISSING,
                    action=ACTION_ADD,
                    entity=dcat.name,
                    detail=f"{len(dcat.items)} item(s) would be added",
                )
            )
            continue
        # The channel category most of this MM category's items sit in.
        best = max(landed, key=lambda c: landed[c])
        if normalize_name(best) != normalize_name(dcat.name):
            out.deltas.append(
                Delta(
                    kind=K_CATEGORY_RENAME,
                    action=ACTION_UPDATE,
                    entity=dcat.name,
                    mm_value=dcat.name,
                    channel_value=best,
                    detail="MM category maps to a differently-named channel one",
                )
            )
    # Channel categories whose items are entirely absent from MM.
    landed_channel = {normalize_name(c) for cats in cat_landing.values() for c in cats}
    for akey, aname in actual_cat_names.items():
        if akey not in landed_channel:
            out.deltas.append(
                Delta(
                    kind=K_CATEGORY_EXTRA,
                    action=ACTION_DELETE,
                    entity=aname,
                    detail="on channel, not in MM; review before delete",
                )
            )


def _diff_one_item(
    out: MenuDiff,
    ditem: NormalizedItem,
    aitem: NormalizedItem,
    category: str,
    enforce_price_parity: bool,
) -> None:
    if (
        (ditem.description or "")
        and (aitem.description or "")
        and (normalize_name(ditem.description) != normalize_name(aitem.description))
    ):
        out.deltas.append(
            Delta(
                kind=K_ITEM_DESC,
                action=ACTION_UPDATE,
                entity=ditem.name,
                detail=f"category {category}",
            )
        )
    if aitem.is_available is False and ditem.is_available is True:
        out.deltas.append(
            Delta(
                kind=K_ITEM_UNAVAILABLE,
                action=ACTION_INFO,
                entity=ditem.name,
                detail=f"active in MM, unavailable on channel ({category})",
            )
        )

    if enforce_price_parity:
        if ditem.modifier_groups and aitem.modifier_groups:
            # Both sides expose variant options — compare the priced ones.
            _diff_option_prices(out, ditem, aitem, category)
        elif not ditem.modifier_groups and not aitem.modifier_groups:
            # Flat-priced on both sides — compare the item price directly.
            if _prices_disagree(ditem.price, aitem.price):
                out.deltas.append(
                    Delta(
                        kind=K_ITEM_PRICE,
                        action=ACTION_UPDATE,
                        entity=ditem.name,
                        mm_value=str(ditem.price),
                        channel_value=str(aitem.price),
                        detail=f"category {category}",
                    )
                )
        # else: one side is variant-priced and the other flat (e.g. a channel
        # reader that lists products but not their options) — we cannot compare
        # option prices we did not read, so we do not invent a "missing option".


def _diff_option_prices(
    out: MenuDiff, ditem: NormalizedItem, aitem: NormalizedItem, category: str
) -> None:
    """Compare the priced options (variant sizes) of a modifier-priced item."""
    d_opts = {
        normalize_name(name): price
        for name, price in _item_effective_prices(ditem)
        if name
    }
    a_opts = {
        normalize_name(name): price
        for name, price in _item_effective_prices(aitem)
        if name
    }
    for name, dprice in d_opts.items():
        if name not in a_opts:
            out.deltas.append(
                Delta(
                    kind=K_OPTION_MISSING,
                    action=ACTION_ADD,
                    entity=ditem.name,
                    detail=f"option {name} ({category})",
                )
            )
            continue
        if _prices_disagree(dprice, a_opts[name]):
            out.deltas.append(
                Delta(
                    kind=K_OPTION_PRICE,
                    action=ACTION_UPDATE,
                    entity=ditem.name,
                    mm_value=str(dprice),
                    channel_value=str(a_opts[name]),
                    detail=f"option {name} ({category})",
                )
            )
    for name in a_opts:
        if name not in d_opts:
            out.deltas.append(
                Delta(
                    kind=K_OPTION_EXTRA,
                    action=ACTION_DELETE,
                    entity=ditem.name,
                    detail=f"option {name} ({category})",
                )
            )


# ── Hours diff ────────────────────────────────────────────────────────────────


@dataclass
class HoursDiff:
    target: str
    deltas: list[Delta] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "total": len(self.deltas),
            "deltas": [d.to_dict() for d in self.deltas],
        }


def _by_day(hours: NormalizedHours) -> dict[int, list[tuple[str, str]]]:
    out: dict[int, list[tuple[str, str]]] = {}
    for s in hours.shifts:
        out.setdefault(s.weekday, []).append((s.opens, s.closes))
    for day in out:
        out[day].sort()
    return out


def diff_hours(
    desired: NormalizedHours, actual: NormalizedHours, *, target: str
) -> HoursDiff:
    """Diff MM's weekly schedule against one integrator's actual hours."""
    out = HoursDiff(target=target)
    d = _by_day(desired)
    a = _by_day(actual)
    for day in range(7):
        dd = d.get(day, [])
        aa = a.get(day, [])
        label = _WEEKDAYS[day]
        if dd and not aa:
            out.deltas.append(
                Delta(
                    kind=K_HOURS_DAY_CLOSED_CHANNEL,
                    action=ACTION_UPDATE,
                    entity=label,
                    mm_value=_fmt(dd),
                    channel_value="closed",
                )
            )
        elif aa and not dd:
            out.deltas.append(
                Delta(
                    kind=K_HOURS_DAY_OPEN_CHANNEL,
                    action=ACTION_UPDATE,
                    entity=label,
                    mm_value="closed",
                    channel_value=_fmt(aa),
                )
            )
        elif dd != aa:
            out.deltas.append(
                Delta(
                    kind=K_HOURS_SHIFT,
                    action=ACTION_UPDATE,
                    entity=label,
                    mm_value=_fmt(dd),
                    channel_value=_fmt(aa),
                )
            )
    for closure in sorted(set(actual.closures) - set(desired.closures)):
        out.deltas.append(
            Delta(
                kind=K_HOURS_CLOSURE,
                action=ACTION_INFO,
                entity=closure,
                channel_value="closed",
                detail="one-off closure on channel, not in MM",
            )
        )
    return out


def _fmt(shifts: list[tuple[str, str]]) -> str:
    return ", ".join(f"{o}-{c}" for o, c in shifts) or "closed"
