"""
Submit every public URL to IndexNow, once, by hand.

Run: cd apps/api && python -m scripts.indexnow_submit_all

The service pings automatically when something changes — a product saved, a post
published, a category renamed. This is for the case that has no event behind it:
a bulk change that already happened, or a first submission after adding the site
to Bing Webmaster Tools. Migration 120 is exactly that. It renamed all eight
category slugs and moved 36 product URLs with them before any of this existed,
so the only thing pointing Bing at the new addresses is a 308 it has not
followed yet.

An operator tool, deliberately, per convention 7. It is not a change that has to
land — it is a thing a human runs when they have a reason to. Nothing about the
site is wrong if it never runs again.

    --dry-run   print the URLs and submit nothing

Reads the same catalogue the sitemap does, so the two cannot disagree about what
is public.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.blog import BlogPost
from app.models.category import Category
from app.models.product import Product
from app.services import indexnow_service

#: Pages that exist regardless of the catalogue. The same list the sitemap
#: carries, minus the ones `robots.ts` disallows — there is no sense asking a
#: crawler to fetch `/cart`.
STATIC_PATHS = ("", "about", "contact", "faq", "blog", "all-products", "privacy")


async def collect(db: AsyncSession) -> list[str]:
    paths: list[str] = []

    for locale in indexnow_service.LOCALES:
        for path in STATIC_PATHS:
            paths.append(f"/{locale}/{path}".rstrip("/"))

    categories = (
        (await db.execute(select(Category).where(Category.is_active.is_(True))))
        .scalars()
        .all()
    )
    by_id = {c.id: c.slug for c in categories}
    for category in categories:
        paths.extend(indexnow_service.category_urls(category.slug))

    products = (
        (await db.execute(select(Product).where(Product.is_active.is_(True))))
        .scalars()
        .all()
    )
    for product in products:
        category_slug = by_id.get(product.category_id)
        # A product whose category is hidden has no reachable URL — the listing
        # 404s and so does the product page under it.
        if category_slug:
            paths.extend(indexnow_service.product_urls(category_slug, product.slug))

    posts = (
        (await db.execute(select(BlogPost).where(BlogPost.is_active.is_(True))))
        .scalars()
        .all()
    )
    for post in posts:
        paths.extend(indexnow_service.blog_urls(post.slug))

    return paths


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, do not submit")
    args = parser.parse_args()

    from app.core.config import settings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            paths = await collect(db)
    finally:
        await engine.dispose()

    print(f"{len(paths)} url(s) against {settings.WEB_URL}")
    if args.dry_run:
        for path in paths:
            print(f"  {path}")
        print("\n(dry run — nothing submitted)")
        return

    ok = await indexnow_service.submit(paths)
    print("✅ accepted" if ok else "❌ not accepted — see the log line above for why")


if __name__ == "__main__":
    asyncio.run(main())
