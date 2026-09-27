import type { MetadataRoute } from 'next';

import { RSC_API_BASE } from '@/lib/api-server';
import type { BlogPostListResponse, Category, ProductListResponse } from '@/lib/types';

/**
 * Built fresh on every request, from live data.
 *
 * This used to be ISR on `FEED_TTL`, with every fetch in the data cache on the
 * same TTL. That is two layers of stale-while-revalidate, and both of them only
 * move when a request arrives. A crawler's visit was served the previous copy
 * and started a rebuild in the background, and that rebuild then read the
 * product list the data cache had *also* kept from before. So a sitemap
 * fetched a few times a day described the catalogue as it was one or two
 * visits earlier. On 2026-09-27 it was still listing three sold-out cakes
 * (which were 404s at the time) at 18:49, five hours after they sold out,
 * from a copy it had just rebuilt.
 *
 * There is nothing to save by caching here. A crawler asks for this a handful
 * of times a day, and a build is three small API calls.
 *
 * A failure is still a failure: each fetch throws, and the request answers
 * 500. That is safe in a way the old swallowed failure was not. F-WEB-6 was
 * about a partial sitemap being *cached* and served as the truth. A search
 * engine treats a 500 as "try again later" and keeps the last sitemap it read.
 */
export const dynamic = 'force-dynamic';

const BASE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';
const LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? 'en,ar').split(',');

function localeAlternates(path: string) {
  const languages: Record<string, string> = {};
  for (const locale of LOCALES) {
    languages[locale] = `${BASE}/${locale}${path}`;
  }
  return { languages };
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [];

  // Static routes — one entry per locale
  const staticPaths = [
    { path: '',              priority: 1.0, changeFrequency: 'weekly' as const },
    { path: '/about',        priority: 0.7, changeFrequency: 'monthly' as const },
    { path: '/contact',      priority: 0.7, changeFrequency: 'monthly' as const },
    { path: '/faq',          priority: 0.6, changeFrequency: 'monthly' as const },
    { path: '/all-products', priority: 0.8, changeFrequency: 'weekly' as const },
    { path: '/blog',         priority: 0.7, changeFrequency: 'weekly' as const },
    { path: '/privacy',      priority: 0.3, changeFrequency: 'yearly' as const },
  ];

  for (const { path, priority, changeFrequency } of staticPaths) {
    for (const locale of LOCALES) {
      entries.push({
        url: `${BASE}/${locale}${path}`,
        priority,
        changeFrequency,
        alternates: localeAlternates(path),
      });
    }
  }

  // No try/catch, and every fetch below throws on a non-2xx or a timeout. A
  // failure that fell through to `return entries` (static-only), or was
  // swallowed into a partial list, would tell crawlers that every product URL
  // had gone. Throwing turns it into a 500, which they retry (F-WEB-6; see
  // `dynamic` above).
  const res = await fetch(`${RSC_API_BASE}/categories`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`sitemap: /categories returned ${res.status}`);

  const categories: Category[] = await res.json();

  for (const c of categories.filter(c => c.is_active)) {
    for (const locale of LOCALES) {
      entries.push({
        url: `${BASE}/${locale}/${c.slug}`,
        lastModified: c.updated_at,
        priority: 0.9,
        changeFrequency: 'daily',
        alternates: localeAlternates(`/${c.slug}`),
      });
    }
  }

  // Product pages — every product with a page, sold out or not. A cake that
  // sold out tonight is a live page marked out of stock, not a 404, so it
  // stays in the sitemap. That also keeps the sitemap from changing every
  // evening and morning as the kitchens sell out and restock.
  let page = 1;
  let hasMore = true;
  while (hasMore) {
    const prodRes = await fetch(
      `${RSC_API_BASE}/products?per_page=100&page=${page}&is_active=true&include_unavailable=true`,
      { cache: 'no-store', signal: AbortSignal.timeout(5000) },
    );
    if (!prodRes.ok) {
      throw new Error(`sitemap: /products page ${page} returned ${prodRes.status}`);
    }

    const data: ProductListResponse = await prodRes.json();
    for (const p of data.items) {
      if (!p.category) continue;
      const productPath = `/${p.category.slug}/${p.slug}`;
      for (const locale of LOCALES) {
        entries.push({
          url: `${BASE}/${locale}${productPath}`,
          lastModified: p.updated_at,
          priority: 0.8,
          changeFrequency: 'weekly',
          alternates: localeAlternates(productPath),
        });
      }
    }

    hasMore = page < data.pages;
    page++;
  }
  // Blog posts
  let blogPage = 1;
  let blogHasMore = true;
  while (blogHasMore) {
    const blogRes = await fetch(
      `${RSC_API_BASE}/blog/public?locale=en&per_page=50&page=${blogPage}`,
      { cache: 'no-store', signal: AbortSignal.timeout(5000) },
    );
    if (!blogRes.ok) {
      throw new Error(`sitemap: /blog page ${blogPage} returned ${blogRes.status}`);
    }

    const blogData: BlogPostListResponse = await blogRes.json();
    for (const post of blogData.items) {
      const postPath = `/blog/${post.slug}`;
      for (const locale of LOCALES) {
        entries.push({
          url: `${BASE}/${locale}${postPath}`,
          lastModified: post.updated_at,
          priority: 0.7,
          changeFrequency: 'weekly',
          alternates: localeAlternates(postPath),
        });
      }
    }

    blogHasMore = blogPage < blogData.pages;
    blogPage++;
  }

  return entries;
}
