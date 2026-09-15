"""Create one or more MM products on every integrator they belong on, and map them.

The catalog-sync service already knows how to create a single product on a single
target (`catalog_sync.create_menu_item`) and how to record the name→product
mapping from a menu read (`resolve_and_approve_mappings`). What it has never had
is the one operator action that ties them together for a *newly added* integrator
product: "this product is now in the integrator menu — put it on every outlet it
should be on, then map the item codes back."

This is that action. Given a product (by SKU or id) it:

1. **Foodics** — one account-level create (`target=foodics`, `branch_id=None`).
   Foodics is the master for the integrated branches (Barsha Heights, Sharjah
   Kitchen): the create places the product in its Grubtech subgroup at price
   parity and Foodics propagates it to every marketplace outlet of those two
   branches. The Foodics id is mapped inline.
2. **Portals** — for each *non-Foodics* branch (Al Karama, Dubai Silicon Oasis),
   a direct create on each channel that (a) the branch actually trades on and
   (b) has a server-callable create (careem / noon / talabat). Created off-shelf.
3. **Worker-only channels** — keeta and deliveroo have no server-callable menu
   API (H5guard / separate login), so their create must run through the headed
   worker. These are reported as an explicit hand-off, never silently skipped.
4. **--map** — after the creates, refresh each target's live menu into a snapshot
   and run `resolve_and_approve_mappings`, so the name→product rows for the
   propagated/created items land in `external_item_map` without waiting for the
   next scheduled sweep (which is off in prod: `CATALOG_SYNC_SWEEP_MINUTES=0`).

Default is a DRY RUN: it resolves everything and prints the exact create each
target would POST, mutating nothing. Pass `--apply` to actually create, and
`--map` to run the mapping phase after. Per-target isolated: one dead session or
missing category is reported as an error row and never stops the rest.

Gated exactly like the admin surface: creates need `CATALOG_SYNC_ENABLED`, the
`--map` reads need `CATALOG_SYNC_READ_ENABLED`. Runs on the live api slot.

Usage (on the VM; derive the live slot — api or api-green — from `docker ps`):
    docker compose exec <api-slot> python -m scripts.sync_product_to_integrators \
        --sku FG0133 --sku FG0134            # dry run, all applicable targets
    docker compose exec <api-slot> python -m scripts.sync_product_to_integrators \
        --sku FG0133 --sku FG0134 --apply --map
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.branch import Branch
from app.models.catalog_sync import SNAPSHOT_MENU, TARGET_FOODICS
from app.models.product import Product
from app.services.aggregators import catalog_sync
from app.services.aggregators.catalog_sync import (
    _CREATE_NEEDS_WORKER,
    _DIRECT_CREATE_CHANNELS,
)


async def _resolve_products(
    db: Any, *, skus: list[str], product_ids: list[str]
) -> list[Product]:
    out: list[Product] = []
    for sku in skus:
        p = (
            await db.execute(select(Product).where(Product.sku == sku))
        ).scalar_one_or_none()
        if p is None:
            print(f"  ! no product with SKU {sku!r}")
        else:
            out.append(p)
    for pid in product_ids:
        p = (
            await db.execute(select(Product).where(Product.id == pid))
        ).scalar_one_or_none()
        if p is None:
            print(f"  ! no product with id {pid!r}")
        else:
            out.append(p)
    return out


async def _plan_matrix(
    db: Any,
) -> tuple[list[tuple[str, Any]], list[tuple[str, Any]], dict[Any, str]]:
    """Return (server_creates, worker_handoffs, branch_names).

    Foodics is one account-level create (branch_id None). Non-Foodics branches get
    a per-channel create for each channel they trade on; server-callable channels
    go in the first list, worker-only channels in the second. `branch_names` maps
    branch id → name (captured now, so nothing lazy-loads after a rollback).
    """
    branches = (
        (
            await db.execute(
                select(Branch)
                .where(Branch.is_active.is_(True))
                .options(
                    selectinload(Branch.aggregator_maps),
                    selectinload(Branch.foodics_map),
                )
            )
        )
        .scalars()
        .all()
    )
    # Account-level creates (one call covers every outlet they serve): Foodics is
    # the master for the two integrated branches; noon is one partner restaurant
    # whose MM-managed menu serves both non-Foodics outlets (Karama + DSO) at once.
    server: list[tuple[str, Any]] = [(TARGET_FOODICS, None), ("noon", None)]
    # careem / talabat are genuinely per-outlet (each non-Foodics branch is its own
    # catalog/vendor), so they stay per-branch.
    per_branch_direct = tuple(c for c in _DIRECT_CREATE_CHANNELS if c != "noon")
    worker: list[tuple[str, Any]] = []
    names: dict[Any, str] = {None: "account-level"}
    for b in branches:
        names[b.id] = b.name
        if b.has_foodics:
            continue  # covered by the Foodics master create
        for channel in b.aggregators:  # active (channel, branch) rows only
            if channel in per_branch_direct:
                server.append((channel, b.id))
            elif channel in _CREATE_NEEDS_WORKER:
                worker.append((channel, b.id))
    return server, worker, names


async def run(args: argparse.Namespace) -> None:
    async with AsyncSessionFactory() as db:
        products = await _resolve_products(
            db, skus=args.sku, product_ids=args.product_id
        )
        if not products:
            print("No products resolved — nothing to do.")
            return
        # Snapshot to plain values up front: create_menu_item takes plain ids and
        # re-queries its own Product, and a rollback on an error would otherwise
        # expire these ORM instances and trigger a sync lazy-load (MissingGreenlet).
        prods = [(p.id, p.sku, p.name, p.base_price) for p in products]
        server, worker, names = await _plan_matrix(db)
        if args.targets:
            wanted = set(args.targets)
            server = [t for t in server if t[0] in wanted]
            worker = [t for t in worker if t[0] in wanted]

        # --enrich is its own operation (patch existing items); it must NOT run the
        # create loop, or an --apply would create a second copy of an item that
        # already exists on foodics/noon/careem.
        create_phase = not args.enrich
        if create_phase:
            print(f"\n{'APPLY' if args.apply else 'DRY RUN'} — creates per product\n")
        for pid, sku, pname, price in prods if create_phase else []:
            print(f"== {sku}  {pname}  (AED {price}) ==")
            for target, branch_id in server:
                where = names.get(branch_id, str(branch_id))
                try:
                    plan = await catalog_sync.create_menu_item(
                        db,
                        product_id=pid,
                        target=target,
                        branch_id=branch_id,
                        dry_run=not args.apply,
                    )
                    if args.apply:
                        await db.commit()
                    note = plan.get("note", "")
                    ident = (
                        plan.get("foodics_id")
                        or plan.get("careem_id")
                        or plan.get("noon_item_code")
                        or plan.get("command_id")
                        or ""
                    )
                    print(f"  [{target:9}/{where:20}] ok {ident} {note}")
                except Exception as exc:  # noqa: BLE001 — isolate per target
                    await db.rollback()
                    print(f"  [{target:9}/{where:20}] ERROR {exc}")
            for target, branch_id in worker:
                where = names.get(branch_id, str(branch_id))
                print(
                    f"  [{target:9}/{where:20}] HANDOFF — worker-only "
                    f"(no server-callable menu API); create on the portal/worker."
                )
            print()

        if args.enrich:
            print("Enrich phase — full bilingual name/desc + image on existing items\n")
            # Targets whose items already exist and take enrichment in place.
            enrich_targets = ("foodics", "careem", "noon")
            if args.targets:
                enrich_targets = tuple(
                    t for t in enrich_targets if t in set(args.targets)
                )
            for pid, sku, pname, _ in prods:
                print(f"== {sku}  {pname} ==")
                for target in enrich_targets:
                    try:
                        rep = await catalog_sync.enrich_existing_item(
                            db, product_id=pid, target=target, dry_run=not args.apply
                        )
                        if args.apply:
                            await db.commit()
                        print(
                            f"  [{target:9}] {rep.get('fields') or rep.get('would_set')} {rep.get('note', '')}"
                        )
                    except Exception as exc:  # noqa: BLE001 — isolate per target
                        await db.rollback()
                        print(f"  [{target:9}] ERROR {exc}")
                print()

        if args.map:
            print("Mapping phase — refresh menu snapshots + resolve\n")
            # Only targets we can create + read server-side. keeta/deliveroo menus
            # come from the headed worker, so a server read would just error.
            targets = sorted({t for t, _ in server})
            for target in targets:
                # Foodics reads account-level; portals read per outlet — use any
                # active branch that trades on the channel as the read scope.
                branch_id = None
                if target != TARGET_FOODICS:
                    branch_id = next(
                        (bid for t, bid in server + worker if t == target and bid),
                        None,
                    )
                try:
                    await catalog_sync.refresh_target(
                        db, target=target, branch_id=branch_id, kind=SNAPSHOT_MENU
                    )
                    await db.commit()
                    rep = await catalog_sync.resolve_and_approve_mappings(
                        db, target=target
                    )
                    await db.commit()
                    print(
                        f"  [{target:9}] mapped: "
                        f"{rep.get('products_matched')} products, "
                        f"{rep.get('approved')} approved"
                    )
                except Exception as exc:  # noqa: BLE001 — isolate per target
                    await db.rollback()
                    print(f"  [{target:9}] map ERROR {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sku", action="append", default=[], help="MM SKU (repeatable)")
    ap.add_argument(
        "--product-id", action="append", default=[], help="MM product id (repeatable)"
    )
    ap.add_argument(
        "--targets",
        nargs="*",
        default=None,
        help="Restrict to these targets (default: all applicable).",
    )
    ap.add_argument(
        "--apply", action="store_true", help="Actually create (default: dry run)."
    )
    ap.add_argument(
        "--map",
        action="store_true",
        help="After creating, refresh menu snapshots and resolve mappings.",
    )
    ap.add_argument(
        "--enrich",
        action="store_true",
        help="Enrich already-created items (foodics/careem/noon) with the full "
        "bilingual name/description + image. Dry-run unless --apply.",
    )
    args = ap.parse_args()
    if args.apply and not settings.CATALOG_SYNC_ENABLED:
        raise SystemExit("CATALOG_SYNC_ENABLED is off — refusing to --apply.")
    if args.map and not settings.CATALOG_SYNC_READ_ENABLED:
        raise SystemExit("CATALOG_SYNC_READ_ENABLED is off — refusing to --map.")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
