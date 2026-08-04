"""
Register a branch with noon Send, once, and write the code it returns onto it.

noon Send will only collect from a pickup point it knows about. Our kitchens are
not noon marketplace outlets, so each one has to be created through the
pickup-points API rather than looked up in `/public/v1/outlets`. The `code` that
comes back is what `branches.noon_send_outlet_code` holds; without it that
branch cannot dispatch through noon Send and its orders fall back to Lalamove.

Per branch, deliberately. When Barsha Heights starts delivering it is registered
separately, gets its own code, and both kitchens dispatch from their own — which
is the whole reason the code lives on the branch rather than in the environment.

This is a script and not something the application does on start-up: creating a
pickup point is a non-idempotent write against a partner account, and doing it
automatically would quietly accumulate duplicates every time a container
restarted.

Run from apps/api, with `NOON_SEND_API_KEY` and `NOON_SEND_ENV` in the
environment:

    python -m scripts.register_noon_send_pickup                    # report only
    python -m scripts.register_noon_send_pickup --branch K001      # report one
    python -m scripts.register_noon_send_pickup --branch K001 --create

Coordinates, address and phone all come from the branch row, so the pickup point
and the delivery-zone geometry cannot drift apart.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.branch import Branch
from app.services.lalamove_service import normalise_phone
from app.services.providers.noon_send_provider import NoonSendError, provider


async def _branches(db, reference: str | None) -> list[Branch]:
    stmt = select(Branch).where(
        Branch.is_active.is_(True),
        Branch.deleted_at.is_(None),
        Branch.latitude.isnot(None),
        Branch.longitude.isnot(None),
    )
    if reference:
        stmt = stmt.where(Branch.reference == reference)
    result = await db.execute(stmt.order_by(Branch.display_order, Branch.name))
    return list(result.scalars().all())


async def main(reference: str | None, create: bool) -> int:
    if not settings.NOON_SEND_API_KEY:
        print("NOON_SEND_API_KEY is not set — nothing to talk to.", file=sys.stderr)
        return 1

    print(f"environment : {settings.NOON_SEND_ENV} ({provider.config.host})")

    async with AsyncSessionFactory() as db:
        branches = await _branches(db, reference)
        if not branches:
            print(
                "No branch matched. It has to be active, undeleted and have "
                "coordinates.",
                file=sys.stderr,
            )
            return 1

        if create and len(branches) > 1:
            print(
                "Refusing to register several branches at once — pass --branch "
                "<reference> and do them one at a time.",
                file=sys.stderr,
            )
            return 1

        for branch in branches:
            print(f"\n{branch.reference} — {branch.name}")
            print(f"  pin        : {branch.latitude}, {branch.longitude}")
            print(f"  outlet code: {branch.noon_send_outlet_code or '(none)'}")

            try:
                serviceable = await provider.check_serviceability(
                    float(branch.latitude), float(branch.longitude)
                )
            except NoonSendError as exc:
                print(f"  serviceable: could not check ({exc})", file=sys.stderr)
                continue
            print(f"  serviceable: {serviceable}")

            if not create:
                continue

            if branch.noon_send_outlet_code:
                print(
                    f"  already registered as {branch.noon_send_outlet_code}. Clear "
                    "the field in the admin first if you really mean to re-register.",
                    file=sys.stderr,
                )
                return 1
            if not serviceable:
                print(
                    "  refusing to register a pickup point noon Send says it "
                    "cannot reach.",
                    file=sys.stderr,
                )
                return 1

            phone = normalise_phone(branch.phone or "")
            if not phone:
                print(
                    "  the branch needs a phone number — it is what the rider "
                    "calls on arrival.",
                    file=sys.stderr,
                )
                return 1

            try:
                created = await provider.create_pickup_point(
                    name=branch.name,
                    latitude=float(branch.latitude),
                    longitude=float(branch.longitude),
                    address_line=branch.address or branch.name,
                    contact_phone_number=phone,
                    # Our own reference, so a point can be traced back to a
                    # branch from their side of the integration.
                    external_code=branch.reference,
                    pickup_instructions=(
                        "Collect the boxed cake from the counter. Keep upright."
                    ),
                )
            except NoonSendError as exc:
                print(f"  noon Send refused: {exc}", file=sys.stderr)
                return 1

            code = created.get("code")
            if not code:
                print("  noon Send returned no code.", file=sys.stderr)
                return 1

            branch.noon_send_outlet_code = code
            await db.commit()
            print(
                f"  registered : {code} (serviceable={created.get('is_serviceable')})"
            )
            print(f"  saved onto branch {branch.reference}.")
            return 0

    if not create:
        print("\nPass --branch <reference> --create to register one.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", help="branch reference, e.g. K001")
    parser.add_argument(
        "--create",
        action="store_true",
        help="actually register the branch (otherwise this only reports)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.branch, args.create)))
