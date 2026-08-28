"""Expand portal modifier payloads into `StandardModifier` rows (with qty).

Each channel's raw shape differs — nested qty maps (Noon), attribute lists
(Keeta), option arrays (Deliveroo) — but ingest and promote only ever see this
list. Quantity defaults to 1 only when the portal omitted it; never invent qty
by duplicating rows.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.aggregators.normalized import StandardModifier


def _as_decimal(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _qty(value: Any) -> Decimal:
    parsed = _as_decimal(value, default=Decimal("1"))
    if parsed is None or parsed <= 0:
        return Decimal("1")
    return parsed


def modifier_to_dict(mod: StandardModifier) -> dict[str, Any]:
    """JSONB-safe dict for `aggregator_order_item.modifiers`."""
    return {
        "name": mod.name,
        "quantity": str(mod.quantity),
        "unit_price": str(mod.unit_price) if mod.unit_price is not None else None,
        "external_ref": mod.external_ref,
    }


def modifiers_to_json(mods: list[StandardModifier]) -> list[dict[str, Any]] | None:
    if not mods:
        return None
    return [modifier_to_dict(m) for m in mods]


def modifiers_from_json(raw: Any) -> list[StandardModifier]:
    """Rehydrate stored JSONB back into DTOs (promote path)."""
    if not raw or not isinstance(raw, list):
        return []
    out: list[StandardModifier] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out.append(
            StandardModifier(
                name=name,
                quantity=_qty(row.get("quantity")),
                unit_price=_as_decimal(row.get("unit_price")),
                external_ref=(
                    str(row["external_ref"]).strip()
                    if row.get("external_ref")
                    else None
                ),
            )
        )
    return out


def expand_modifiers(
    raw: Any,
    *,
    name_keys: tuple[str, ...] = (
        "name",
        "nameEn",
        "name_en",
        "option_name",
        "modifier_name",
        "title",
        "label",
    ),
    qty_keys: tuple[str, ...] = ("quantity", "qty", "count", "amount"),
    price_keys: tuple[str, ...] = (
        "unit_price",
        "price",
        "option_price",
        "unitPrice",
    ),
    ref_keys: tuple[str, ...] = (
        "external_ref",
        "option_id",
        "optionId",
        "modifierCode",
        "modifier_code",
        "id",
        "code",
        "sku",
    ),
) -> list[StandardModifier]:
    """Best-effort expand of common portal shapes into StandardModifier rows.

    Handles:
    - list of option dicts
    - list of bare code strings
    - Noon-style nested map `{modifierCode: {optionCode: qty}}`
    - single dict option
    """
    if raw is None or raw == "" or raw == {} or raw == []:
        return []

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return [StandardModifier(name=text, quantity=Decimal("1"))]
        return expand_modifiers(
            parsed,
            name_keys=name_keys,
            qty_keys=qty_keys,
            price_keys=price_keys,
            ref_keys=ref_keys,
        )

    out: list[StandardModifier] = []

    if isinstance(raw, dict):
        # Noon nested qty map: values are dicts of optionCode -> qty, OR a single option.
        if raw and all(isinstance(v, (dict, int, float, str)) for v in raw.values()):
            nested_maps = [v for v in raw.values() if isinstance(v, dict)]
            # Guard: the outer dict has no name-like keys (it's coded, not named)
            # and the inner dicts' leaves are all scalar qtys — not option objects.
            outer_has_no_name_key = not any(k in name_keys for k in raw)
            if nested_maps and outer_has_no_name_key:
                # Heuristic: dict-of-dicts with numeric leaf values → qty map.
                looks_like_qty_map = all(
                    isinstance(v, dict)
                    and all(
                        isinstance(inner_v, (int, float, str, Decimal))
                        for inner_v in v.values()
                    )
                    for v in raw.values()
                    if isinstance(v, dict)
                )
                if looks_like_qty_map and any(
                    isinstance(v, dict) for v in raw.values()
                ):
                    for mod_code, options in raw.items():
                        if not isinstance(options, dict):
                            continue
                        for opt_code, qty in options.items():
                            ref = str(opt_code)
                            out.append(
                                StandardModifier(
                                    name=ref,
                                    quantity=_qty(qty),
                                    external_ref=ref,
                                )
                            )
                    if out:
                        return out

        # Single option object.
        name = _first_str(raw, name_keys)
        ref = _first_str(raw, ref_keys)
        if name or ref:
            out.append(
                StandardModifier(
                    name=(name or ref or "").strip(),
                    quantity=_qty(_first_raw(raw, qty_keys)),
                    unit_price=_as_decimal(_first_raw(raw, price_keys)),
                    external_ref=ref,
                )
            )
        return out

    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str):
                text = entry.strip()
                if text:
                    out.append(
                        StandardModifier(
                            name=text, quantity=Decimal("1"), external_ref=text
                        )
                    )
                continue
            if isinstance(entry, dict):
                out.extend(
                    expand_modifiers(
                        entry,
                        name_keys=name_keys,
                        qty_keys=qty_keys,
                        price_keys=price_keys,
                        ref_keys=ref_keys,
                    )
                )
        return out

    return out


def _first_str(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_raw(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    return None
