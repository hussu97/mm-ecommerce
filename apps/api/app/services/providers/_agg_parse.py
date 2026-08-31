"""Shared field-parsing primitives for the aggregator provider clients.

Every marketplace client (careem, deliveroo, keeta, noon, talabat) had grown its
own copy of the same three helpers — a money cleaner, a "first present key"
picker, and the `Asia/Dubai` business timezone — differing only in incidental
detail (which currency tokens each stripped). Five near-identical copies is how a
money-parsing rule drifts silently between channels, so they live here once.

Kept deliberately small: only the primitives that were genuinely identical across
channels. Datetime parsing is NOT here — the channels really do parse different
formats (talabat's CSV `%Y-%m-%d %H:%M` vs the others' ISO-8601), so forcing one
parser would be a lie, not a dedup.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

#: The business timezone every channel's calendar day is aligned to (the ingest's
#: Dubai-aligned range window, the daily report's trading day). One definition so
#: a channel cannot silently disagree about when "today" is.
DUBAI_TZ = ZoneInfo("Asia/Dubai")

#: Currency/whitespace tokens stripped from a human money string before parsing —
#: the union of what each channel used to strip on its own (`AED`/`aed`/`د.إ`
#: prefixes, thousands commas, and the non-breaking space Talabat's export
#: carries). Stripping a token a given channel never emits is harmless, so one
#: superset is safe for all five.
_MONEY_TOKENS = ("AED", "aed", "د.إ", ",", "\xa0")


def parse_money(value: Any) -> Decimal | None:
    """A money value as `Decimal`, or `None` for anything not a clean number.

    `None` — not `0` — for a blank/absent/unparseable value: a null fee and a
    zero fee are different claims, and the normalized layer keeps them distinct.
    Handles the shapes the channels actually emit:

    * an already-parsed `int`/`float`/`Decimal`;
    * a human string — `"1,234.50 AED"`, `"(5.00)"` for a parenthesised negative;
    * Careem's `{"amount": 357.53, "currency": "AED"}` money object (the bare
      `amount` is taken).

    `bool` is rejected (it is an `int` subclass, but `True` is not money).
    """
    if isinstance(value, dict):
        value = value.get("amount")
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for token in _MONEY_TOKENS:
        text = text.replace(token, "")
    cleaned = text.replace("(", "-").replace(")", "").strip()
    if cleaned in {"", "-"}:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def first_present(mapping: Any, *keys: str) -> Any:
    """The first present, non-null value among `keys` — for a field a payload
    spells more than one way across endpoints, or a CSV heads two ways."""
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) is not None:
            return mapping[key]
    return None
