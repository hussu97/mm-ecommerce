"""
Seed a demo shop for exercising the POS end to end.

Creates one branch, UAE VAT, payment methods, a cashier with a PIN, a paired-
ready terminal, a receipt printer pointed at localhost, and a small menu with a
modifier group. Intended for a throwaway database — it refuses to run against
anything whose name does not look like a demo.

    DATABASE_URL=postgresql+asyncpg://... python scripts/seed_pos_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    ALL_PERMISSIONS,
    Branch,
    BusinessSettings,
    Category,
    Device,
    Modifier,
    ModifierOption,
    PaymentMethod,
    Printer,
    Product,
    ProductModifier,
    Reason,
    Role,
    Tax,
    TaxGroup,
    TaxGroupTax,
    User,
    UserBranch,
)

PIN = "1234"
PAIRING_CODE = "DEMO2345"


def now() -> datetime:
    return datetime.now(timezone.utc)


async def main() -> None:
    url = os.environ["DATABASE_URL"]
    if "demo" not in url and "test" not in url:
        raise SystemExit(f"refusing to seed a non-demo database: {url}")

    engine = create_async_engine(url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        settings = BusinessSettings(
            business_name="Melting Moments",
            receipt_header="Handcrafted in Dubai",
            receipt_footer="meltingmomentscakes.com",
            invoice_title="Tax Invoice",
            kitchen_auto_print_on_send=True,
        )
        db.add(settings)

        vat = Tax(name="VAT", rate=Decimal("0.0500"), type="inclusive")
        db.add(vat)
        await db.flush()

        group = TaxGroup(name="UAE VAT", reference="uae_vat", is_default=True)
        db.add(group)
        await db.flush()
        db.add(TaxGroupTax(tax_group_id=group.id, tax_id=vat.id))

        branch = Branch(
            name="Barsha Heights",
            reference="B001",
            phone="04 445 1555",
            address="Barsha Heights, Dubai",
            tax_number="100123456700003",
            tax_registration_name="Melting Moments Cakes LLC",
            tax_group_id=group.id,
            opening_from="09:00",
            opening_to="23:00",
            business_day_start="04:00",
            accepts_reservations=True,
        )
        db.add(branch)
        await db.flush()

        for code, name, kind, drawer, tender in [
            ("cash", "Cash", "cash", True, True),
            ("card", "Debit/Credit Card", "card", False, False),
        ]:
            db.add(
                PaymentMethod(
                    name=name,
                    code=code,
                    type=kind,
                    auto_open_drawer=drawer,
                    allows_tendering=tender,
                )
            )

        for name, kind in [
            ("Wrong entry", "void_return"),
            ("Customer changed mind", "void_return"),
            ("Waste", "quantity_adjustment"),
            ("Cash change", "drawer_operation"),
        ]:
            db.add(Reason(name=name, type=kind))

        role = Role(name="Cashier", permissions=ALL_PERMISSIONS, is_super_admin=False)
        db.add(role)
        await db.flush()

        cashier = User(
            email="fatema@meltingmomentscakes.com",
            display_name="Fatema",
            staff_number="C001",
            hashed_password=hash_password("demo-password-not-real"),
            pin_hash=hash_password(PIN),
            role_id=role.id,
            is_staff=True,
            is_active=True,
        )
        db.add(cashier)
        await db.flush()
        db.add(UserBranch(user_id=cashier.id, branch_id=branch.id))

        db.add(
            Device(
                name="Front Counter iPad",
                reference="C01",
                type="cashier",
                branch_id=branch.id,
                status="available",
                pairing_code=PAIRING_CODE,
                pairing_code_expires_at=now().replace(year=now().year + 1),
            )
        )

        # Points at the Mac's loopback so the simulator can reach a local
        # ESC/POS listener standing in for the counter printer.
        db.add(
            Printer(
                name="Front Counter Printer",
                branch_id=branch.id,
                role="receipt",
                connection="lan",
                ip_address="127.0.0.1",
                port=9100,
                characters_per_line=42,
                codepage="cp437",
                supports_arabic=False,
                has_cash_drawer=True,
                is_default=True,
            )
        )

        # A real branch prints the customer's receipt and the kitchen's ticket
        # on two different machines. Both point at the same local listener here
        # so one fake printer can capture both jobs.
        db.add(
            Printer(
                name="Kitchen Printer",
                branch_id=branch.id,
                role="kitchen",
                connection="lan",
                ip_address="127.0.0.1",
                port=9100,
                characters_per_line=42,
                codepage="cp437",
                supports_arabic=False,
                has_cash_drawer=False,
                is_default=True,
            )
        )

        menu = {
            "Cakes": [
                ("Chocolate Matilda Cake Slice", "FG0130", "55.00"),
                ("Chocolate & Whipped Salted Caramel", "FG0131", "35.00"),
                ("Chocolate Mousse", "FG0129", "30.00"),
            ],
            "Hot Coffee": [
                ("Flat White", "FG0085", "14.00"),
                ("Cappuccino", "FG0088", "16.00"),
                ("Spanish Latte", "FG0093", "18.00"),
            ],
            "Brownies": [
                ("Classic Brownie", "FG0201", "22.00"),
                ("Walnut Brownie", "FG0202", "25.00"),
            ],
        }

        milk = Modifier(reference="milk", name="Milk")
        db.add(milk)
        await db.flush()
        for option_name, price, sku in [
            ("Full fat", "0.00", "MOD-FULL"),
            ("Oat milk", "3.00", "MOD-OAT"),
            ("Almond milk", "3.00", "MOD-ALM"),
        ]:
            db.add(
                ModifierOption(
                    modifier_id=milk.id,
                    name=option_name,
                    sku=sku,
                    price=Decimal(price),
                )
            )

        order_index = 0
        for category_name, products in menu.items():
            category = Category(
                name=category_name,
                slug=category_name.lower().replace(" ", "-").replace("&", "and"),
                display_order=order_index,
            )
            db.add(category)
            await db.flush()
            order_index += 1

            for product_name, sku, price in products:
                product = Product(
                    category_id=category.id,
                    name=product_name,
                    slug=sku.lower(),
                    sku=sku,
                    base_price=Decimal(price),
                    tax_group_id=group.id,
                    pricing_method="fixed",
                    is_active=True,
                )
                db.add(product)
                await db.flush()
                if category_name == "Hot Coffee":
                    db.add(
                        ProductModifier(
                            product_id=product.id,
                            modifier_id=milk.id,
                            minimum_options=1,
                            maximum_options=1,
                        )
                    )

        await db.commit()

        print("Seeded demo shop")
        print(f"  branch      {branch.name} ({branch.reference})")
        print(f"  cashier     {cashier.display_name}  PIN {PIN}")
        print(f"  device      C01  pairing code {PAIRING_CODE}")
        print("  printers    receipt + kitchen, both 127.0.0.1:9100")
        print(f"  products    {sum(len(v) for v in menu.values())}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
