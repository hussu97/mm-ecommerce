"""
Seed script for Melting Moments database.
Run: cd apps/api && python -m scripts.seed_db
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

CATEGORIES = [
    {"name": "Brownies", "slug": "brownies", "description": "Rich, fudgy brownies with premium toppings", "display_order": 1},
    {"name": "Cookies", "slug": "cookies", "description": "Soft-baked cookies with gooey centres", "display_order": 2},
    {"name": "Cookie Melt", "slug": "cookie-melt", "description": "Warm, melty cookie desserts for sharing", "display_order": 3},
    {"name": "Mix Boxes", "slug": "mix-boxes", "description": "Curated boxes with a mix of our bestsellers", "display_order": 4},
    {"name": "Desserts", "slug": "desserts", "description": "Indulgent desserts and sweet treats", "display_order": 5},
]

# Products: (name, slug, description, base_price, is_featured, variants)
# Variants: (name, sku_suffix, price, stock)
PRODUCTS = {
    "brownies": [
        (
            "Pistachio Kunafa Brownies",
            "pistachio-kunafa-brownies",
            "Our signature rich brownie topped with a delightful layer of Pistachio Kunafa. Three flavours don't resist!",
            55.00, True,
            [("3 Pieces", "3PC", 55.00, 50), ("6 Pieces", "6PC", 95.00, 50), ("9 Pieces", "9PC", 135.00, 30)],
        ),
        (
            "Raspberry Brownies",
            "raspberry-brownies",
            "Decadent dark chocolate brownies swirled with fresh raspberry compote.",
            55.00, True,
            [("3 Pieces", "3PC", 55.00, 50), ("6 Pieces", "6PC", 95.00, 50), ("9 Pieces", "9PC", 135.00, 30)],
        ),
        (
            "Lindor Brownies",
            "lindor-brownies",
            "Fudgy brownies with Lindor chocolate truffles melted in for an extra creamy bite.",
            55.00, False,
            [("3 Pieces", "3PC", 55.00, 50), ("6 Pieces", "6PC", 95.00, 50), ("9 Pieces", "9PC", 135.00, 30)],
        ),
        (
            "Ferrero Brownies",
            "ferrero-brownies",
            "Chocolate brownies packed with whole Ferrero Rocher and topped with a hazelnut glaze.",
            55.00, False,
            [("3 Pieces", "3PC", 55.00, 50), ("6 Pieces", "6PC", 95.00, 50), ("9 Pieces", "9PC", 135.00, 30)],
        ),
        (
            "Snickers Brownies",
            "snickers-brownies",
            "Salted caramel, peanut, and milk chocolate layered into our classic fudgy base.",
            55.00, False,
            [("3 Pieces", "3PC", 55.00, 50), ("6 Pieces", "6PC", 95.00, 50), ("9 Pieces", "9PC", 135.00, 30)],
        ),
        (
            "Cheesecake Brownies",
            "cheesecake-brownies",
            "A marble of cream cheese cheesecake swirled through rich chocolate brownie batter.",
            55.00, False,
            [("3 Pieces", "3PC", 55.00, 50), ("6 Pieces", "6PC", 95.00, 50), ("9 Pieces", "9PC", 135.00, 30)],
        ),
    ],
    "cookies": [
        (
            "Chocolate Chip Cookies",
            "chocolate-chip-cookies",
            "Classic soft-baked cookies loaded with semi-sweet chocolate chips.",
            40.00, True,
            [("3 Pieces", "3PC", 40.00, 60), ("6 Pieces", "6PC", 70.00, 60), ("9 Pieces", "9PC", 100.00, 40)],
        ),
        (
            "Nutella Stuffed Cookies",
            "nutella-stuffed-cookies",
            "Giant cookies with a molten Nutella centre that oozes with every bite.",
            45.00, True,
            [("3 Pieces", "3PC", 45.00, 60), ("6 Pieces", "6PC", 80.00, 60), ("9 Pieces", "9PC", 115.00, 40)],
        ),
        (
            "Oreo Cookies",
            "oreo-cookies",
            "Soft cookies packed with crushed Oreos and white chocolate chips.",
            45.00, False,
            [("3 Pieces", "3PC", 45.00, 60), ("6 Pieces", "6PC", 80.00, 60), ("9 Pieces", "9PC", 115.00, 40)],
        ),
        (
            "Red Velvet Cookies",
            "red-velvet-cookies",
            "Vibrant red velvet cookies with a cream cheese glaze drizzle.",
            45.00, False,
            [("3 Pieces", "3PC", 45.00, 60), ("6 Pieces", "6PC", 80.00, 60), ("9 Pieces", "9PC", 115.00, 40)],
        ),
        (
            "Pistachio Cookies",
            "pistachio-cookies",
            "Buttery cookies with a pistachio cream filling and crushed pistachio topping.",
            50.00, False,
            [("3 Pieces", "3PC", 50.00, 60), ("6 Pieces", "6PC", 85.00, 60), ("9 Pieces", "9PC", 120.00, 40)],
        ),
    ],
    "cookie-melt": [
        (
            "Lotus Biscoff Cookie Melt",
            "lotus-biscoff-cookie-melt",
            "A warm, gooey cookie melt with Lotus Biscoff spread swirled through. Best served warm with ice cream.",
            45.00, True,
            [("Serves 1-2", "S12", 45.00, 30), ("Serves 3-5", "S35", 85.00, 20)],
        ),
        (
            "Chocolate Fudge Cookie Melt",
            "chocolate-fudge-cookie-melt",
            "Ultra-rich chocolate cookie melt with a molten fudge centre.",
            45.00, False,
            [("Serves 1-2", "S12", 45.00, 30), ("Serves 3-5", "S35", 85.00, 20)],
        ),
        (
            "Pistachio Cookie Melt",
            "pistachio-cookie-melt",
            "Delicate pistachio-flavoured cookie melt topped with crushed pistachios and a rose water drizzle.",
            50.00, False,
            [("Serves 1-2", "S12", 50.00, 30), ("Serves 3-5", "S35", 90.00, 20)],
        ),
    ],
    "mix-boxes": [
        (
            "Brownie Mix Box",
            "brownie-mix-box",
            "A selection of our most popular brownie flavours, perfect as a gift.",
            95.00, True,
            [("6 Pieces", "6PC", 95.00, 20), ("12 Pieces", "12PC", 165.00, 15)],
        ),
        (
            "Cookie Mix Box",
            "cookie-mix-box",
            "A curated mix of our bestselling cookie flavours in one beautiful box.",
            75.00, False,
            [("6 Pieces", "6PC", 75.00, 20), ("12 Pieces", "12PC", 130.00, 15)],
        ),
        (
            "Everything Box",
            "everything-box",
            "The ultimate Melting Moments experience — brownies, cookies, and cookie melts together.",
            120.00, True,
            [("9 Pieces", "9PC", 120.00, 15), ("18 Pieces", "18PC", 220.00, 10)],
        ),
    ],
    "desserts": [
        (
            "Chocolate Lava Cake",
            "chocolate-lava-cake",
            "Warm chocolate cake with a molten centre. Served with a dusting of icing sugar.",
            45.00, True,
            [("Single", "SGL", 45.00, 25), ("Double", "DBL", 80.00, 20)],
        ),
        (
            "Cheesecake Jar",
            "cheesecake-jar",
            "Creamy New York-style cheesecake layered with biscuit crumble in a cute glass jar.",
            35.00, False,
            [("Regular (200ml)", "REG", 35.00, 30), ("Large (350ml)", "LRG", 55.00, 20)],
        ),
        (
            "Tiramisu Cup",
            "tiramisu-cup",
            "Classic Italian tiramisu with espresso-soaked ladyfingers and mascarpone cream.",
            35.00, False,
            [("Regular (200ml)", "REG", 35.00, 30), ("Large (350ml)", "LRG", 60.00, 20)],
        ),
    ],
}

PROMO_CODES = [
    {"code": "MM10", "discount_type": "percentage", "discount_value": 10.00, "is_active": True},
    {"code": "FREESHIP", "discount_type": "fixed", "discount_value": 35.00, "min_order_amount": 100.00, "is_active": True},
]


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

async def seed(session: AsyncSession) -> None:
    import bcrypt
    from app.models import (
        Base, User, Category, Product, ProductVariant, PromoCode,
        DiscountTypeEnum,
    )

    def _hash(pw: str) -> str:
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

    print("🌱 Seeding database...")

    # Admin user
    result = await session.execute(select(User).where(User.email == "admin@meltingmomentscakes.com"))
    if not result.scalar_one_or_none():
        admin = User(
            id=uuid.uuid4(),
            email="admin@meltingmomentscakes.com",
            hashed_password=_hash("admin123!"),
            first_name="Admin",
            last_name="MM",
            is_active=True,
            is_admin=True,
            is_guest=False,
        )
        session.add(admin)
        print("  ✅ Admin user created")
    else:
        print("  ⏭  Admin user already exists")

    # Categories
    category_map: dict[str, uuid.UUID] = {}
    for cat_data in CATEGORIES:
        result = await session.execute(select(Category).where(Category.slug == cat_data["slug"]))
        existing = result.scalar_one_or_none()
        if not existing:
            cat_id = uuid.uuid4()
            cat = Category(id=cat_id, **cat_data, is_active=True)
            session.add(cat)
            category_map[cat_data["slug"]] = cat_id
            print(f"  ✅ Category: {cat_data['name']}")
        else:
            category_map[cat_data["slug"]] = existing.id
            print(f"  ⏭  Category already exists: {cat_data['name']}")

    await session.flush()  # ensure category IDs are available

    # Products + Variants
    for cat_slug, products in PRODUCTS.items():
        cat_id = category_map.get(cat_slug)
        for i, (name, slug, description, base_price, is_featured, variants) in enumerate(products):
            result = await session.execute(select(Product).where(Product.slug == slug))
            if result.scalar_one_or_none():
                print(f"  ⏭  Product already exists: {name}")
                continue

            product_id = uuid.uuid4()
            product = Product(
                id=product_id,
                category_id=cat_id,
                name=name,
                slug=slug,
                description=description,
                base_price=base_price,
                image_urls=[],
                is_active=True,
                is_featured=is_featured,
                display_order=i,
            )
            session.add(product)

            for j, (v_name, v_sku_suffix, v_price, v_stock) in enumerate(variants):
                sku = f"{slug.upper().replace('-', '')[:12]}-{v_sku_suffix}"
                variant = ProductVariant(
                    id=uuid.uuid4(),
                    product_id=product_id,
                    name=v_name,
                    sku=sku,
                    price=v_price,
                    stock_quantity=v_stock,
                    is_active=True,
                    display_order=j,
                )
                session.add(variant)

            print(f"  ✅ Product: {name} ({len(variants)} variants)")

    await session.flush()

    # Promo codes
    for promo_data in PROMO_CODES:
        result = await session.execute(select(PromoCode).where(PromoCode.code == promo_data["code"]))
        if result.scalar_one_or_none():
            print(f"  ⏭  Promo code already exists: {promo_data['code']}")
            continue

        promo = PromoCode(
            id=uuid.uuid4(),
            code=promo_data["code"],
            discount_type=DiscountTypeEnum(promo_data["discount_type"]),
            discount_value=promo_data["discount_value"],
            min_order_amount=promo_data.get("min_order_amount"),
            is_active=promo_data["is_active"],
        )
        session.add(promo)
        print(f"  ✅ Promo code: {promo_data['code']}")

    await session.commit()
    print("\n✨ Seed complete!")


async def main() -> None:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://mm_user:mm_password@localhost:5432/mm_ecommerce",
    )
    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
