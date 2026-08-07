"""
One shape for a phone number, everywhere one is compared to another.

A phone number is now an **identity**, not just somebody to ring when a driver
cannot find the door. The new-customer coupon counts a customer's orders across
account, email and phone precisely because the first two are free to mint, and
the phone is the one that costs something to fake. That only holds while two
spellings of the same number compare equal.

They do not, on their own. `0501234567`, `971501234567`, `+971 50 123 4567` and
`00971501234567` are one handset and four different strings, and a rule that
matches on the raw text is a rule anybody can walk past by typing their number
differently each time. The storefront's `PhoneInput` emits E.164 and hides the
problem in a browser — but the API is a public entry point and cannot rely on
its own client being the only caller.

`""` for anything that cannot be read as a number, rather than a guess. A wrong
number strands a driver outside a building, and a guessed identity is worse than
no identity: it merges two customers.

Lifted out of `lalamove_service`, where it lived because the courier's API
demanded E.164. Same function, same rules, now in the one place the rest of the
codebase can reach without importing a courier.
"""

from __future__ import annotations

__all__ = ["normalise_phone", "phone_identities"]

#: The UAE, because that is who orders. A bare national number with no country
#: information is read as one of ours.
_DEFAULT_COUNTRY_CODE = "971"


def normalise_phone(raw: str | None) -> str:
    """
    E.164, or nothing.

    Accepts what people actually type: spaces, dashes, brackets, a leading zero,
    an international prefix written as `00`. Returns `""` for anything left that
    cannot be read as a phone number.
    """
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        return digits if len(digits) >= 8 else ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(_DEFAULT_COUNTRY_CODE):
        return f"+{digits}"
    if digits.startswith("0"):
        digits = digits[1:]
    if 8 <= len(digits) <= 10:
        return f"+{_DEFAULT_COUNTRY_CODE}{digits}"
    return f"+{digits}" if len(digits) >= 10 else ""


def phone_identities(raw: str | None) -> list[str]:
    """
    Every spelling of this number a stored row might legitimately hold.

    The canonical form, and the string as it was given, when they differ. Rows
    written before this normalisation existed hold whatever was typed at the
    counter, so a lookup that asked only for the canonical form would quietly
    stop seeing them — and a coupon rule that stops seeing a customer's history
    is a coupon rule that lets them start again.

    Empty list when there is no usable number at all, which callers read as "do
    not add a phone clause" rather than as "match nothing".
    """
    given = (raw or "").strip()
    canonical = normalise_phone(given)
    return [value for value in dict.fromkeys((canonical, given)) if value]
