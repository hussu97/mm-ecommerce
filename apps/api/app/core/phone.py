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

**The check used to be length arithmetic** — anything starting with `+` and
eight characters long was a phone number, and any bare run of eight to ten
digits became a UAE one. `+12345678` passed. Meanwhile the storefront's
`PhoneInput` runs libphonenumber-js and rejects it, which put the *stricter*
check on the side of the wire we do not control: a browser is a courtesy, the
API is the door. For a value whose entire job is to be an identity that costs
something to fake, "eight or more digits" is not a check — it is eight
keystrokes and a fresh discount.

So this delegates to `phonenumbers`, the Python port of Google's libphonenumber
and the same metadata the storefront uses. `is_valid_number` means the number
matches a real numbering plan for its country — an assigned prefix and the
right length — not merely that it looks numeric.
"""

from __future__ import annotations

from dataclasses import dataclass

import phonenumbers
from phonenumbers import PhoneNumberType

__all__ = ["PhoneParts", "describe_phone", "normalise_phone", "phone_identities"]

#: The UAE, because that is who orders. A bare national number with no country
#: information is read as one of ours — `0501234567` and `04 445 1555` are the
#: two shapes the shop actually receives.
_DEFAULT_REGION = "AE"


def _parsed(candidate: str) -> str:
    """E.164 for *candidate* if it is a real number, `""` otherwise."""
    try:
        number = phonenumbers.parse(candidate, _DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(number):
        return ""
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalise_phone(raw: str | None) -> str:
    """
    E.164, or nothing.

    Accepts what people actually type: spaces, dashes, brackets, a leading zero,
    an international prefix written as `00`, the `(0)` some people put in an
    international number, and Arabic-Indic digits. Returns `""` for anything
    left that cannot be read as a *valid* phone number.

    Two passes, because the previous implementation was tolerant of surrounding
    junk in a way that mattered: it kept the digits and threw the rest away, so
    a stored number like `0501234567 (Ali)` still reached a courier. The strict
    parse is tried first; failing that, the string is reduced to its digits (and
    a leading `+`) and tried again. Validity is demanded either way, so the
    fallback restores the old tolerance without restoring the old credulity —
    `abc0501234567xyz` still normalises, `+12345678` no longer does.
    """
    if not raw:
        return ""

    direct = _parsed(raw)
    if direct:
        return direct

    # The old cleaning rule, reused only as a second chance. `+` is kept
    # wherever it appears for the same reason it was: a number typed as
    # `+971 (0)50…` has already been handled above, and one typed with stray
    # characters around it is still telling us its country.
    reduced = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if reduced and reduced != raw.strip():
        return _parsed(reduced)
    return ""


#: `phonenumbers` line type → the plain word the shop reads. A UAE landline
#: ("04 445 1555") and a mobile ("050…") are the two shapes the counter sorts by
#: at a glance; toll-free is the marketplace's own line (Deliveroo's 800 number).
#: Anything else — the rare fixed-or-mobile, a premium line — is "other" rather
#: than a guess between the two the shop cares about.
_TYPE_LABELS: dict[int, str] = {
    PhoneNumberType.FIXED_LINE: "landline",
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "mobile",
    PhoneNumberType.TOLL_FREE: "toll_free",
}


@dataclass(frozen=True)
class PhoneParts:
    """A phone number split into the parts we store: the number, its country and
    its line type. `e164` is `""` for anything that cannot be read as a valid
    number, and `country`/`type` are then `None`."""

    e164: str
    #: ISO region ("AE") — the readable country, stored beside the number. The
    #: dial code (+971) already lives inside `e164`, so this is for a person, not
    #: for arithmetic.
    country: str | None
    #: "mobile" | "landline" | "toll_free" | "other".
    type: str | None


def describe_phone(raw: str | None) -> PhoneParts:
    """The stored shape of a phone number: E.164, ISO region and line type.

    Reuses `normalise_phone` for the number itself — same validity rule, same
    metadata as the storefront — then reads the region and line type off the same
    parse. One helper for every write path (checkout, register, aggregator ingest,
    custom orders) so a number is stored one way whoever sends it.
    """
    e164 = normalise_phone(raw)
    if not e164:
        return PhoneParts(e164="", country=None, type=None)
    # `normalise_phone` already proved this parses and is valid.
    number = phonenumbers.parse(e164, _DEFAULT_REGION)
    region = phonenumbers.region_code_for_number(number)
    label = _TYPE_LABELS.get(phonenumbers.number_type(number), "other")
    return PhoneParts(e164=e164, country=region, type=label)


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
