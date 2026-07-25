"""
Import a Foodics account export into the Melting Moments database.

Idempotent: every row is matched on its natural business key (SKU, reference,
email) rather than on the Foodics UUID, so re-running updates in place and a
half-finished run is safe to repeat. Nothing is deleted — an item that has
disappeared from Foodics is left alone rather than silently removed from a
catalogue the shop may still be selling from.

    python scripts/foodics/import_catalogue.py export.json \
        --images image_manifest.json [--dry-run]

Pair with sync_images.py, which copies the photos into our own bucket and
writes the manifest this reads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
import unicodedata
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.models import (  # noqa: E402
    Branch,
    BusinessSettings,
    Category,
    Charge,
    Modifier,
    ModifierOption,
    PaymentMethod,
    Product,
    ProductModifier,
    Reason,
    Role,
    Supplier,
    Tag,
    Tax,
    TaxGroup,
    TaxGroupTax,
    User,
    UserBranch,
)

#: Foodics encodes the tender type as an integer. Anything not listed is
#: imported as "other" rather than dropped, so the books still balance.
PAYMENT_TYPE_BY_CODE = {
    1: "cash",
    2: "card",
    3: "online",
    4: "gift_card",
    5: "house_account",
    7: "other",  # prepaid by a delivery aggregator
}

#: Gift cards and house accounts are out of scope for this build, so their
#: tender rows would be unusable buttons on the terminal.
SKIPPED_PAYMENT_TYPES = {"gift_card", "house_account"}

#: Foodics enumerates these as integers too. The observed values in this
#: account are tags 3–4, reasons 1–3 and charges 1; anything else falls back to
#: the safest bucket rather than failing the whole import.
TAG_TYPE_BY_CODE = {
    1: "order",
    2: "customer",
    3: "inventory_item",
    4: "product",
    5: "revenue_center",
}
REASON_TYPE_BY_CODE = {
    1: "void_return",
    2: "drawer_operation",
    3: "quantity_adjustment",
}
CHARGE_TYPE_BY_CODE = {1: "fixed", 2: "percentage", 3: "open"}

#: Foodics stores each branch's TRN inside the free-text receipt header.
TRN_PATTERN = re.compile(r"TRN\s*[-:]?\s*(\d{10,20})")


def slugify(value: str, fallback: str) -> str:
    ascii_form = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_form.lower()).strip("-")
    return slug or fallback


def translations(*pairs: tuple[str, str | None]) -> dict:
    """An `{ar: {...}}` block, omitting fields Foodics left blank."""
    arabic = {key: value for key, value in pairs if value}
    return {"ar": arabic} if arabic else {}


def money(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


class Importer:
    def __init__(self, db, export: dict, images: dict[str, str], dry_run: bool):
        self.db = db
        self.export = export
        self.images = images
        self.dry_run = dry_run
        self.stats: dict[str, list[int]] = {}
        # Foodics UUID -> our row, for wiring foreign keys within one run.
        self.tax_groups: dict[str, TaxGroup] = {}
        self.categories: dict[str, Category] = {}
        self.products: dict[str, Product] = {}
        self.modifiers: dict[str, Modifier] = {}
        self.branches: dict[str, Branch] = {}

    def record(self, entity: str, created: bool) -> None:
        counts = self.stats.setdefault(entity, [0, 0])
        counts[0 if created else 1] += 1

    def hosted_image(self, url: str | None) -> str | None:
        """Our copy of a Foodics photo, or nothing if it was never mirrored."""
        return self.images.get(url) if url else None

    async def one(self, model, **where):
        return (
            await self.db.execute(select(model).filter_by(**where))
        ).scalar_one_or_none()

    async def upsert(self, model, key: dict, values: dict):
        row = await self.one(model, **key)
        created = row is None
        if created:
            row = model(**key, **values)
            self.db.add(row)
        else:
            for field, value in values.items():
                setattr(row, field, value)
        await self.db.flush()
        self.record(model.__name__, created)
        return row

    # ── Taxes ────────────────────────────────────────────────────────────────

    async def import_taxes(self) -> None:
        by_foodics_id: dict[str, Tax] = {}
        for row in self.export.get("taxes", []):
            tax = await self.upsert(
                Tax,
                {"name": row["name"]},
                {
                    # Foodics states the rate as a percentage; we store a fraction.
                    "rate": money(row.get("rate")) / Decimal(100),
                    # UAE menu prices are quoted VAT-inclusive.
                    "type": "inclusive",
                    "is_active": True,
                },
            )
            by_foodics_id[row["id"]] = tax

        for row in self.export.get("tax_groups", []):
            group = await self.upsert(
                TaxGroup,
                {
                    "reference": row.get("reference")
                    or slugify(row["name"], "tax-group")
                },
                {"name": row["name"], "is_active": True, "is_default": True},
            )
            self.tax_groups[row["id"]] = group
            for member in row.get("taxes", []):
                tax = by_foodics_id.get(member["id"])
                if tax and not await self.one(
                    TaxGroupTax, tax_group_id=group.id, tax_id=tax.id
                ):
                    self.db.add(TaxGroupTax(tax_group_id=group.id, tax_id=tax.id))
            await self.db.flush()

    # ── Menu ─────────────────────────────────────────────────────────────────

    async def import_categories(self) -> None:
        for order, row in enumerate(self.export.get("categories", [])):
            reference = row.get("reference") or slugify(row["name"], row["id"][:8])
            category = await self.upsert(
                Category,
                {"slug": slugify(row["name"], reference)},
                {
                    "name": row["name"],
                    "reference": reference,
                    "translations": translations(("name", row.get("name_localized"))),
                    "image_url": self.hosted_image(row.get("image")),
                    "display_order": order,
                    "is_active": True,
                },
            )
            self.categories[row["id"]] = category

    async def import_modifiers(self) -> None:
        for row in self.export.get("modifiers", []):
            reference = row.get("reference") or slugify(row["name"], row["id"][:8])
            modifier = await self.upsert(
                Modifier,
                {"reference": reference},
                {
                    "name": row["name"],
                    "translations": translations(("name", row.get("name_localized"))),
                    "is_active": True,
                },
            )
            self.modifiers[row["id"]] = modifier

            for order, option in enumerate(row.get("options", [])):
                # SKU is the only stable key Foodics gives an option; fall back
                # to the name so an unsku'd option still round-trips.
                sku = (
                    option.get("sku")
                    or f"{reference}-{slugify(option['name'], str(order))}"
                )
                await self.upsert(
                    ModifierOption,
                    {"modifier_id": modifier.id, "sku": sku},
                    {
                        "name": option["name"],
                        "translations": translations(
                            ("name", option.get("name_localized"))
                        ),
                        "price": money(option.get("price")),
                        "is_active": option.get("is_active", True),
                        "display_order": order,
                    },
                )

    async def import_products(self) -> None:
        for order, row in enumerate(self.export.get("products", [])):
            sku = row.get("sku")
            if not sku:
                continue
            category = self.categories.get(row.get("category_id"))
            tax_group = self.tax_groups.get(row.get("tax_group_id"))
            hosted = self.hosted_image(row.get("image"))

            product = await self.upsert(
                Product,
                {"sku": sku},
                {
                    "name": row["name"],
                    "slug": slugify(row["name"], sku.lower()),
                    "description": row.get("description"),
                    "translations": translations(
                        ("name", row.get("name_localized")),
                        ("description", row.get("description_localized")),
                    ),
                    "base_price": money(row.get("price")),
                    "image_urls": [hosted] if hosted else [],
                    "category_id": category.id if category else None,
                    "tax_group_id": tax_group.id if tax_group else None,
                    "is_active": row.get("is_active", True),
                    "is_stock_product": row.get("is_stock_product", False),
                    "pricing_method": "fixed",
                    "display_order": order,
                },
            )
            self.products[row["id"]] = product
            await self.link_modifiers(row, product)

    async def link_modifiers(self, row: dict, product: Product) -> None:
        """
        Attach modifier groups, inferring the choice rules Foodics omits.

        Roughly half the drinks carry no price of their own and are priced
        entirely by a "Size" group. For those the group must be a required
        single-select, or the cashier could ring up a latte for nothing.
        Everything else is treated as an optional extra.
        """
        priced_by_modifier = money(row.get("price")) == 0 and bool(row["modifier_ids"])

        for order, foodics_id in enumerate(row["modifier_ids"]):
            modifier = self.modifiers.get(foodics_id)
            if modifier is None:
                continue
            option_count = len(
                (
                    await self.db.execute(
                        select(ModifierOption).where(
                            ModifierOption.modifier_id == modifier.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            required = priced_by_modifier
            await self.upsert(
                ProductModifier,
                {"product_id": product.id, "modifier_id": modifier.id},
                {
                    "minimum_options": 1 if required else 0,
                    "maximum_options": 1 if required else max(option_count, 1),
                    "free_options": 0,
                    "unique_options": True,
                    "display_order": order,
                },
            )

    # ── Operations ───────────────────────────────────────────────────────────

    async def import_branches(self) -> None:
        default_group = next(iter(self.tax_groups.values()), None)
        for order, row in enumerate(self.export.get("branches", [])):
            header = row.get("receipt_header") or ""
            trn = TRN_PATTERN.search(header)
            branch = await self.upsert(
                Branch,
                {"reference": row["reference"]},
                {
                    "name": row["name"],
                    "name_localized": row.get("name_localized"),
                    "address": row.get("address"),
                    "phone": row.get("phone"),
                    "latitude": row.get("latitude"),
                    "longitude": row.get("longitude"),
                    "opening_from": row.get("opening_from") or "08:00",
                    "opening_to": row.get("opening_to") or "23:00",
                    # Deliberately not carried over. Foodics has no structured
                    # TRN or address field, so operators type both into the
                    # free-text header. We print those from real columns, and
                    # importing the header verbatim printed them a second time.
                    "receipt_header": None,
                    "receipt_footer": row.get("receipt_footer"),
                    # Foodics keeps the TRN only in the receipt header; we need
                    # it as a field to print a compliant tax invoice and QR.
                    "tax_number": trn.group(1) if trn else None,
                    "tax_registration_name": "Melting Moments Cakes LLC",
                    "tax_group_id": default_group.id if default_group else None,
                    "accepts_reservations": bool(row.get("accepts_reservations")),
                    "receives_online_orders": bool(
                        row.get("receives_online_orders", True)
                    ),
                    "is_active": True,
                    "display_order": order,
                },
            )
            self.branches[row["id"]] = branch

    async def import_payment_methods(self) -> None:
        for order, row in enumerate(self.export.get("payment_methods", [])):
            kind = PAYMENT_TYPE_BY_CODE.get(row.get("type"), "other")
            if kind in SKIPPED_PAYMENT_TYPES:
                continue
            code = row.get("code") or slugify(row["name"], f"pm-{order}")
            await self.upsert(
                PaymentMethod,
                {"code": code},
                {
                    "name": row["name"],
                    "name_localized": row.get("name_localized"),
                    "type": kind,
                    "auto_open_drawer": kind == "cash",
                    "allows_tendering": kind == "cash",
                    "is_active": row.get("is_active", True),
                    "display_order": order,
                },
            )

    async def import_staff(self, pin_start: int) -> list[tuple[str, str]]:
        """
        Recreate the Foodics staff list.

        Foodics never discloses a user's PIN, so each imported cashier is given
        a fresh one here and the caller prints them. These are placeholders to
        be rotated in the admin console — they are not the staff's real PINs.
        """
        from app.core.security import hash_password

        role = await self.upsert(
            Role,
            {"name": "Cashier"},
            {"permissions": [], "is_super_admin": False, "is_active": True},
        )
        # Give the role the full register permission set so an imported cashier
        # can actually work a shift; trim it in the console per branch policy.
        from app.models import ALL_PERMISSIONS

        role.permissions = list(ALL_PERMISSIONS)
        await self.db.flush()

        issued: list[tuple[str, str]] = []
        branches = list(self.branches.values())
        for index, row in enumerate(self.export.get("users", [])):
            name = row.get("name") or f"Staff {index + 1}"
            # Most floor staff sign in with a PIN and have no email in Foodics,
            # but email is our user identity. Mint a stable internal address so
            # they still get an account instead of being dropped.
            email = (
                row.get("email")
                or f"{slugify(name, f'staff-{index}')}@staff.meltingmomentscakes.local"
            )
            pin = str(pin_start + index).zfill(4)
            user = await self.upsert(
                User,
                {"email": email.lower()},
                {
                    "display_name": name,
                    "staff_number": row.get("number") or f"S{index + 1:03d}",
                    "phone": row.get("phone"),
                    "is_staff": True,
                    "is_active": row.get("is_active", True),
                    "role_id": role.id,
                    "pin_hash": hash_password(pin),
                },
            )
            issued.append((name, pin))

            for branch in branches:
                if not await self.one(UserBranch, user_id=user.id, branch_id=branch.id):
                    self.db.add(UserBranch(user_id=user.id, branch_id=branch.id))
            await self.db.flush()
        return issued

    async def import_reference_data(self) -> None:
        for row in self.export.get("tags", []):
            await self.upsert(
                Tag,
                {"name": row["name"]},
                {
                    "name_localized": row.get("name_localized"),
                    "type": TAG_TYPE_BY_CODE.get(row.get("type"), "product"),
                    "color": row.get("color"),
                },
            )
        for row in self.export.get("reasons", []):
            await self.upsert(
                Reason,
                {"name": row["name"]},
                {
                    "name_localized": row.get("name_localized"),
                    "type": REASON_TYPE_BY_CODE.get(row.get("type"), "void_return"),
                    "is_active": True,
                },
            )
        for row in self.export.get("charges", []):
            await self.upsert(
                Charge,
                {"reference": row.get("reference") or slugify(row["name"], "charge")},
                {
                    "name": row["name"],
                    "name_localized": row.get("name_localized"),
                    "type": CHARGE_TYPE_BY_CODE.get(row.get("type"), "fixed"),
                    "value": money(row.get("value")),
                    "is_active": row.get("is_active", True),
                },
            )
        for row in self.export.get("suppliers", []):
            await self.upsert(
                Supplier,
                {"name": row["name"]},
                {
                    "contact_name": row.get("contact_name"),
                    "phone": row.get("phone"),
                    "email": row.get("email"),
                    "address": row.get("address"),
                    "is_active": True,
                },
            )

    async def import_business_settings(self) -> None:
        """
        The single settings row the terminal reads to render a receipt.

        Foodics keeps most of this per-brand rather than exposing it on the
        API, so the values come from the branch data we do have plus UAE
        defaults. Without a row the POS cannot print at all.
        """
        existing = (await self.db.execute(select(BusinessSettings))).scalars().first()
        if existing is not None:
            self.record("BusinessSettings", created=False)
            return

        self.db.add(
            BusinessSettings(
                business_name="Melting Moments",
                currency_code="AED",
                currency_symbol="AED",
                decimal_places=2,
                timezone="Asia/Dubai",
                # Left empty on purpose. This header prints on every branch's
                # receipts, so seeding it from one branch would put the Sharjah
                # address on Barsha Heights' tax invoices. The per-branch
                # address, phone and TRN are printed from their own columns.
                receipt_header=None,
                receipt_footer="meltingmomentscakes.com",
                invoice_title="Tax Invoice",
                receipt_show_qr=True,
                kitchen_auto_print_on_send=True,
                default_order_type="pickup",
            )
        )
        await self.db.flush()
        self.record("BusinessSettings", created=True)

    async def run(self, pin_start: int) -> list[tuple[str, str]]:
        await self.import_taxes()
        await self.import_categories()
        await self.import_modifiers()
        await self.import_products()
        await self.import_branches()
        await self.import_payment_methods()
        await self.import_reference_data()
        await self.import_business_settings()
        return await self.import_staff(pin_start)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=pathlib.Path)
    parser.add_argument("--images", type=pathlib.Path, help="image manifest JSON")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--pin-start",
        type=int,
        default=2001,
        help="First placeholder PIN to issue to imported staff",
    )
    parser.add_argument("--dry-run", action="store_true", help="roll back at the end")
    args = parser.parse_args()

    if not args.database_url:
        parser.error("set DATABASE_URL or pass --database-url")

    export = json.loads(args.export.read_text())
    images = json.loads(args.images.read_text()) if args.images else {}

    engine = create_async_engine(args.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        importer = Importer(db, export, images, args.dry_run)
        pins = await importer.run(args.pin_start)

        if args.dry_run:
            await db.rollback()
            print("\n[dry run] rolled back\n")
        else:
            await db.commit()

        width = max(len(name) for name in importer.stats) if importer.stats else 10
        print(f"\n{'entity'.ljust(width)}  created  updated")
        for name, (created, updated) in sorted(importer.stats.items()):
            print(f"{name.ljust(width)}  {created:>7}  {updated:>7}")

        if pins:
            print("\nPlaceholder PINs issued (rotate these in the console):")
            for name, pin in pins:
                print(f"  {name:<20} {pin}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
