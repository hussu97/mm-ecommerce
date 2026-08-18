"""
Change what a courier promises, from the command line.

The same three numbers the admin's delivery Estimates screen writes —
`unbatched_promise_kind`, `unbatched_promise_minutes`, `unbatched_promise_days`
on `couriers`. This exists for the deploy that introduces them, where the
screen is not up yet and somebody wants the change to land with the release
rather than after it.

**A script and not a migration**, per CLAUDE.md §7: these are commercial
figures, not schema. A migration that set one would also re-set it on any
environment restored from an older dump, silently reverting whatever the shop
had since chosen — which is exactly the failure mode content-in-migrations has.

Run from apps/api against the environment's own `DATABASE_URL`:

    # what everything currently promises
    python -m scripts.set_courier_promise

    # noon Send moves from an hour to ninety minutes (2026-08-18)
    python -m scripts.set_courier_promise --code noon_send --minutes 90

    # a partner covering Al Ain quotes two days rather than next-day
    python -m scripts.set_courier_promise --code third_party --days 2

Nothing already quoted moves. What the shop said out loud is a record, not a
derivation — `order_deliveries` keeps it — so this changes the next promise,
never the last one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.database import AsyncSessionFactory
from app.models.courier import Courier, UnbatchedPromiseEnum


def _describe(courier: Courier) -> str:
    if courier.unbatched_promise_kind == UnbatchedPromiseEnum.MINUTES.value:
        promise = f"{courier.unbatched_promise_minutes or '—'} minutes"
    else:
        days = courier.unbatched_promise_days
        promise = f"{days} day{'s' if days != 1 else ''} after handover"
    batching = "batches" if courier.supports_batching else "one order per booking"
    active = "" if courier.is_active else "  [inactive]"
    return f"  {courier.code:<14} {courier.name:<16} {promise:<24} {batching}{active}"


async def _run(args: argparse.Namespace) -> int:
    async with AsyncSessionFactory() as db:
        if args.code is None:
            couriers = (
                (await db.execute(select(Courier).order_by(Courier.code)))
                .scalars()
                .all()
            )
            print("Couriers and what they promise when there is no batch to wait for:")
            for courier in couriers:
                print(_describe(courier))
            return 0

        courier = (
            await db.execute(select(Courier).where(Courier.code == args.code))
        ).scalar_one_or_none()
        if courier is None:
            print(f"No courier with code '{args.code}'.", file=sys.stderr)
            return 1

        before = _describe(courier)
        if args.kind is not None:
            courier.unbatched_promise_kind = args.kind
        if args.minutes is not None:
            courier.unbatched_promise_minutes = args.minutes
        if args.days is not None:
            courier.unbatched_promise_days = args.days

        # The one combination that makes the resolver fall back to a literal
        # nobody chose, and quote it to a customer as though somebody had.
        if (
            courier.unbatched_promise_kind == UnbatchedPromiseEnum.MINUTES.value
            and not courier.unbatched_promise_minutes
        ):
            print(
                f"{courier.name} promises minutes but has none set. "
                "Pass --minutes, or --kind next_day.",
                file=sys.stderr,
            )
            return 1

        await db.commit()
        print("before:")
        print(before)
        print("after:")
        print(_describe(courier))
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", help="Courier code, e.g. noon_send. Omit to list.")
    parser.add_argument(
        "--kind",
        choices=[member.value for member in UnbatchedPromiseEnum],
        help="Whether this courier promises an hour or a day.",
    )
    parser.add_argument(
        "--minutes", type=int, help="Ready to door, for a courier we dispatch."
    )
    parser.add_argument(
        "--days",
        type=int,
        help="Handover to door, for a courier that collects on its own schedule.",
    )
    args = parser.parse_args()
    if args.minutes is not None and args.minutes < 1:
        parser.error("--minutes must be at least 1")
    if args.days is not None and args.days < 1:
        parser.error("--days must be at least 1")
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
