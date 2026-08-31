"""The channel-neutral shape of a *menu* and a *schedule*, for the catalog sync.

`normalized.py` gives the ingest one vocabulary for orders/finance. This is the
same idea for the other direction: every integrator describes its menu and hours
differently (Foodics groups + a price tag, Talabat's menu-v2, Careem's catalog,
Keeta's product tree), and none of that reaches the diff engine — each reader
translates its integrator into these dataclasses at the edge, and MM's own
catalogue is translated into the *same* shape, so the diff is one comparison of
two `NormalizedMenu`s: MM (desired) against the integrator (actual).

Money is `Decimal | None`. None means "the integrator did not expose a price"
(e.g. a restricted Talabat role that renders AED 0.00), which is not the same as a
real zero — a price diff must not fire on an unknown.

Everything here is a plain, JSON-round-trippable value: `to_dict`/`from_dict` put
a whole menu (or schedule) into `aggregator_menu_snapshot.normalized` and back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _dec_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass
class NormalizedOption:
    """One option within a modifier group — a size, an add-on, a box filling."""

    name: str
    #: The integrator's option id, when it has one. The map keys on this.
    external_ref: str | None = None
    #: Option price. None = the integrator did not tell us (not free).
    price: Decimal | None = None
    is_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "external_ref": self.external_ref,
            "price": _dec_str(self.price),
            "is_available": self.is_available,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalizedOption:
        return cls(
            name=d.get("name", ""),
            external_ref=d.get("external_ref"),
            price=_dec(d.get("price")),
            is_available=bool(d.get("is_available", True)),
        )


@dataclass
class NormalizedModifierGroup:
    """One option group on an item — MM's `Modifier` / a channel's choice group."""

    name: str
    external_ref: str | None = None
    min_options: int | None = None
    max_options: int | None = None
    options: list[NormalizedOption] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "external_ref": self.external_ref,
            "min_options": self.min_options,
            "max_options": self.max_options,
            "options": [o.to_dict() for o in self.options],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalizedModifierGroup:
        return cls(
            name=d.get("name", ""),
            external_ref=d.get("external_ref"),
            min_options=d.get("min_options"),
            max_options=d.get("max_options"),
            options=[NormalizedOption.from_dict(o) for o in d.get("options", [])],
        )


@dataclass
class NormalizedItem:
    """One product on a menu — with its price and modifier groups."""

    name: str
    #: The integrator's item id (or MM's product id for the desired side). The map
    #: keys on this; `external_ref` is the normalised name fallback for matching.
    external_id: str | None = None
    external_ref: str | None = None
    description: str | None = None
    #: The item's own price. For a variant-priced item (MM's brownies) this is 0
    #: and the real prices live in a modifier group — the diff reads both.
    price: Decimal | None = None
    is_available: bool = True
    category_ref: str | None = None
    modifier_groups: list[NormalizedModifierGroup] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "external_id": self.external_id,
            "external_ref": self.external_ref,
            "description": self.description,
            "price": _dec_str(self.price),
            "is_available": self.is_available,
            "category_ref": self.category_ref,
            "modifier_groups": [g.to_dict() for g in self.modifier_groups],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalizedItem:
        return cls(
            name=d.get("name", ""),
            external_id=d.get("external_id"),
            external_ref=d.get("external_ref"),
            description=d.get("description"),
            price=_dec(d.get("price")),
            is_available=bool(d.get("is_available", True)),
            category_ref=d.get("category_ref"),
            modifier_groups=[
                NormalizedModifierGroup.from_dict(g)
                for g in d.get("modifier_groups", [])
            ],
        )


@dataclass
class NormalizedCategory:
    """One menu category and its items."""

    name: str
    external_id: str | None = None
    items: list[NormalizedItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "external_id": self.external_id,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalizedCategory:
        return cls(
            name=d.get("name", ""),
            external_id=d.get("external_id"),
            items=[NormalizedItem.from_dict(i) for i in d.get("items", [])],
        )


@dataclass
class NormalizedMenu:
    """A whole outlet menu, from either side of the comparison."""

    #: `foodics`, a marketplace channel, or `mm` for the desired side.
    source: str
    categories: list[NormalizedCategory] = field(default_factory=list)
    #: Free note when a read was partial (a restricted role, a page cap) so the
    #: diff can down-weight a "missing" that is really "not shown".
    truncation_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "categories": [c.to_dict() for c in self.categories],
            "truncation_note": self.truncation_note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalizedMenu:
        return cls(
            source=d.get("source", ""),
            categories=[
                NormalizedCategory.from_dict(c) for c in d.get("categories", [])
            ],
            truncation_note=d.get("truncation_note"),
        )


# ── Hours ─────────────────────────────────────────────────────────────────────


@dataclass
class NormalizedShift:
    """One open shift on one weekday. weekday 0=Sunday … 6=Saturday."""

    weekday: int
    opens: str  # "HH:MM"
    closes: str  # "HH:MM"

    def to_dict(self) -> dict[str, Any]:
        return {"weekday": self.weekday, "opens": self.opens, "closes": self.closes}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalizedShift:
        return cls(
            weekday=int(d["weekday"]),
            opens=d.get("opens", ""),
            closes=d.get("closes", ""),
        )


@dataclass
class NormalizedHours:
    """One outlet's weekly schedule (open shifts) + one-off closures."""

    source: str
    shifts: list[NormalizedShift] = field(default_factory=list)
    #: `YYYY-MM-DD` full-day closures the portal advertises (Deliveroo Days off,
    #: Keeta Special hours, a Talabat Branch-Status pause window). Best-effort.
    closures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "shifts": [s.to_dict() for s in self.shifts],
            "closures": list(self.closures),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NormalizedHours:
        return cls(
            source=d.get("source", ""),
            shifts=[NormalizedShift.from_dict(s) for s in d.get("shifts", [])],
            closures=list(d.get("closures", [])),
        )
