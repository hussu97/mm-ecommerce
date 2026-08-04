"""
Register the kitchen with noon Send, once, and print the code it hands back.

noon Send will only collect from a pickup point it knows about. The kitchen is
not a noon marketplace outlet, so it has to be created through the pickup-points
API rather than looked up in `/public/v1/outlets`. The `code` that comes back is
what `NOON_SEND_OUTLET_CODE` holds; without it nothing can be dispatched.

This is deliberately a script and not something the application does on start-up.
Creating a pickup point is a write against a partner account, it is not
idempotent, and doing it automatically would quietly accumulate duplicates every
time a container restarted.

Run from apps/api, with the environment already carrying `NOON_SEND_API_KEY`
and `NOON_SEND_ENV`:

    python -m scripts.register_noon_send_pickup            # show what exists
    python -m scripts.register_noon_send_pickup --create   # create it

Reads the kitchen's own coordinates from the branches table, so the pickup point
and the delivery-zone geometry cannot drift apart — they have one source, which
is the same `resolve_pickup` the dispatcher uses.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.services.lalamove_service import resolve_pickup
from app.services.providers.noon_send_provider import NoonSendError, provider

EXTERNAL_CODE = "mm-kitchen"


async def main(create: bool) -> int:
    if not settings.NOON_SEND_API_KEY:
        print("NOON_SEND_API_KEY is not set — nothing to talk to.", file=sys.stderr)
        return 1

    print(f"environment : {settings.NOON_SEND_ENV} ({provider.config.host})")

    async with AsyncSessionFactory() as db:
        pickup = await resolve_pickup(db)

    if pickup is None:
        print(
            "No usable pickup branch: it needs to be active, take online orders, "
            "and have both coordinates and a phone number.",
            file=sys.stderr,
        )
        return 1

    print(f"kitchen     : {pickup.name} at {pickup.latitude}, {pickup.longitude}")

    try:
        serviceable = await provider.check_serviceability(
            pickup.latitude, pickup.longitude
        )
        print(f"serviceable : {serviceable}")
        if not serviceable and create:
            print(
                "Refusing to create a pickup point noon Send says it cannot reach.",
                file=sys.stderr,
            )
            return 1

        existing = await provider.list_pickup_points()
        mine = [p for p in existing if p.get("external_code") == EXTERNAL_CODE]
        for point in mine:
            print(
                f"existing    : {point['code']} — {point['name']} "
                f"(serviceable={point.get('is_serviceable')})"
            )
        if mine and create:
            print(
                "\nA pickup point tagged "
                f"'{EXTERNAL_CODE}' already exists. Reuse the code above, or "
                "update it through /public/v1/pickup-points/{code}/update.",
                file=sys.stderr,
            )
            return 1

        if not create:
            print("\nPass --create to register a new pickup point.")
            return 0

        created = await provider.create_pickup_point(
            name=pickup.name,
            latitude=pickup.latitude,
            longitude=pickup.longitude,
            address_line=pickup.address,
            contact_phone_number=pickup.phone,
            external_code=EXTERNAL_CODE,
            pickup_instructions="Collect the boxed cake from the counter. Keep upright.",
        )
    except NoonSendError as exc:
        print(f"noon Send refused: {exc}", file=sys.stderr)
        return 1

    print(f"\ncreated     : {created.get('code')}")
    print(f"serviceable : {created.get('is_serviceable')}")
    print(f"\nSet NOON_SEND_OUTLET_CODE={created.get('code')}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create",
        action="store_true",
        help="actually create the pickup point (otherwise this only reports)",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().create)))
