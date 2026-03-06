"""
Seed script for Melting Moments database.
Run: cd apps/api && python -m scripts.seed_db
"""

from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

CATEGORIES: list = []
PRODUCTS: dict = {}

PROMO_CODES = [
    {
        "code": "MM10",
        "discount_type": "percentage",
        "discount_value": 10.00,
        "is_active": True,
    },
    {
        "code": "FREESHIP",
        "discount_type": "fixed",
        "discount_value": 35.00,
        "min_order_amount": 100.00,
        "is_active": True,
    },
]


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


async def seed(session: AsyncSession) -> None:
    import bcrypt
    from app.models import (
        User,
        PromoCode,
        DiscountTypeEnum,
    )

    def _hash(pw: str) -> str:
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

    print("🌱 Seeding database...")

    # Admin user
    result = await session.execute(
        select(User).where(User.email == "admin@meltingmomentscakes.com")
    )
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

    # Promo codes
    for promo_data in PROMO_CODES:
        result = await session.execute(
            select(PromoCode).where(PromoCode.code == promo_data["code"])
        )
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
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
