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

from app.models.product import POS_CHANNEL, WEB_CHANNEL  # noqa: E402
from app.models import (  # noqa: E402
    Branch,
    Combo,
    ComboSize,
    Discount,
    MenuGroup,
    MenuGroupProduct,
    InventoryItem,
    BusinessSettings,
    Category,
    Charge,
    Modifier,
    ModifierOption,
    PaymentMethod,
    Product,
    Promotion,
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

#: What a floor cashier can do without a supervisor standing next to them:
#: ring an order up, take the money, print, and send to the kitchen.
#:
#: Everything that moves money the other way — voids, returns, open discounts,
#: closing the till — is deliberately absent. Foodics draws the same line, and
#: it is the whole reason roles exist rather than one permission set for all.
CASHIER_STAFF_PERMISSIONS = [
    "pos.register.access",
    "pos.payment.perform",
    "pos.print.check",
    "pos.print.receipt",
    "pos.kitchen.send_before_payment",
    "pos.orders.join",
    "pos.orders.ahead",
    "pos.discounts.predefined",
    "pos.products.availability",
    "pos.orders.tags",
    "menu.read",
    "orders.read",
    "customers.read",
]

#: A shift lead. Runs the register end to end, including the drawer and the
#: end-of-day, but stays out of the back office (menu pricing, staff, taxes).
CASHIER_PERMISSION_PREFIXES = ("pos.", "dashboard.general", "reports.sales")
CASHIER_EXTRA_PERMISSIONS = [
    "menu.read",
    "menu.items.hide",
    "menu.items.out_of_stock",
    "orders.read",
    "orders.manage",
    "orders.tags.manage",
    "customers.read",
    "customers.manage",
    "reports.other",
]

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
    def __init__(
        self,
        db,
        export: dict,
        images: dict[str, str],
        dry_run: bool,
        web_visible_on_import: bool = False,
    ):
        self.web_visible_on_import = web_visible_on_import
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
        self.roles: dict[str, Role] = {}

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

    async def upsert(
        self, model, key: dict, values: dict, create_only: dict | None = None
    ):
        """
        Insert or update, matched on `key`.

        `create_only` is applied when the row is first created and never
        touched again. That is where anything the shop owns rather than
        Foodics belongs: a slug is a live URL, and whether an item is on sale
        is an operational decision — neither should be restored from an
        export.
        """
        row = await self.one(model, **key)
        created = row is None
        if created:
            row = model(**key, **values, **(create_only or {}))
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
            rate = money(row.get("rate")) / Decimal(100)
            # Match on the rate, not the name. Production already called its
            # 5% VAT "VAT" while Foodics calls it "UAE VAT"; matching by name
            # created a second 5% row, and both landed in the same group —
            # which would have charged every customer 10%.
            existing = (
                (
                    await self.db.execute(
                        select(Tax).where(Tax.rate == rate, Tax.deleted_at.is_(None))
                    )
                )
                .scalars()
                .first()
            )
            tax = await self.upsert(
                Tax,
                {"name": existing.name if existing else row["name"]},
                {
                    # Foodics states the rate as a percentage; we store a fraction.
                    "rate": rate,
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
            # Keyed on reference, not slug: `reference` is the stable Foodics
            # identity and is what our schema enforces as unique. Production
            # derives its slugs from the reference ("cat-brownies") while this
            # importer derives them from the name ("brownies"), so matching on
            # slug tried to insert a second row for every existing category.
            category = await self.upsert(
                Category,
                {"reference": reference},
                {
                    "name": row["name"],
                    "translations": translations(("name", row.get("name_localized"))),
                    "image_url": self.hosted_image(row.get("image")),
                    "display_order": order,
                    "is_active": True,
                },
                create_only={"slug": slugify(row["name"], reference)},
            )
            self.categories[row["id"]] = category

    async def build_menu_tree(self) -> None:
        """
        Mirror the categories into the register's menu tree.

        The terminal builds its menu from groups, not from the category
        taxonomy the website uses, so without this a freshly imported shop has
        a catalogue and an empty till. One group per category is the sensible
        starting shape; nesting it further is a merchandising decision the
        console makes, and re-running here never disturbs it — a group is only
        created when its category has none.
        """
        for category in self.categories.values():
            reference = f"cat-{str(category.id)[:8]}"
            group = await self.upsert(
                MenuGroup,
                {"reference": reference},
                {},
                create_only={
                    "name": category.name,
                    "translations": category.translations or {},
                    "image_url": category.image_url,
                    "display_order": category.display_order,
                    "is_active": True,
                },
            )
            members = {
                row.product_id
                for row in (
                    await self.db.execute(
                        select(MenuGroupProduct).where(
                            MenuGroupProduct.group_id == group.id
                        )
                    )
                )
                .scalars()
                .all()
            }
            for product in self.products.values():
                if product.category_id == category.id and product.id not in members:
                    self.db.add(
                        MenuGroupProduct(
                            group_id=group.id,
                            product_id=product.id,
                            display_order=product.display_order,
                        )
                    )
                    self.record("MenuGroupProduct", created=True)
            await self.db.flush()

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

            # Foodics signals an open-price product by omitting `price`
            # entirely — a priced item carries `price: 0` when it is priced by
            # a required size modifier instead. The distinction matters: a
            # fixed product at 0.00 with no modifier rings up free, whereas an
            # open one prompts the cashier for the amount.
            is_open_price = "price" not in row

            product = await self.upsert(
                Product,
                {"sku": sku},
                {
                    "name": row["name"],
                    "description": row.get("description"),
                    "translations": translations(
                        ("name", row.get("name_localized")),
                        ("description", row.get("description_localized")),
                    ),
                    "base_price": money(row.get("price")),
                    "image_urls": [hosted] if hosted else [],
                    "category_id": category.id if category else None,
                    "tax_group_id": tax_group.id if tax_group else None,
                    "is_stock_product": row.get("is_stock_product", False),
                    "pricing_method": "open" if is_open_price else "fixed",
                    "display_order": order,
                },
                create_only={
                    "slug": slugify(row["name"], sku.lower()),
                    "is_active": row.get("is_active", True),
                    "sales_channels": (
                        [POS_CHANNEL, WEB_CHANNEL]
                        if self.web_visible_on_import
                        else [POS_CHANNEL]
                    ),
                },
            )
            self.products[row["id"]] = product
            await self.link_modifiers(row, product)

    async def link_modifiers(self, row: dict, product: Product) -> None:
        """
        Attach modifier groups, carrying Foodics' own choice rules across.

        Foodics keeps `minimum_options`, `maximum_options` and `free_options`
        on the product↔modifier link, not on the group — the same "Milk" group
        is optional on a brownie and required on a latte. An export taken with
        `?include=modifiers` carries them in each modifier's `pivot`.

        This used to guess them instead, and the guess was wrong in a way
        nobody could see from the console: it set `maximum_options` to *the
        number of options in the group*. "Mix Brownies Box Of 3" therefore
        imported as min 0 / max 9 — pick nothing, or pick all nine — when
        Foodics says 3 and 3. It also forced `unique_options`, which is what
        stopped a cashier putting two Ferrero and one Fudge in the same box.

        The inference is kept only for an export that predates the pivot, and
        `unique_options` is now false by default: repeating an option is the
        norm for a mixed box, and a group that genuinely must not repeat says
        so with max 1.
        """
        priced_by_modifier = money(row.get("price")) == 0 and bool(row["modifier_ids"])

        for order, foodics_id in enumerate(row["modifier_ids"]):
            modifier = self.modifiers.get(foodics_id)
            if modifier is None:
                continue

            pivot = self.modifier_pivot(row, foodics_id)
            if pivot is not None:
                rules = {
                    "minimum_options": int(pivot.get("minimum_options") or 0),
                    "maximum_options": int(pivot.get("maximum_options") or 1),
                    "free_options": int(pivot.get("free_options") or 0),
                }
            else:
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
                rules = {
                    "minimum_options": 1 if required else 0,
                    "maximum_options": 1 if required else max(option_count, 1),
                    "free_options": 0,
                }

            await self.upsert(
                ProductModifier,
                {"product_id": product.id, "modifier_id": modifier.id},
                {
                    **rules,
                    # A single-select group is unique by construction; anything
                    # wider is a quantity choice until Foodics says otherwise.
                    "unique_options": rules["maximum_options"] <= 1,
                    "display_order": order,
                },
            )

    @staticmethod
    def modifier_pivot(row: dict, foodics_id: str) -> dict | None:
        """
        The choice rules Foodics recorded for this product↔modifier pair.

        `None` when the export was taken without `include=modifiers`, which is
        the only case the inference above is for.
        """
        for modifier in row.get("modifiers") or []:
            if modifier.get("id") != foodics_id:
                continue
            pivot = modifier.get("pivot") or {}
            if "maximum_options" in pivot:
                return pivot
            # Some exports flatten the pivot onto the modifier itself.
            if "maximum_options" in modifier:
                return modifier
            return None
        return None

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

        Foodics never discloses a user's PIN, so each cashier created here is
        given a fresh one and the caller prints them. These are placeholders to
        be rotated in the admin console — they are not the staff's real PINs.

        An account that already exists keeps its role and its PIN. Foodics
        leaves `role_name` empty on most users, so honouring the export would
        put everybody on Cashier — including the owner's own admin account,
        which signs in with the same email. Demoting the owner and resetting
        their PIN on a routine catalogue re-import is not a trade worth making
        for a field the export does not actually carry.
        """
        from app.core.security import hash_password

        default_role = self.roles.get("Cashier") or next(iter(self.roles.values()))

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
            role = self.roles.get(row.get("role_name") or "") or default_role
            is_new = await self.one(User, email=email.lower()) is None
            user = await self.upsert(
                User,
                {"email": email.lower()},
                {
                    "display_name": name,
                    "staff_number": row.get("number") or f"S{index + 1:03d}",
                    "phone": row.get("phone"),
                    "is_staff": True,
                    "is_active": row.get("is_active", True),
                },
                create_only={
                    "role_id": role.id,
                    "pin_hash": hash_password(pin),
                },
            )
            # Only report a PIN that was actually set, or the operator would
            # hand a cashier a code that does not work.
            if is_new:
                issued.append((name, pin))
            elif user.role_id is None:
                # Filling an empty role is not a demotion — an account with no
                # role cannot sign in to the register at all.
                user.role_id = role.id
                await self.db.flush()

            for branch in branches:
                if not await self.one(UserBranch, user_id=user.id, branch_id=branch.id):
                    self.db.add(UserBranch(user_id=user.id, branch_id=branch.id))
            await self.db.flush()
        return issued

    # ── Roles ────────────────────────────────────────────────────────────────

    async def import_roles(self) -> None:
        """
        Recreate the Foodics role list with a real permission set each.

        Foodics does not export which permissions a role holds, so the sets are
        derived from the role's name and job. Getting this wrong in the
        permissive direction is what lets any cashier void a paid order, so the
        default here is the narrow set and the console widens it.
        """
        from app.models import ALL_PERMISSIONS

        cashier = [
            permission
            for permission in ALL_PERMISSIONS
            if permission.startswith(CASHIER_PERMISSION_PREFIXES)
        ] + CASHIER_EXTRA_PERMISSIONS

        for row in self.export.get("roles", []):
            name = row["name"]
            is_admin = "admin" in name.lower()
            if is_admin:
                permissions = list(ALL_PERMISSIONS)
            elif name == "Cashier":
                permissions = cashier
            else:
                permissions = list(CASHIER_STAFF_PERMISSIONS)

            role = await self.upsert(
                Role,
                {"name": name},
                {
                    "name_localized": row.get("name_localized"),
                    "translations": translations(("name", row.get("name_localized"))),
                    "is_super_admin": is_admin,
                    "is_active": True,
                },
                # Permissions are set once and then owned by the console. A
                # re-import must not silently restore rights an owner revoked.
                create_only={"permissions": permissions},
            )
            self.roles[name] = role

        if not self.roles:
            self.roles["Cashier"] = await self.upsert(
                Role,
                {"name": "Cashier"},
                {"is_super_admin": False, "is_active": True},
                create_only={"permissions": cashier},
            )

    # ── Marketing ────────────────────────────────────────────────────────────

    async def import_marketing(self) -> None:
        """
        Discounts, promotions and combos.

        Foodics exports little more than the name for these — the rules live in
        screens the export API does not cover — so each is created inactive
        with its numbers zeroed. That is deliberate: a discount that arrives
        switched on with a guessed value would start taking money off real
        orders the moment it lands.
        """
        for row in self.export.get("discounts", []):
            name = row["name"]
            # "Open Discount" is Foodics' name for a cashier-entered amount,
            # which is a qualification rather than a fixed value.
            is_open = "open" in name.lower()
            await self.upsert(
                Discount,
                {"name": name},
                {
                    "name_localized": row.get("name_localized"),
                    "translations": translations(("name", row.get("name_localized"))),
                    "reference": row.get("reference") or slugify(name, "discount"),
                },
                create_only={
                    "qualification": "open" if is_open else "order",
                    "amount": Decimal("0"),
                    "is_percentage": False,
                    "is_taxable": True,
                    "minimum_order_price": Decimal("0"),
                    "minimum_product_price": Decimal("0"),
                    "order_types": [],
                    "is_active": False,
                },
            )

        for row in self.export.get("promotions", []):
            name = row["name"]
            percent = re.search(r"(\d+(?:\.\d+)?)\s*%", name)
            await self.upsert(
                Promotion,
                {"name": name},
                {
                    "name_localized": row.get("name_localized"),
                    "translations": translations(("name", row.get("name_localized"))),
                },
                create_only={
                    "type": "discount",
                    "trigger": "order_total",
                    "trigger_value": Decimal("0"),
                    # The percentage is in the name and nowhere else, so read
                    # it from there rather than leave a promotion that does
                    # nothing when someone switches it on.
                    "reward": "percentage" if percent else "fixed",
                    "reward_value": (
                        Decimal(percent.group(1)) if percent else Decimal("0")
                    ),
                    "order_types": [],
                    "priority": 100,
                    "max_uses_per_order": 1,
                    "is_active": False,
                },
            )

        for order, row in enumerate(self.export.get("combos", [])):
            name = row["name"]
            combo = await self.upsert(
                Combo,
                {"sku": row.get("sku") or slugify(name, f"combo-{order}")},
                {
                    "name": name,
                    "name_localized": row.get("name_localized"),
                    "translations": translations(("name", row.get("name_localized"))),
                },
                # Inactive until someone adds the sizes and the items it
                # contains — none of which the export carries.
                create_only={"display_order": order, "is_active": False},
            )
            if not await self.one(ComboSize, combo_id=combo.id, name="Regular"):
                self.db.add(
                    ComboSize(
                        combo_id=combo.id,
                        name="Regular",
                        price=Decimal("0"),
                        display_order=0,
                    )
                )
                await self.db.flush()

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
        for row in self.export.get("inventory_items", []):
            # Foodics exports the stock item, not its unit or level; those are
            # per-warehouse operational data the shop sets up here. Cost stays
            # untouched on re-import so a revaluation is not undone.
            sku = row.get("sku") or slugify(row["name"], row["id"][:8])
            await self.upsert(
                InventoryItem,
                {"sku": sku},
                {
                    "name": row["name"],
                    "name_localized": row.get("name_localized"),
                    "is_active": True,
                },
                create_only={
                    "storage_unit": "unit",
                    "ingredient_unit": "unit",
                    "storage_to_ingredient_factor": Decimal("1"),
                    "cost": money(row.get("cost")),
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
        await self.import_roles()
        await self.import_taxes()
        await self.import_categories()
        await self.import_modifiers()
        await self.import_products()
        await self.import_branches()
        await self.import_payment_methods()
        await self.import_reference_data()
        await self.build_menu_tree()
        await self.import_marketing()
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
    parser.add_argument(
        "--web-visible",
        action="store_true",
        help="also list newly imported products on the website (default: POS only)",
    )
    args = parser.parse_args()

    if not args.database_url:
        parser.error("set DATABASE_URL or pass --database-url")

    export = json.loads(args.export.read_text())
    images = json.loads(args.images.read_text()) if args.images else {}

    engine = create_async_engine(args.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        importer = Importer(db, export, images, args.dry_run, args.web_visible)
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
