"""
One address, written the same way everywhere.

An order's `shipping_address_snapshot` is a dump of `AddressCreate`, and four
different places were turning it back into something a person reads: the order
emails, the register's ticket, Lalamove's stop and noon Send's task. Each did it
slightly differently, and the differences were not deliberate — the emails
stacked the lines and dropped `unit_number` entirely, which is the one field a
rider needs and a map pin can never supply.

So it is one function. A customer, a cashier and a driver looking at the same
order see the same address.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable

__all__ = ["one_line", "recipient_name", "delivery_contact"]

#: The order the parts are read in.
#:
#: `unit_number` leads on purpose. A formatted Google address gets somebody to
#: the building; the flat, floor or office number is the part that finishes the
#: delivery, and a driver reading a long line wants it before the landmark
#: rather than after it.
#:
#: `city` is in the list but is rarely present: `AddressCreate` has no such
#: field, so a snapshot written by the current checkout has none. It stays
#: because older snapshots carry one, and dropping it would silently shorten
#: those addresses.
_PARTS = ("unit_number", "address_line_1", "address_line_2", "city")


def one_line(snapshot: dict[str, Any] | None) -> str | None:
    """
    The whole address on one line, or None when there is nothing to show.

    None rather than an empty string, because every caller renders this
    conditionally and `if address` reading false for `""` is the behaviour they
    all already expect.
    """
    if not snapshot:
        return None
    parts = [str(snapshot.get(key) or "").strip() for key in _PARTS]
    return ", ".join(part for part in parts if part) or None


def recipient_name(snapshot: dict[str, Any] | None) -> str | None:
    """
    Who is receiving it, first and last name, however many of those exist.

    Beside `one_line` because it is the other half of the same card and had
    the same problem — three implementations, one of which rendered a lone
    space when both fields were empty.
    """
    if not snapshot:
        return None
    parts = [
        str(snapshot.get(key) or "").strip() for key in ("first_name", "last_name")
    ]
    return " ".join(part for part in parts if part) or None


def delivery_contact(order: Any) -> tuple[str | None, str | None]:
    """The name and number a courier drop-off should carry for this order.

    The gift recipient when one was given — an order placed for someone else is
    handed to *them*, not to the person who paid, so their name and phone are
    what the driver is given (`OrderReceiver`). Otherwise the shipping address's
    own name and phone, as before. Centralised so all three courier builders
    (Slider, Lalamove, noon Send) make the same choice — Slider carries only the
    phone, the other two carry both.

    Reads `order.receiver` only when it is already loaded: a bare relationship
    access on an async ORM order that did not eager-load it is a `MissingGreenlet`,
    not a query. The dispatch path loads it (`courier_service.dispatch` ensures
    it); a quote or estimate path that did not simply falls back to the address,
    which is all a quote needs. A stand-in that is not an ORM instance at all (a
    test's `SimpleNamespace`) has no lazy load to trigger, so it is read directly.
    `recipient_name` and the raw `phone` come from the snapshot exactly as they
    did before this existed.
    """
    snapshot = order.shipping_address_snapshot or {}
    try:
        receiver_loaded = "receiver" not in sa_inspect(order).unloaded
    except NoInspectionAvailable:
        receiver_loaded = True
    receiver = getattr(order, "receiver", None) if receiver_loaded else None
    if receiver is not None:
        return receiver.name, receiver.phone
    return recipient_name(snapshot), (snapshot.get("phone") or None)
